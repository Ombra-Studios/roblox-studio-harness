"""Hub central Studio Harness 1.0.

Un singur proces pentru toți developerii: fiecare PC se prezintă cu un token de dispozitiv din care hub-ul reține doar `sha256`,
adminul îl aprobă din panou sau cu `--approve-pending` (sau hub-ul rulează cu înrolare deschisă). Workspace-ul este jocul deschis în
Studio: sesiunile, claims-urile, jurnalul, prezența și harta proiectului sunt grupate pe cheia workspace-ului. Fără echipe, fără
`team.json`, fără credențiale AI/Roblox. Log-urile nu conțin niciodată token-uri sau conținutul sesiunilor.

Numele fișierului rămâne `team_hub.py` pentru continuitatea canalului de actualizare; clasa este `Hub` (`TeamHub` = alias)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
# Fără `urlopen`: orice cerere ieșită a hub-ului (avatar, --approve-pending) trece prin `_OPENER`, care nu urmează redirectări.
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claims import MAX_LENGTH as MAX_CLAIM_PATH, ClaimTable, display, normalize  # noqa: E402
from local_state import DEVICE_TOKEN_PATTERN, device_id_for, ensure_admin_token, restrict_permissions, state_dir  # noqa: E402
from project_map import CREATOR_UNKNOWN, MAX_ID, workspace_meta  # noqa: E402
import updater  # noqa: E402

VERSION = "1.0.0"

# Pe lostcube.pro hub-ul stă la rădăcina domeniului, deci proxy-ul trimite calea neatinsă. Prefixul rămâne acceptat pentru
# instalările care îl pun sub o cale: aceleași rute merg și când proxy-ul nu îl scoate, și când nu există proxy deloc.
BASE_PATH = "/roblox/harness"


def strip_base(path: str) -> str:
    """`/roblox/harness/hub/status` → `/hub/status`; `/roblox/harness` → `/`; căile fără prefix rămân neschimbate."""
    if path == BASE_PATH or path == BASE_PATH + "/":
        return "/"
    if path.startswith(BASE_PATH + "/"):
        return path[len(BASE_PATH):]
    return path


# Stare persistentă (versiunea 2): dispozitive, workspace-uri, sesiuni, claims per workspace, jurnal; salvată la 30 s și la oprire/update.
STATE_FILE = "hub-state.json"
STATE_VERSION = 2
SAVE_INTERVAL = 30
SAVED_EVENTS = 256
MAX_STATE_BYTES = 64 * 1024 * 1024
DRAIN_SECONDS = 5.0
ONLINE_SECONDS = 15
OFFLINE_SECONDS = 60
SESSION_RETENTION = 1800
MAX_EVENTS = 1024
MAX_JOURNAL = 1000
SYNC_JOURNAL = 50
WORKSPACE_JOURNAL = 200
MAX_SYNC_SESSIONS = 64
# Plafoane per dispozitiv, nu doar per cerere: un singur dispozitiv aprobat nu poate umple memoria hub-ului sau `hub-state.json`.
MAX_DEVICE_SESSIONS = 64
MAX_EVENT_BYTES = 64000
MAX_SYNC_JOURNAL = 32
MAX_META_TEXT = 200
MAX_META_CLAIMS = 32
MAX_JOURNAL_PATHS = 32
MAX_PENDING_DEVICES = 64
MAX_WORKSPACES = 512
PENDING_IDLE_SECONDS = 24 * 3600
# Autorizare: după 10 eșecuri într-un minut de la același client, hub-ul refuză cu 429 (cod de admin neghicibil prin forță brută).
RECOMMENDED_ADMIN_CODE = 32
AUTH_FAIL_LIMIT = 10
AUTH_FAIL_WINDOW = 60.0
TOO_MANY_ATTEMPTS = "Prea multe încercări de autorizare; încearcă din nou peste un minut."
TOO_MANY_PENDING = "Prea multe dispozitive în așteptare; contactează adminul."
MAX_WAIT = 60
MAX_BODY = 4 * 1024 * 1024
MAX_PROJECT_NODES = 20000
# Fiecare daemon are alt `snapshot_id` (GUID per scanare); ca două PC-uri din același joc să nu-și retrimită harta la fiecare sync,
# hub-ul cere proiectul altui dispozitiv decât cel care a raportat-o doar după PROJECT_REFRESH_SECONDS.
PROJECT_REFRESH_SECONDS = 300
AVATAR_CACHE_SECONDS = 3600
AVATAR_RETRY_SECONDS = 60
AVATAR_TIMEOUT = 5
AVATAR_MAX_BYTES = 512 * 1024
AVATAR_SIZES = (48, 60, 100)
AVATAR_API = "https://thumbnails.roblox.com/v1/users/avatar-headshot"
# Ruta avatarului este publică (un `<img src>` nu poate trimite antete), deci cache-ul are un plafon real (nu doar curățarea
# intrărilor expirate) și se servesc doar id-urile raportate de dispozitive: nimeni din afară nu poate umfla memoria hub-ului
# și nici nu poate folosi hub-ul ca amplificator de trafic către Roblox.
AVATAR_CACHE_ENTRIES = 256
# Cererile ieșite ale proxy-ului de avatar merg doar către aceste gazde, fără redirectări (nicio cale spre rețeaua internă).
AVATAR_HOSTS = frozenset({"thumbnails.roblox.com", "tr.rbxcdn.com"})
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEVICE_HEADER = "X-Studio-Harness-Device"
ADMIN_HEADER = "X-Studio-Harness-Admin"
ADMIN_ENV = "STUDIO_HARNESS_ADMIN_TOKEN"
OPEN_ENROLLMENT_ENV = "STUDIO_HARNESS_OPEN_ENROLLMENT"
ENROLLMENTS = ("approve", "open")
DEVICE_STATES = ("pending", "approved", "revoked")
STATE_ORDER = {"pending": 0, "approved": 1, "revoked": 2}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
# Meta unei sesiuni: câmpurile text se taie la MAX_META_TEXT, cele numerice trec ca numere, restul se ignoră (hub-ul nu stochează
# structuri arbitrare trimise de un dispozitiv).
META_TEXT_FIELDS = ("job_id", "kind", "provider", "developer", "name", "state", "studio_id", "cwd")
META_NUMBER_FIELDS = ("started", "last_activity")
# Lista din contract §3.4, derivată din cele de mai sus ca să nu poată ajunge să promită un câmp pe care `_session_meta` nu îl copiază.
META_FIELDS = META_TEXT_FIELDS + META_NUMBER_FIELDS + ("pending_approval", "claims")
JOURNAL_TEXT_LIMITS = {"job_id": 128, "provider": 40, "tool": 80, "summary": 300}
EVENT_TOO_LARGE = "Evenimentul depășește limita hub-ului și a fost omis."
SESSIONS_FULL = "Prea multe sesiuni pentru acest dispozitiv; închide una înainte de a deschide alta."
WORKSPACE_KEY = re.compile(r"(?:game|place):[1-9][0-9]{0,15}|local")
DEVICE_ROUTES = frozenset({"/hub/register", "/hub/sync", "/hub/claims/claim", "/hub/claims/release", "/hub/claims/touch", "/hub/claims/wait"})
UNKNOWN_DEVICE = "Dispozitiv necunoscut; înregistrează-te."
PENDING_DEVICE = "Dispozitivul așteaptă aprobarea adminului."
REVOKED_DEVICE = "Dispozitivul a fost revocat de admin."
ADMIN_REQUIRED = "Este necesar codul de administrator."
ADMIN_INVALID = "Codul de administrator este invalid."
DEVICE_REQUIRED = "Ruta cere antetul dispozitivului."
NO_WORKSPACE = "Dispozitivul nu are un workspace curent."
NO_ROUTE = "Rută inexistentă."
# Panoul web: un singur fișier, fără CDN; scripturile și stilurile inline sunt permise, orice resursă externă este blocată.
PANEL_HEADERS = (
    # `base-uri` și `form-action` nu moștenesc din `default-src`, iar `frame-ancestors` este singura protecție anti-încadrare
    # respectată de browserele moderne (X-Frame-Options rămâne pentru cele vechi): panoul ține codul de admin în localStorage.
    ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
                                "base-uri 'none'; form-action 'none'"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cache-Control", "no-cache"),
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class HubError(RuntimeError):
    def __init__(self, message: str, status: int = 400, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class OutdatedState(ValueError):
    """`hub-state.json` dintr-o altă versiune (0.8 avea `format: 1`): se ignoră, dar `hub_id` poate fi păstrat."""

    def __init__(self, state: dict[str, Any], hub_id: str | None):
        version, old_format = state.get("version"), state.get("format")
        if _is_int(version):
            label = "versiunea " + str(version)
        elif _is_int(old_format):
            label = "formatul " + str(old_format) + " (hub " + str(version) + ")"
        else:
            label = "versiunea " + repr(version)
        super().__init__(label + ", nu versiunea " + str(STATE_VERSION))
        self.hub_id = hub_id


@dataclass(frozen=True)
class Actor:
    """Cine face cererea, după validarea antetelor: cod de admin valid și/sau dispozitiv identificat prin token."""

    admin: bool = False
    device_id: str | None = None
    token_hash: str | None = None


def _text(value: Any, limit: int, name: str, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise HubError(f"Câmpul {name} este invalid.")
    return value


def _reject_constant(name: str) -> Any:
    raise ValueError("Valoarea " + name + " nu este permisă în JSON.")


def _clean(value: str) -> str:
    """Nume afișate: fără caractere de control, fără spații la capete."""
    return _CONTROL.sub(" ", value).strip()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _roblox_id(value: Any, name: str) -> int:
    """Id Roblox din JSON: întreg (sau float integral din Luau) între 0 și MAX_ID; lipsă = 0."""
    if value is None:
        return 0
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not _is_int(value) or value < 0 or value > MAX_ID:
        raise HubError(f"Câmpul {name} trebuie să fie un întreg între 0 și {MAX_ID}.")
    return value


def valid_workspace_key(value: Any) -> bool:
    """`game:<id>`, `place:<id>` (id-uri ≤ MAX_ID) sau `local`."""
    if not isinstance(value, str) or not WORKSPACE_KEY.fullmatch(value):
        return False
    return value == "local" or int(value.partition(":")[2]) <= MAX_ID


def meta_for_key(key: str) -> dict[str, Any]:
    """Meta minimă a unui workspace cunoscut doar după cheie (sesiune sau claim primit înaintea identității): numele = cheia."""
    kind, _, number = key.partition(":")
    ids = {"game_id": int(number)} if kind == "game" else {"place_id": int(number)} if kind == "place" else {}
    meta = workspace_meta(ids)
    meta["name"] = key
    return meta


def log(message: str) -> None:
    """Jurnal pe stdout (journald/docker): fără token-uri, fără conținutul sesiunilor."""
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + message, flush=True)


RELEASE_NAME = re.compile(r"[A-Za-z0-9._-]{1,120}")
RELEASE_TYPES = {".json": "application/json; charset=utf-8", ".zip": "application/zip", ".sha256": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


def releases_dir(app_dir: Path | None = None) -> Path:
    """`releases/` lângă team_hub.py (hub găzduit) sau în rădăcina repo-ului; hub-ul îl servește la /releases/."""
    base = Path(app_dir) if app_dir is not None else Path(__file__).resolve().parent
    for candidate in (base / "releases", base.parent / "releases"):
        if candidate.is_dir():
            return candidate
    return base / "releases"


def panel_file(app_dir: Path | None = None) -> Path | None:
    """`panel/index.html` lângă team_hub.py (hub găzduit) sau în rădăcina repo-ului (checkout); None dacă lipsește."""
    base = Path(app_dir) if app_dir is not None else Path(__file__).resolve().parent
    for candidate in (base / "panel" / "index.html", base.parent / "panel" / "index.html"):
        if candidate.is_file():
            return candidate
    return None


class _NoRedirect(HTTPRedirectHandler):
    """Fără redirectări: `urllib` le urmează implicit și acceptă și `http`, deci un 302 al API-ului ar deveni o cerere a hub-ului
    către orice gazdă (inclusiv adrese interne). Aici un 30x rămâne `HTTPError` → avatarul lipsește (204)."""

    def redirect_request(self, *_: Any) -> None:
        return None


# Folosit și de proxy-ul de avatar, și de `--approve-pending`: nicio cerere a hub-ului nu urmează un redirect.
_OPENER = build_opener(_NoRedirect)


def _avatar_host_allowed(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host in AVATAR_HOSTS or host.endswith(".rbxcdn.com"))


def fetch_avatar(user_id: int, size: int, timeout: float = AVATAR_TIMEOUT) -> bytes | None:
    """Fetcher-ul implicit al avatarului: API-ul de thumbnail-uri Roblox (JSON `data[0].imageUrl`), apoi imaginea PNG (≤ 512 KB).

    Ambele cereri merg doar către gazdele din `AVATAR_HOSTS` (thumbnails.roblox.com, *.rbxcdn.com) și nu urmează redirectări."""
    headers = {"User-Agent": "StudioHarnessHub/" + VERSION}
    query = urlencode({"userIds": user_id, "size": f"{size}x{size}", "format": "Png", "isCircular": "false"})
    api = AVATAR_API + "?" + query
    if not _avatar_host_allowed(api):
        return None
    with _OPENER.open(Request(api, headers=headers), timeout=timeout) as response:
        payload = json.loads(response.read(64 * 1024).decode("utf-8"))
    rows = payload.get("data") if isinstance(payload, dict) else None
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    image_url = row.get("imageUrl")
    if not isinstance(image_url, str) or not _avatar_host_allowed(image_url):
        return None
    with _OPENER.open(Request(image_url, headers=headers), timeout=timeout) as response:
        data = response.read(AVATAR_MAX_BYTES + 1)
    return data if len(data) <= AVATAR_MAX_BYTES else None


class Hub:
    """Starea hub-ului și toate operațiile lui; HTTP-ul (HubHandler) doar validează antetele și traduce rutele în apeluri."""

    def __init__(self, admin_code: str, clock: Any = time.time, logger: Any = None, app_dir: Path | None = None,
                 avatar_fetcher: Callable[[int, int], bytes | None] | None = None):
        self.admin_code = admin_code
        self.clock = clock
        # `log` se rezolvă la apel (nu ca valoare implicită), ca jurnalul înlocuit în main()/teste să fie folosit și de hub.
        self.log = logger if logger is not None else log
        self.app_dir = app_dir
        self.avatar_fetcher = avatar_fetcher if avatar_fetcher is not None else fetch_avatar
        self.hub_id = uuid.uuid4().hex
        self.enrollment = "approve"
        self.lock = threading.RLock()
        self.devices: dict[str, dict[str, Any]] = {}
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.claims: dict[str, ClaimTable] = {}
        self.journal: deque[dict[str, Any]] = deque(maxlen=MAX_JOURNAL)
        self.journal_seq = 0
        # Jobul → dispozitivul care l-a raportat (sesiune sau claim): un dispozitiv nu poate atinge joburile altuia.
        self.job_devices: dict[str, str] = {}
        self.avatars: dict[tuple[int, int], tuple[float, bytes | None]] = {}
        # Eșecurile de autorizare per client (IP), într-o fereastră glisantă: opresc ghicirea codului de admin prin forță brută.
        self.auth_failures: dict[str, deque[float]] = {}

    def panel_file(self) -> Path | None:
        return panel_file(self.app_dir)

    # ----- stare persistentă (versiunea 2) -----

    def export(self) -> dict[str, Any]:
        """Starea de salvat: dispozitive (cu `token_hash`, niciodată tokenul), workspace-uri cu sumarul proiectului, sesiuni (meta +
        ultimele 256 de evenimente), claims per workspace, jurnal (≤ 1000). Codul de admin nu se salvează."""
        now = self.clock()
        with self.lock:
            workspaces = {}
            for key, workspace in self.workspaces.items():
                row = {field: workspace[field] for field in ("key", "game_id", "place_id", "name", "creator_id", "creator_type", "first_seen", "last_seen")}
                row["project"] = self._project_summary(workspace)
                workspaces[key] = row
            sessions = {job_id: {"device_id": entry["device_id"], "workspace": entry["workspace"], "meta": copy.deepcopy(entry["meta"]),
                                 "events": copy.deepcopy(entry["events"][-SAVED_EVENTS:]), "closed_at": entry.get("closed_at")}
                        for job_id, entry in self.sessions.items()}
            # Rândurile `ClaimTable.export()` primesc și dispozitivul jobului, ca după repornire claims-urile să rămână ale lui.
            claims = {key: [dict(row, device_id=self.job_devices.get(row["job_id"])) for row in table.export()] for key, table in self.claims.items()}
            return {"version": STATE_VERSION, "hub_version": VERSION, "saved": now, "hub_id": self.hub_id, "enrollment": self.enrollment,
                    "journal_seq": self.journal_seq, "devices": copy.deepcopy(self.devices), "workspaces": workspaces, "sessions": sessions,
                    "claims": {key: rows for key, rows in claims.items() if rows}, "journal": [copy.deepcopy(row) for row in self.journal]}

    def save(self, path: Path) -> Path:
        """Scrie starea atomic (fișier .part, apoi înlocuire)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.export(), ensure_ascii=False, allow_nan=False).encode("utf-8")
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(data)
        # Starea conține `token_hash`-uri, identități Roblox și conținutul sesiunilor: permisiunile se restrâng pe fișierul
        # temporar (înainte de înlocuire), ca să nu existe nicio fereastră în care să fie lizibil pentru alți utilizatori locali.
        restrict_permissions(temporary)
        os.replace(temporary, target)
        restrict_permissions(target)
        return target

    def load(self, path: Path) -> bool:
        """Încarcă starea salvată dacă fișierul există și are versiunea 2; altfel jurnalizează și hub-ul pornește curat.

        O stare mai veche (0.8, `format: 1`) se ignoră, dar `hub_id`-ul ei valid se păstrează, ca daemon-urile să nu piardă cursoarele.
        Claims-urile expirate și dispozitivele offline sunt curățate imediat."""
        target = Path(path)
        try:
            if not target.is_file():
                return False
            if target.stat().st_size > MAX_STATE_BYTES:
                raise ValueError("fișierul depășește limita de dimensiune")
            state = json.loads(target.read_text(encoding="utf-8"))
            parsed = self._parse_state(state)
        except OutdatedState as error:
            kept = ""
            if error.hub_id:
                self.hub_id = error.hub_id
                kept = "; hub_id păstrat"
            self.log("starea salvată (" + target.name + ") are " + str(error) + "; se ignoră, hub-ul pornește curat" + kept)
            return False
        except (OSError, ValueError, TypeError) as error:
            self.log("starea salvată (" + target.name + ") este invalidă: " + (str(error) or type(error).__name__) + "; hub-ul pornește curat")
            return False
        now = self.clock()
        with self.lock:
            self.hub_id = parsed["hub_id"]
            self.enrollment = parsed["enrollment"]
            self.devices = parsed["devices"]
            self.workspaces = parsed["workspaces"]
            self.sessions = parsed["sessions"]
            self.journal = deque(parsed["journal"], maxlen=MAX_JOURNAL)
            self.journal_seq = parsed["journal_seq"]
            self.claims = {}
            self.job_devices = {}
            restored = 0
            for key, rows in parsed["claims"].items():
                self._workspace_for_key(key, now)
                restored += self._table(key).restore(rows)
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("job_id"), str) and isinstance(row.get("device_id"), str):
                        self.job_devices.setdefault(row["job_id"], row["device_id"])
            for job_id, entry in self.sessions.items():
                self._workspace_for_key(entry["workspace"], now)
                self.job_devices[job_id] = entry["device_id"]
            self._reap(now)
            self.log("stare încărcată din " + target.name + ": " + str(len(self.devices)) + " dispozitive, " + str(len(self.workspaces))
                     + " workspace-uri, " + str(len(self.sessions)) + " sesiuni, " + str(restored) + " claims, " + str(len(self.journal))
                     + " intrări de jurnal; hub_id păstrat")
        return True

    @staticmethod
    def _valid_hub_id(value: Any) -> str | None:
        return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) else None

    def _parse_state(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise ValueError("format necunoscut")
        if state.get("version") != STATE_VERSION:
            raise OutdatedState(state, self._valid_hub_id(state.get("hub_id")))
        hub_id = self._valid_hub_id(state.get("hub_id"))
        if hub_id is None:
            raise ValueError("hub_id lipsă")
        enrollment = state.get("enrollment") if state.get("enrollment") in ENROLLMENTS else "approve"
        raw_devices = state.get("devices")
        if not isinstance(raw_devices, dict):
            raise ValueError("dispozitive invalide")
        devices: dict[str, dict[str, Any]] = {}
        for device_id, device in raw_devices.items():
            if not isinstance(device, dict) or device.get("device_id") != device_id or not isinstance(device_id, str):
                continue
            token_hash = device.get("token_hash")
            if not isinstance(token_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", token_hash) or token_hash[:len(device_id)] != device_id:
                continue
            if device.get("status") not in DEVICE_STATES or not isinstance(device.get("machine"), str):
                continue
            user_id = device.get("roblox_user_id")
            restored = {"device_id": device_id, "token_hash": token_hash,
                        "roblox_user_id": user_id if _is_int(user_id) and 0 <= user_id <= MAX_ID else 0,
                        "roblox_name": _clean(device["roblox_name"]) if isinstance(device.get("roblox_name"), str) else "",
                        "machine": _clean(device["machine"]),
                        "bridge_id": device["bridge_id"] if isinstance(device.get("bridge_id"), str) else "",
                        "version": device["version"] if isinstance(device.get("version"), str) else "", "status": device["status"],
                        "first_seen": _number(device.get("first_seen")) or 0.0, "last_seen": _number(device.get("last_seen")) or 0.0,
                        "approved_at": _number(device.get("approved_at")) or 0.0,
                        "approved_by": device["approved_by"] if isinstance(device.get("approved_by"), str) else "",
                        "workspace": device["workspace"] if valid_workspace_key(device.get("workspace")) else None}
            if restored["roblox_user_id"] == 0:
                restored["roblox_name"] = ""
            if device.get("reaped") is True:
                restored["reaped"] = True
            if isinstance(device.get("project_rejected"), str):
                restored["project_rejected"] = device["project_rejected"]
            devices[device_id] = restored
        raw_workspaces = state.get("workspaces", {})
        if not isinstance(raw_workspaces, dict):
            raise ValueError("workspace-uri invalide")
        workspaces: dict[str, dict[str, Any]] = {}
        for key, row in raw_workspaces.items():
            if not valid_workspace_key(key) or not isinstance(row, dict):
                continue
            try:
                meta = workspace_meta(row)
            except ValueError:
                continue
            if meta["key"] != key:
                continue
            workspace = dict(meta, name=meta["name"] or key, first_seen=_number(row.get("first_seen")) or 0.0,
                             last_seen=_number(row.get("last_seen")) or 0.0, project=None)
            summary = row.get("project")
            if isinstance(summary, dict) and isinstance(summary.get("snapshot_id"), str) and summary["snapshot_id"].strip():
                groups = summary.get("groups") if isinstance(summary.get("groups"), dict) else {}
                reporter = devices.get(summary.get("reported_by")) if isinstance(summary.get("reported_by"), str) else None
                count = summary.get("count")
                workspace["project"] = {"snapshot_id": summary["snapshot_id"], "place_id": workspace["place_id"] or None, "place_name": workspace["name"],
                                        "studio_id": None, "count": count if _is_int(count) and count >= 0 else 0, "taken": None,
                                        "truncated": summary.get("truncated") is True,
                                        "groups": {name: {"count": total} for name, total in groups.items() if isinstance(name, str) and _is_int(total)},
                                        "nodes": [], "reported_by": summary.get("reported_by") if isinstance(summary.get("reported_by"), str) else None,
                                        "developer": self._display_name(reporter) if reporter else "", "machine": reporter["machine"] if reporter else "",
                                        "received": _number(summary.get("at")) or 0.0, "summary_only": True}
            workspaces[key] = workspace
        raw_sessions = state.get("sessions", {})
        if not isinstance(raw_sessions, dict):
            raise ValueError("sesiuni invalide")
        sessions: dict[str, dict[str, Any]] = {}
        for job_id, entry in raw_sessions.items():
            if not isinstance(job_id, str) or not job_id or not isinstance(entry, dict) or entry.get("device_id") not in devices:
                continue
            meta = entry.get("meta")
            if not isinstance(meta, dict) or meta.get("job_id") != job_id:
                continue
            device = devices[entry["device_id"]]
            workspace = entry.get("workspace") if valid_workspace_key(entry.get("workspace")) else (device["workspace"] or "local")
            events = [event for event in (entry.get("events") or []) if isinstance(event, dict) and _is_int(event.get("seq"))]
            meta = dict(meta, workspace=workspace, device_id=device["device_id"], remote=True)
            sessions[job_id] = {"device_id": device["device_id"], "workspace": workspace, "meta": meta, "events": events[-SAVED_EVENTS:],
                                "closed_at": _number(entry.get("closed_at"))}
        raw_claims = state.get("claims", {})
        if not isinstance(raw_claims, dict):
            raise ValueError("claims invalide")
        claims = {key: rows for key, rows in raw_claims.items() if valid_workspace_key(key) and isinstance(rows, list)}
        journal = [row for row in (state.get("journal") or []) if isinstance(row, dict) and _is_int(row.get("seq"))][-MAX_JOURNAL:]
        journal_seq = state.get("journal_seq")
        if not _is_int(journal_seq) or journal_seq < 0:
            journal_seq = 0
        journal_seq = max([journal_seq] + [row["seq"] for row in journal])
        return {"hub_id": hub_id, "enrollment": enrollment, "devices": devices, "workspaces": workspaces, "sessions": sessions,
                "claims": claims, "journal": journal, "journal_seq": journal_seq}

    # ----- identitate și autorizare -----

    def _check_attempts(self, client: str | None) -> None:
        """429 după AUTH_FAIL_LIMIT coduri de administrator greșite într-un minut de la același client (fereastră glisantă)."""
        if client is None:
            return
        now = self.clock()
        with self.lock:
            attempts = self.auth_failures.get(client)
            if attempts is None:
                return
            while attempts and now - attempts[0] > AUTH_FAIL_WINDOW:
                attempts.popleft()
            if not attempts:
                del self.auth_failures[client]
                return
            if len(attempts) >= AUTH_FAIL_LIMIT:
                raise HubError(TOO_MANY_ATTEMPTS, 429)

    def _note_failure(self, client: str | None) -> None:
        """Un cod de administrator greșit; se jurnalizează o singură dată, la atingerea plafonului, fără niciun fragment de cod."""
        if client is None:
            return
        now = self.clock()
        with self.lock:
            attempts = self.auth_failures.setdefault(client, deque())
            while attempts and now - attempts[0] > AUTH_FAIL_WINDOW:
                attempts.popleft()
            attempts.append(now)
            reached = len(attempts) == AUTH_FAIL_LIMIT
            # Fără listă nemărginită de clienți: curățăm ferestrele goale ale celorlalți.
            if len(self.auth_failures) > 1024:
                for key in [key for key, rows in self.auth_failures.items() if not rows or now - rows[-1] > AUTH_FAIL_WINDOW]:
                    del self.auth_failures[key]
        if reached:
            self.log("prea multe coduri de administrator greșite de la " + client + "; cererile lui sunt refuzate un minut")

    def actor(self, device_token: str | None, admin_code: str | None, allow_unknown: bool = False, client: str | None = None) -> Actor:
        """Validează antetele: codul de admin (dacă este prezent) trebuie să fie corect, tokenul de dispozitiv (dacă este prezent)
        trebuie să aibă 64 hex și să fie cunoscut — cu excepția înregistrării (`allow_unknown`).

        `client` (IP-ul cererii) activează limitarea de rată a codului de administrator — singurul secret ghicibil; un token de
        dispozitiv are 64 hex și un 401 „necunoscut” este normal după o repornire fără stare, deci acelea nu se contorizează."""
        admin = False
        if admin_code is not None:
            # Doar cererile care prezintă un cod de admin sunt limitate: în spatele unui proxy toți clienții împart aceeași
            # adresă, deci un atacator nu trebuie să poată bloca dispozitivele altora cu coduri greșite.
            self._check_attempts(client)
            if not hmac.compare_digest(self.admin_code.encode("utf-8"), admin_code.encode("utf-8")):
                self._note_failure(client)
                raise HubError(ADMIN_INVALID, 401)
            admin = True
        if device_token is None:
            return Actor(admin=admin)
        if not DEVICE_TOKEN_PATTERN.fullmatch(device_token):
            raise HubError(UNKNOWN_DEVICE, 401, {"status": "unknown"})
        token_hash = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
        device_id = device_id_for(device_token)
        with self.lock:
            known = self._device_by_hash(device_id, token_hash) is not None
        if not known and not allow_unknown:
            raise HubError(UNKNOWN_DEVICE, 401, {"status": "unknown"})
        return Actor(admin=admin, device_id=device_id, token_hash=token_hash)

    def _device_by_hash(self, device_id: str | None, token_hash: str | None) -> dict[str, Any] | None:
        device = self.devices.get(device_id) if isinstance(device_id, str) else None
        if device is None or not isinstance(token_hash, str) or not hmac.compare_digest(device["token_hash"], token_hash):
            return None
        return device

    def _resolve(self, actor: Actor) -> dict[str, Any] | None:
        """Dispozitivul cererii (None fără antet de dispozitiv); 401 dacă a fost uitat între timp."""
        if actor.device_id is None:
            return None
        device = self._device_by_hash(actor.device_id, actor.token_hash)
        if device is None:
            raise HubError(UNKNOWN_DEVICE, 401, {"status": "unknown"})
        return device

    @staticmethod
    def _check_status(device: dict[str, Any]) -> None:
        if device["status"] == "pending":
            raise HubError(PENDING_DEVICE, 403, {"status": "pending", "device_id": device["device_id"]})
        if device["status"] == "revoked":
            raise HubError(REVOKED_DEVICE, 403, {"status": "revoked", "device_id": device["device_id"]})

    def _member(self, actor: Actor) -> dict[str, Any] | None:
        """Rută de membru: dispozitiv aprobat sau cod de admin (cu sau fără dispozitiv)."""
        device = self._resolve(actor)
        if actor.admin:
            return device
        if device is None:
            raise HubError(UNKNOWN_DEVICE, 401, {"status": "unknown"})
        self._check_status(device)
        return device

    @staticmethod
    def _require_device(actor: Actor) -> None:
        """Rutele care acționează ca dispozitiv: anonim → 401 (necunoscut); admin fără antetul dispozitivului → 400."""
        if actor.device_id is None:
            raise HubError(DEVICE_REQUIRED, 400) if actor.admin else HubError(UNKNOWN_DEVICE, 401, {"status": "unknown"})

    def _as_device(self, actor: Actor) -> dict[str, Any]:
        """Rută care acționează ca dispozitiv (register, sync, claims): antetul dispozitivului este obligatoriu și pentru admin."""
        self._require_device(actor)
        device = self._resolve(actor)
        assert device is not None
        if not actor.admin:
            self._check_status(device)
        return device

    def _require_admin(self, actor: Actor) -> None:
        if not actor.admin:
            raise HubError(ADMIN_REQUIRED, 403)

    # ----- dispozitive -----

    @staticmethod
    def _display_name(device: dict[str, Any]) -> str:
        return device["roblox_name"] or device["machine"]

    def _label(self, device: dict[str, Any]) -> str:
        """`<nume Roblox> @ <mașină> (<device_id>)`; fără identitate Roblox numele cade pe mașină, deci scriem doar mașina."""
        name = device["roblox_name"]
        return (name + " @ " + device["machine"] if name else device["machine"]) + " (" + device["device_id"] + ")"

    def _online(self, device: dict[str, Any], now: float) -> bool:
        return device["status"] == "approved" and now - device["last_seen"] <= ONLINE_SECONDS

    def _device_row(self, device: dict[str, Any], now: float) -> dict[str, Any]:
        """Rândul public al unui dispozitiv (fără `token_hash`)."""
        row = {field: device[field] for field in ("device_id", "roblox_user_id", "roblox_name", "machine", "bridge_id", "version", "status",
                                                    "first_seen", "last_seen", "approved_at", "approved_by", "workspace")}
        row["online"] = self._online(device, now)
        return row

    @staticmethod
    def _present_row(device: dict[str, Any]) -> dict[str, Any]:
        return {"device_id": device["device_id"], "roblox_user_id": device["roblox_user_id"], "roblox_name": device["roblox_name"],
                "machine": device["machine"], "last_seen": device["last_seen"], "online": True}

    def _present_rows(self, key: str | None, now: float) -> list[dict[str, Any]]:
        """Dispozitivele prezente într-un workspace: aprobate, online (≤ 15 s) și cu acel workspace ca ultimul raportat."""
        if key is None:
            return []
        present = [device for device in self.devices.values() if device["workspace"] == key and self._online(device, now)]
        present.sort(key=lambda device: (self._display_name(device).lower(), device["machine"].lower()))
        return [self._present_row(device) for device in present]

    def _parse_roblox(self, value: Any) -> tuple[int, str]:
        if value is None:
            return 0, ""
        if not isinstance(value, dict):
            raise HubError("Câmpul roblox trebuie să fie un obiect sau null.")
        user_id = _roblox_id(value.get("user_id"), "roblox.user_id")
        name = _clean(_text(value.get("name"), 64, "roblox.name", required=False) or "")
        if user_id == 0:
            return 0, ""
        return user_id, name or "user_" + str(user_id)

    @staticmethod
    def _parse_workspace(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise HubError("Câmpul workspace trebuie să fie un obiect sau null.")
        try:
            return workspace_meta(value)
        except ValueError as error:
            raise HubError(str(error)) from None

    def _prune_workspaces(self, now: float) -> None:
        """Peste plafon, workspace-urile inactive (fără sesiuni, claims sau dispozitive) sunt uitate, cele mai vechi întâi."""
        if len(self.workspaces) < MAX_WORKSPACES:
            return
        used = {entry["workspace"] for entry in self.sessions.values()}
        used.update(key for key, table in self.claims.items() if table.snapshot())
        used.update(device["workspace"] for device in self.devices.values() if device["workspace"])
        idle = sorted((workspace for key, workspace in self.workspaces.items() if key not in used), key=lambda row: row["last_seen"])
        for workspace in idle[:len(self.workspaces) - MAX_WORKSPACES + 1]:
            self.claims.pop(workspace["key"], None)
            del self.workspaces[workspace["key"]]

    def _ensure_workspace(self, meta: dict[str, Any], now: float, touch: bool = True) -> dict[str, Any]:
        """Creează sau actualizează workspace-ul din meta (cheia este deja recalculată din id-uri de `workspace_meta`)."""
        key = meta["key"]
        workspace = self.workspaces.get(key)
        if workspace is None:
            self._prune_workspaces(now)
            workspace = self.workspaces[key] = dict(meta, name=meta["name"] or key, first_seen=now, last_seen=now, project=None)
            self.log("workspace nou: " + workspace["name"] + " (" + key + ")")
            return workspace
        for field in ("game_id", "place_id", "creator_id"):
            if meta[field]:
                workspace[field] = meta[field]
        if meta["creator_type"] != CREATOR_UNKNOWN:
            workspace["creator_type"] = meta["creator_type"]
        if meta["name"]:
            workspace["name"] = meta["name"]
        if touch:
            workspace["last_seen"] = now
        return workspace

    def _workspace_for_key(self, key: str, now: float) -> dict[str, Any]:
        """Workspace-ul cu cheia dată; unul necunoscut (sesiune sau claim raportate înaintea identității) primește o meta minimă."""
        workspace = self.workspaces.get(key)
        return workspace if workspace is not None else self._ensure_workspace(meta_for_key(key), now, touch=False)

    def _set_presence(self, device: dict[str, Any], meta: dict[str, Any] | None, now: float) -> None:
        if meta is None:
            device["workspace"] = None
            return
        self._ensure_workspace(meta, now)
        device["workspace"] = meta["key"]

    def register(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        """Singura rută care acceptă un dispozitiv necunoscut (îl creează) sau pending/revocat (îi actualizează datele și îi spune starea).

        Răspunsul are `status` approved/pending; un dispozitiv revocat primește HubError 403 cu `status: revoked`."""
        self._require_device(actor)
        user_id, roblox_name = self._parse_roblox(body.get("roblox"))
        machine = _clean(_text(body.get("machine"), 64, "machine"))
        if not machine:
            raise HubError("Câmpul machine este invalid.")
        bridge_id = _text(body.get("bridge_id"), 64, "bridge_id")
        version = _text(body.get("version"), 32, "version", required=False) or ""
        meta = self._parse_workspace(body.get("workspace"))
        now = self.clock()
        with self.lock:
            device = self._device_by_hash(actor.device_id, actor.token_hash)
            if device is None:
                approved = self.enrollment == "open"
                if not approved:
                    self._make_room_for_pending(now)
                device = self.devices[actor.device_id] = {
                    "device_id": actor.device_id, "token_hash": actor.token_hash, "roblox_user_id": user_id, "roblox_name": roblox_name,
                    "machine": machine, "bridge_id": bridge_id, "version": version, "status": "approved" if approved else "pending",
                    "first_seen": now, "last_seen": now, "approved_at": now if approved else 0.0, "approved_by": "open" if approved else "",
                    "workspace": None}
                self.log(("dispozitiv aprobat automat (înrolare deschisă): " if approved else "dispozitiv nou în așteptare: ") + self._label(device)
                         + " (daemon " + (version or "?") + ")")
            else:
                device.update(roblox_user_id=user_id, roblox_name=roblox_name, machine=machine, bridge_id=bridge_id, version=version)
                if device["status"] == "pending" and self.enrollment == "open":
                    device.update(status="approved", approved_at=now, approved_by="open")
                    self.log("dispozitiv aprobat automat (înrolare deschisă): " + self._label(device))
            device["last_seen"] = now
            device.pop("reaped", None)
            self._set_presence(device, meta, now)
            if device["status"] == "revoked":
                raise HubError(REVOKED_DEVICE, 403, {"status": "revoked", "device_id": device["device_id"]})
            return {"ok": True, "status": device["status"], "device_id": device["device_id"], "hub_id": self.hub_id, "enrollment": self.enrollment}

    def _make_room_for_pending(self, now: float) -> None:
        """Ruta de înregistrare este singura deschisă dispozitivelor necunoscute: lista de așteptare are un plafon, iar un
        dispozitiv în așteptare neatins de peste 24 h face loc unuia nou. Peste plafon, înregistrarea este refuzată (429)."""
        pending = [device for device in self.devices.values() if device["status"] == "pending"]
        if len(pending) < MAX_PENDING_DEVICES:
            return
        stale = [device for device in pending if now - device["last_seen"] > PENDING_IDLE_SECONDS]
        if not stale:
            raise HubError(TOO_MANY_PENDING, 429)
        oldest = min(stale, key=lambda device: device["last_seen"])
        self.log("dispozitiv în așteptare uitat (inactiv de peste 24 h, listă plină): " + self._label(oldest))
        self._drop_device_sessions(oldest["device_id"])
        del self.devices[oldest["device_id"]]

    def _release_device_claims(self, device_id: str) -> None:
        jobs = [job_id for job_id, owner in self.job_devices.items() if owner == device_id]
        for table in self.claims.values():
            for job_id in jobs:
                table.release(job_id)

    def _drop_session(self, job_id: str) -> None:
        entry = self.sessions.pop(job_id, None)
        for table in self.claims.values():
            table.release(job_id)
        if entry is not None:
            self.job_devices.pop(job_id, None)

    def _drop_device_sessions(self, device_id: str) -> None:
        self._release_device_claims(device_id)
        for job_id in [job_id for job_id, entry in self.sessions.items() if entry["device_id"] == device_id]:
            self._drop_session(job_id)
        for job_id in [job_id for job_id, owner in self.job_devices.items() if owner == device_id]:
            del self.job_devices[job_id]

    def _reap(self, now: float) -> None:
        """Dispozitivele aprobate fără sync de 60 s pierd claims-urile, sesiunile lor neterminale devin `lost`; sesiunile închise expiră după 30 min."""
        for device in self.devices.values():
            if device["status"] == "approved" and now - device["last_seen"] >= OFFLINE_SECONDS and not device.get("reaped"):
                device["reaped"] = True
                self.log("dispozitiv offline: " + self._label(device) + "; claims-urile lui au fost eliberate")
                self._release_device_claims(device["device_id"])
                for entry in self.sessions.values():
                    if entry["device_id"] == device["device_id"] and entry["meta"].get("state") not in TERMINAL_STATES and entry["closed_at"] is None:
                        entry["meta"]["state"] = "lost"
                        entry["closed_at"] = now
        for job_id, entry in list(self.sessions.items()):
            if entry.get("closed_at") is not None and now - entry["closed_at"] > SESSION_RETENTION:
                self._drop_session(job_id)

    # ----- sync -----

    def sync(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        """Ca `/team/sync` din 0.8 (sesiuni + evenimente incrementale, `want`, jurnal, `touch`, proiect cu digest), scoped pe workspace."""
        if "workspace" not in body:
            raise HubError("Câmpul workspace lipsește (meta sau null).")
        meta = self._parse_workspace(body["workspace"])
        roblox = self._parse_roblox(body["roblox"]) if "roblox" in body else None
        sessions = body.get("sessions", [])
        if not isinstance(sessions, list) or len(sessions) > MAX_SYNC_SESSIONS:
            raise HubError("sessions trebuie să fie o listă de cel mult 64 de sesiuni.")
        for row in sessions:
            if not isinstance(row, dict):
                raise HubError("Sesiune invalidă.")
            _text(row.get("job_id"), 128, "job_id")
            if not isinstance(row.get("events", []), list):
                raise HubError("events trebuie să fie o listă.")
        now = self.clock()
        with self.lock:
            device = self._as_device(actor)
            device_id = device["device_id"]
            device["last_seen"] = now
            device.pop("reaped", None)
            if roblox is not None:
                device["roblox_user_id"], device["roblox_name"] = roblox
            self._set_presence(device, meta, now)
            current = device["workspace"]
            developer = self._display_name(device)
            for row in sessions:
                job_id = row["job_id"]
                entry = self.sessions.get(job_id)
                if (entry and entry["device_id"] != device_id) or self.job_devices.get(job_id, device_id) != device_id:
                    continue
                key = row["workspace"] if valid_workspace_key(row.get("workspace")) else (entry["workspace"] if entry else current or "local")
                self._workspace_for_key(key, now)
                session_meta = self._session_meta(row)
                session_meta.update(developer=developer, device_id=device_id, roblox_user_id=device["roblox_user_id"], machine=device["machine"],
                                    remote=True, workspace=key)
                if entry is None:
                    self._make_room_for_session(device_id)
                    entry = self.sessions[job_id] = {"device_id": device_id, "workspace": key, "meta": session_meta, "events": [], "closed_at": None}
                    self.job_devices[job_id] = device_id
                entry["meta"] = session_meta
                entry["workspace"] = key
                last = entry["events"][-1]["seq"] if entry["events"] else 0
                for event in row.get("events", []):
                    if not isinstance(event, dict) or not _is_int(event.get("seq")) or event["seq"] <= last:
                        continue
                    entry["events"].append(self._event_row(event))
                    last = event["seq"]
                if len(entry["events"]) > MAX_EVENTS:
                    del entry["events"][:len(entry["events"]) - MAX_EVENTS]
                if session_meta.get("state") in TERMINAL_STATES:
                    if entry["closed_at"] is None:
                        entry["closed_at"] = now
                else:
                    entry["closed_at"] = None
            touched = body.get("touch", [])
            if isinstance(touched, list):
                for job_id in touched:
                    if isinstance(job_id, str) and self.job_devices.get(job_id, device_id) == device_id:
                        for table in self.claims.values():
                            table.touch(job_id)
            entries = body.get("journal") if isinstance(body.get("journal"), list) else []
            for item in entries[:MAX_SYNC_JOURNAL]:
                if not isinstance(item, dict):
                    continue
                self._append_journal(item, device, current, now)
            self._reap(now)
            want_project = self._store_project(device, current, body.get("project_digest"), body.get("project"), now)
            want = body.get("want", {})
            events: dict[str, list[dict[str, Any]]] = {}
            if isinstance(want, dict):
                for job_id, after in list(want.items())[:MAX_SYNC_SESSIONS]:
                    entry = self.sessions.get(job_id)
                    if entry and entry["device_id"] != device_id and entry["workspace"] == current and _is_int(after):
                        events[job_id] = [copy.deepcopy(event) for event in entry["events"] if event["seq"] > after]
            journal_after = body.get("journal_after", 0)
            if not _is_int(journal_after) or journal_after < 0:
                journal_after = 0
            journal = [copy.deepcopy(row) for row in self.journal if row["seq"] > journal_after and row["workspace"] == current][-SYNC_JOURNAL:]
            others = [copy.deepcopy(entry["meta"]) for entry in self.sessions.values() if entry["device_id"] != device_id and entry["workspace"] == current]
            others.sort(key=lambda row: row.get("started") or 0)
            return {"ok": True, "now": now, "hub_id": self.hub_id, "device": {"status": device["status"]}, "workspace": current,
                    "members": self._present_rows(current, now), "sessions": others, "events": events, "claims": self._claim_rows(current),
                    "journal": journal, "journal_seq": self.journal_seq, "want_project": want_project, "workspaces": self._workspace_rows(now)}

    @staticmethod
    def _session_meta(row: dict[str, Any]) -> dict[str, Any]:
        """Meta unei sesiuni raportate de un dispozitiv: doar câmpurile cunoscute, cu tipuri și lungimi plafonate."""
        meta: dict[str, Any] = {field: (row[field][:MAX_META_TEXT] if isinstance(row.get(field), str) else None) for field in META_TEXT_FIELDS}
        for field in META_NUMBER_FIELDS:
            meta[field] = _number(row.get(field))
        meta["pending_approval"] = row.get("pending_approval") is True
        claims = row.get("claims") if isinstance(row.get("claims"), list) else []
        meta["claims"] = [item[:MAX_CLAIM_PATH] for item in claims if isinstance(item, str)][:MAX_META_CLAIMS]
        return meta

    @staticmethod
    def _event_row(event: dict[str, Any]) -> dict[str, Any]:
        """Copia unui eveniment, cu plafon de dimensiune: unul prea mare devine o notă scurtă, ca memoria să rămână mărginită."""
        if len(json.dumps(event, ensure_ascii=False, allow_nan=False)) > MAX_EVENT_BYTES:
            kind = event.get("type") if isinstance(event.get("type"), str) and len(event["type"]) <= 40 else "status"
            return {"seq": event["seq"], "type": kind, "text": EVENT_TOO_LARGE}
        return copy.deepcopy(event)

    def _make_room_for_session(self, device_id: str) -> None:
        """Un dispozitiv nu poate ține mai mult de MAX_DEVICE_SESSIONS sesiuni: cea mai veche închisă face loc, altfel refuzăm."""
        owned = [(job_id, entry) for job_id, entry in self.sessions.items() if entry["device_id"] == device_id]
        if len(owned) < MAX_DEVICE_SESSIONS:
            return
        closed = [(job_id, entry) for job_id, entry in owned if entry.get("closed_at") is not None]
        if not closed:
            raise HubError(SESSIONS_FULL, 429)
        self._drop_session(min(closed, key=lambda item: item[1]["closed_at"])[0])

    def _append_journal(self, item: dict[str, Any], device: dict[str, Any], current: str | None, now: float) -> None:
        """Numerotează intrarea (seq global) și o atribuie workspace-ului din rând, altfel celui al sesiunii, altfel celui curent."""
        if valid_workspace_key(item.get("workspace")):
            key = item["workspace"]
        elif isinstance(item.get("job_id"), str) and item["job_id"] in self.sessions:
            key = self.sessions[item["job_id"]]["workspace"]
        else:
            key = current or "local"
        self.journal_seq += 1
        row: dict[str, Any] = {field: (item[field][:limit] if isinstance(item.get(field), str) else None)
                               for field, limit in JOURNAL_TEXT_LIMITS.items()}
        paths = item.get("paths") if isinstance(item.get("paths"), list) else []
        row["paths"] = [path[:MAX_CLAIM_PATH] for path in paths if isinstance(path, str)][:MAX_JOURNAL_PATHS]
        row["time"] = _number(item.get("time")) if _number(item.get("time")) is not None else now
        row.update(seq=self.journal_seq, workspace=key, device_id=device["device_id"], developer=self._display_name(device),
                   roblox_user_id=device["roblox_user_id"], machine=device["machine"])
        self.journal.append(row)

    # ----- harta proiectului -----

    @staticmethod
    def _valid_project(project: Any) -> dict[str, Any] | None:
        """Verificare structurală (tipuri și plafoane); clasificarea rămâne a daemon-ului, hub-ul nu o reface."""
        if not isinstance(project, dict):
            return None
        snapshot_id, nodes, groups = project.get("snapshot_id"), project.get("nodes"), project.get("groups")
        if not isinstance(snapshot_id, str) or not snapshot_id.strip() or len(snapshot_id) > 128:
            return None
        if not isinstance(nodes, list) or len(nodes) > MAX_PROJECT_NODES or any(not isinstance(node, list) or len(node) != 5 for node in nodes):
            return None
        if not isinstance(groups, dict) or any(not isinstance(key, str) or not isinstance(group, dict) for key, group in groups.items()):
            return None
        place_id, place_name = project.get("place_id"), project.get("place_name")
        if place_id is not None and (type(place_id) not in (int, str) or (isinstance(place_id, str) and len(place_id) > 64)):
            return None
        if place_name is not None and (not isinstance(place_name, str) or len(place_name) > 200):
            return None
        count, taken = project.get("count"), project.get("taken")
        return {"snapshot_id": snapshot_id, "place_id": place_id, "place_name": place_name or "",
                "studio_id": project.get("studio_id") if isinstance(project.get("studio_id"), str) else None,
                "count": count if _is_int(count) and count >= 0 else len(nodes), "taken": _number(taken),
                "truncated": project.get("truncated") is True, "groups": copy.deepcopy(groups), "nodes": copy.deepcopy(nodes)}

    def _store_project(self, device: dict[str, Any], key: str | None, digest: Any, project: Any, now: float) -> bool:
        """Reține proiectul trimis pentru workspace-ul curent și spune dacă hub-ul vrea snapshot-ul anunțat prin `project_digest`."""
        if key is None:
            return False
        workspace = self.workspaces[key]
        if project is not None:
            valid = self._valid_project(project)
            if valid is None:
                # Nu cerem din nou același snapshot: daemon-ul l-ar retrimite la fiecare sync.
                device["project_rejected"] = digest if isinstance(digest, str) else None
                self.log("proiect invalid de la " + self._label(device) + " pentru " + key + "; ignorat")
            else:
                valid.update(reported_by=device["device_id"], developer=self._display_name(device), machine=device["machine"], received=now)
                if valid["taken"] is None:
                    valid["taken"] = now
                workspace["project"] = valid
                device.pop("project_rejected", None)
                self.log("proiect primit de la " + self._label(device) + " pentru " + workspace["name"] + " (" + key + "): " + str(valid["count"]) + " noduri")
        if not isinstance(digest, str) or not digest.strip() or len(digest) > 128:
            return False
        if device.get("project_rejected") == digest:
            return False
        current = workspace["project"]
        # După o repornire hub-ul are doar sumarul salvat: cere din nou proiectul complet.
        if current is None or current.get("summary_only") is True:
            return True
        if current["snapshot_id"] == digest:
            return False
        # Propriul dispozitiv a rescanat (snapshot nou) → îl vrem; de la alt dispozitiv doar după fereastra de reîmprospătare.
        return current.get("reported_by") == device["device_id"] or now - (current.get("received") or 0.0) >= PROJECT_REFRESH_SECONDS

    @staticmethod
    def _group_counts(project: dict[str, Any]) -> dict[str, int]:
        return {key: (group.get("count") if _is_int(group.get("count")) else 0) for key, group in project["groups"].items()}

    def _project_brief(self, workspace: dict[str, Any]) -> dict[str, Any] | None:
        project = workspace.get("project")
        if not project:
            return None
        return {"count": project["count"], "digest": project["snapshot_id"], "groups": self._group_counts(project)}

    def _project_summary(self, workspace: dict[str, Any]) -> dict[str, Any] | None:
        project = workspace.get("project")
        if not project:
            return None
        return {"digest": project["snapshot_id"], "snapshot_id": project["snapshot_id"], "count": project["count"], "truncated": project["truncated"],
                "groups": self._group_counts(project), "reported_by": project.get("reported_by"), "at": project.get("received")}

    # ----- interogări (membru) -----

    def _workspace_rows(self, now: float) -> list[dict[str, Any]]:
        rows = []
        for key, workspace in self.workspaces.items():
            table = self.claims.get(key)
            rows.append({"key": key, "name": workspace["name"], "game_id": workspace["game_id"], "place_id": workspace["place_id"],
                         "creator_id": workspace["creator_id"], "creator_type": workspace["creator_type"], "last_seen": workspace["last_seen"],
                         "members_online": self._present_rows(key, now),
                         "sessions_active": sum(1 for entry in self.sessions.values() if entry["workspace"] == key and entry["closed_at"] is None),
                         "claims": len(table.snapshot()) if table else 0, "project": self._project_brief(workspace)})
        rows.sort(key=lambda row: (-row["last_seen"], row["name"].lower(), row["key"]))
        return rows

    def _claim_rows(self, key: str | None) -> list[dict[str, Any]]:
        table = self.claims.get(key) if key is not None else None
        if table is None:
            return []
        return [dict(row, workspace=key) for row in table.snapshot()]

    def _workspace_detail(self, key: str, now: float) -> dict[str, Any]:
        workspace = self.workspaces[key]
        sessions = [copy.deepcopy(entry["meta"]) for entry in self.sessions.values() if entry["workspace"] == key]
        sessions.sort(key=lambda row: row.get("started") or 0)
        journal = [copy.deepcopy(row) for row in self.journal if row["workspace"] == key][-WORKSPACE_JOURNAL:]
        return {"workspace": {field: workspace[field] for field in ("key", "game_id", "place_id", "name", "creator_id", "creator_type", "first_seen", "last_seen")},
                "members": self._present_rows(key, now), "sessions": sessions, "claims": self._claim_rows(key), "journal": journal,
                "project": self._project_summary(workspace)}

    def _known_key(self, key: Any) -> str:
        if key is None or key == "":
            raise HubError("Parametrul key lipsește.")
        if not valid_workspace_key(key) or key not in self.workspaces:
            raise HubError("Workspace-ul nu există.", 404)
        return key

    def status(self, actor: Actor) -> dict[str, Any]:
        """Accesibil și dispozitivelor pending/revocate (își află starea) și codului de admin singur (`device: null`)."""
        now = self.clock()
        with self.lock:
            self._reap(now)
            device = self._resolve(actor)
            if device is None and not actor.admin:
                raise HubError(UNKNOWN_DEVICE, 401, {"status": "unknown"})
            row = None if device is None else {field: device[field] for field in ("device_id", "status", "roblox_user_id", "roblox_name", "machine")}
            return {"ok": True, "version": VERSION, "hub_id": self.hub_id, "enrollment": self.enrollment, "now": now, "admin": actor.admin,
                    "device": row, "workspaces": len(self.workspaces),
                    "members_online": sum(1 for device in self.devices.values() if self._online(device, now))}

    def workspaces_summary(self, actor: Actor) -> dict[str, Any]:
        now = self.clock()
        with self.lock:
            self._member(actor)
            self._reap(now)
            return {"ok": True, "workspaces": self._workspace_rows(now)}

    def workspace(self, actor: Actor, key: Any) -> dict[str, Any]:
        now = self.clock()
        with self.lock:
            self._member(actor)
            self._reap(now)
            return {"ok": True, **self._workspace_detail(self._known_key(key), now)}

    def project(self, actor: Actor, key: Any) -> dict[str, Any]:
        """Harta completă a proiectului workspace-ului; None după repornire (doar sumar) sau când nu a fost trimisă."""
        with self.lock:
            self._member(actor)
            project = self.workspaces[self._known_key(key)].get("project")
            if not project or project.get("summary_only") is True:
                return {"ok": True, "project": None}
            return {"ok": True, "project": copy.deepcopy(project)}

    def panel_data(self, actor: Actor, key: Any = None) -> dict[str, Any]:
        """Tot ce afișează panoul într-un apel: eu, hub, sumarul workspace-urilor, workspace-ul selectat (sau curent), dispozitivele în așteptare (admin)."""
        now = self.clock()
        with self.lock:
            device = self._member(actor)
            self._reap(now)
            if device is not None:
                me = {field: device[field] for field in ("device_id", "status", "roblox_user_id", "roblox_name", "machine")}
                # `workspace` este cheia ultimului workspace raportat: panoul poate marca „al tău” și când dispozitivul nu mai e prezent.
                me.update(workspace=device["workspace"], admin=actor.admin)
            else:
                me = {"device_id": None, "status": "admin", "roblox_user_id": 0, "roblox_name": "admin", "machine": "",
                      "workspace": None, "admin": True}
            if key is None or key == "":
                key = device["workspace"] if device is not None else None
            selected = self._workspace_detail(key, now) if valid_workspace_key(key) and key in self.workspaces else None
            result = {"ok": True, "me": me, "hub": {"hub_id": self.hub_id, "version": VERSION, "enrollment": self.enrollment, "now": now},
                      "workspaces": self._workspace_rows(now), "selected": selected}
            if actor.admin:
                result["pending_devices"] = sum(1 for row in self.devices.values() if row["status"] == "pending")
            return result

    def session(self, actor: Actor, job: Any, after: Any = 0) -> dict[str, Any]:
        with self.lock:
            self._member(actor)
            if not isinstance(job, str) or not job:
                raise HubError("Parametrul job lipsește.")
            entry = self.sessions.get(job)
            if entry is None:
                raise HubError("Sesiunea nu există.", 404)
            cursor = after if _is_int(after) and after >= 0 else 0
            events = [copy.deepcopy(event) for event in entry["events"] if event["seq"] > cursor]
            return {"ok": True, "meta": copy.deepcopy(entry["meta"]), "events": events,
                    "last_seq": entry["events"][-1]["seq"] if entry["events"] else 0}

    # ----- avatar -----

    def _known_user(self, user_id: int) -> bool:
        """Ruta avatarului este publică: servim doar conturile raportate de dispozitive, ca nimeni din afară să nu poată
        umple cache-ul sau să folosească hub-ul ca amplificator de cereri către Roblox."""
        with self.lock:
            return any(device["roblox_user_id"] == user_id for device in self.devices.values())

    def avatar(self, user: Any, size: Any = 48) -> bytes | None:
        """PNG-ul avatarului Roblox (cache 1 h; un eșec se reține 60 s ca să nu insistăm) sau None → 204."""
        try:
            user_id = int(user) if isinstance(user, (str, int)) and not isinstance(user, bool) else 0
        except ValueError:
            user_id = 0
        if user_id <= 0 or user_id > MAX_ID:
            raise HubError("Parametrul user trebuie să fie un id Roblox (întreg pozitiv).")
        try:
            size_value = int(size) if size is not None else AVATAR_SIZES[0]
        except (TypeError, ValueError):
            size_value = 0
        if size_value not in AVATAR_SIZES:
            raise HubError("Parametrul size trebuie să fie 48, 60 sau 100.")
        key = (user_id, size_value)
        now = self.clock()
        with self.lock:
            cached = self.avatars.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
        if not self._known_user(user_id):
            return None
        try:
            data = self.avatar_fetcher(user_id, size_value)
        except Exception as error:  # rețea, JSON, timeout: orice eșec înseamnă 204, panoul desenează inițialele
            self.log("avatar indisponibil pentru utilizatorul " + str(user_id) + ": " + type(error).__name__)
            data = None
        if not isinstance(data, (bytes, bytearray)) or not data.startswith(PNG_MAGIC) or len(data) > AVATAR_MAX_BYTES:
            data = None
        with self.lock:
            if len(self.avatars) >= AVATAR_CACHE_ENTRIES:
                self.avatars = {item: value for item, value in self.avatars.items() if value[0] > now}
                # Plafon real, nu doar curățarea expirărilor: intrările proaspete trăiesc o oră, deci fără asta dicționarul ar crește.
                extra = len(self.avatars) - AVATAR_CACHE_ENTRIES + 1
                for item, _ in sorted(self.avatars.items(), key=lambda pair: pair[1][0])[:max(0, extra)]:
                    del self.avatars[item]
            self.avatars[key] = (now + (AVATAR_CACHE_SECONDS if data else AVATAR_RETRY_SECONDS), bytes(data) if data else None)
        return bytes(data) if data else None

    # ----- claims (per workspace) -----

    def _table(self, key: str) -> ClaimTable:
        table = self.claims.get(key)
        if table is None:
            table = self.claims[key] = ClaimTable(clock=self.clock)
        return table

    def _claim_workspace(self, device: dict[str, Any], body: dict[str, Any], now: float) -> str:
        """Workspace-ul unui apel de claims: cel din corp, altfel cel curent al dispozitivului (400 dacă nu există)."""
        key = body.get("workspace")
        if key is None:
            key = device["workspace"]
            if key is None:
                raise HubError(NO_WORKSPACE)
        elif not valid_workspace_key(key):
            raise HubError("Câmpul workspace este invalid.")
        self._workspace_for_key(key, now)
        return key

    def _tables_for(self, body: dict[str, Any]) -> list[ClaimTable]:
        key = body.get("workspace")
        if key is None:
            return list(self.claims.values())
        if not valid_workspace_key(key):
            raise HubError("Câmpul workspace este invalid.")
        table = self.claims.get(key)
        return [table] if table else []

    def _check_job_owner(self, job_id: str, device_id: str) -> None:
        if self.job_devices.get(job_id, device_id) != device_id:
            raise HubError("Sesiunea aparține altui dispozitiv.", 403)

    @staticmethod
    def _held(tables: list[ClaimTable], job_id: str) -> list[str]:
        return [display(claim.path) for table in tables for claim in table.held_by(job_id)]

    def claim(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        with self.lock:
            device = self._as_device(actor)
            job_id = _text(body.get("job_id"), 128, "job_id")
            paths = body.get("paths")
            if not isinstance(paths, list) or not paths or len(paths) > 32 or any(not isinstance(path, str) for path in paths):
                raise HubError("paths trebuie să fie o listă de 1-32 căi text.")
            reason = body.get("reason", "")
            if not isinstance(reason, str) or len(reason) > 200:
                raise HubError("reason trebuie să fie un text de cel mult 200 de caractere.")
            self._check_job_owner(job_id, device["device_id"])
            key = self._claim_workspace(device, body, now)
            table = self._table(key)
            try:
                added, conflicts = table.claim(job_id, self._display_name(device), paths, reason)
            except ValueError as error:
                raise HubError(str(error)) from None
            if conflicts:
                raise HubError("Conflict de claim.", 409, {"conflicts": conflicts})
            self.job_devices[job_id] = device["device_id"]
            return {"ok": True, "claimed": [display(claim.path) for claim in added], "held": self._held([table], job_id)}

    def release(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            device = self._as_device(actor)
            job_id = _text(body.get("job_id"), 128, "job_id")
            paths = body.get("paths")
            if paths is not None and (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)):
                raise HubError("paths trebuie să fie o listă de texte.")
            self._check_job_owner(job_id, device["device_id"])
            tables = self._tables_for(body)
            released = []
            try:
                for table in tables:
                    released.extend(display(claim.path) for claim in table.release(job_id, paths))
            except ValueError as error:
                raise HubError(str(error)) from None
            return {"ok": True, "released": released, "held": self._held(tables, job_id)}

    def touch(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            device = self._as_device(actor)
            job_id = _text(body.get("job_id"), 128, "job_id")
            self._check_job_owner(job_id, device["device_id"])
            tables = self._tables_for(body)
            for table in tables:
                table.touch(job_id)
            return {"ok": True, "held": self._held(tables, job_id)}

    def wait(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        """Ține conexiunea (cel mult MAX_WAIT) până când calea este liberă în workspace; nu revendică nimic."""
        path = body.get("path")
        timeout = body.get("timeout_seconds", MAX_WAIT)
        if not isinstance(path, str) or not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise HubError("wait cere path (text) și timeout_seconds (număr).")
        try:
            target = normalize(path)
        except ValueError as error:
            raise HubError(str(error)) from None
        now = self.clock()
        with self.lock:
            device = self._as_device(actor)
            table = self._table(self._claim_workspace(device, body, now))
        free = table.wait_free(target, min(max(float(timeout), 0.0), MAX_WAIT))
        return {"ok": True, "free": free}

    # ----- administrare -----

    def admin_devices(self, actor: Actor) -> dict[str, Any]:
        now = self.clock()
        with self.lock:
            self._require_admin(actor)
            self._reap(now)
            devices = sorted(self.devices.values(), key=lambda row: (STATE_ORDER[row["status"]], self._display_name(row).lower(), row["machine"].lower()))
            return {"ok": True, "devices": [self._device_row(device, now) for device in devices]}

    def _admin_target(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(actor)
        device_id = _text(body.get("device_id"), 64, "device_id")
        device = self.devices.get(device_id)
        if device is None:
            raise HubError("Dispozitivul nu există.", 404)
        return device

    def approve_device(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        with self.lock:
            device = self._admin_target(actor, body)
            if device["status"] != "approved":
                device.update(status="approved", approved_at=now, approved_by="admin")
                self.log("dispozitiv aprobat de admin: " + self._label(device))
            return {"ok": True, "device": self._device_row(device, now)}

    def revoke_device(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        """Revocare: dispozitivul rămâne listat (poate fi aprobat din nou), sesiunile lui dispar și claims-urile se eliberează."""
        now = self.clock()
        with self.lock:
            device = self._admin_target(actor, body)
            if device["status"] != "revoked":
                device["status"] = "revoked"
                self._drop_device_sessions(device["device_id"])
                self.log("dispozitiv revocat de admin: " + self._label(device) + "; sesiunile și claims-urile lui au fost eliberate")
            return {"ok": True, "device": self._device_row(device, now)}

    def forget_device(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            device = self._admin_target(actor, body)
            self._drop_device_sessions(device["device_id"])
            del self.devices[device["device_id"]]
            self.log("dispozitiv uitat de admin: " + self._label(device) + " (poate reveni ca pending)")
            return {"ok": True}

    def set_enrollment(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self._require_admin(actor)
            mode = body.get("mode")
            if mode not in ENROLLMENTS:
                raise HubError("Câmpul mode trebuie să fie approve sau open.")
            if mode != self.enrollment:
                self.enrollment = mode
                self.log("înrolare schimbată de admin: " + mode)
            return {"ok": True, "enrollment": self.enrollment}


TeamHub = Hub


class HubHandler(BaseHTTPRequestHandler):
    server_version = "StudioHarnessHub/1.0"

    def version_string(self) -> str:
        """Antetul `Server` fără versiunea interpretului Python."""
        return self.server_version

    def log_message(self, *_: Any) -> None:
        pass

    def _json(self, status: int, value: Any) -> None:
        self._discard_body()
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self._discard_body()
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _panel(self, hub: Hub) -> None:
        """Pagina panoului, fără antete de autorizare; datele ei vin din /hub/* cu antetul dispozitivului sau al adminului."""
        self._discard_body()
        path = hub.panel_file()
        if path is None:
            raise HubError("Panoul web nu este instalat: panel/index.html lipsește lângă team_hub.py.", 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in PANEL_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _avatar(self, hub: Hub, query: dict[str, list[str]]) -> None:
        """PNG-ul avatarului (public: `<img src>` nu poate trimite antete) sau 204 fără corp la orice eșec."""
        self._discard_body()
        data = hub.avatar(query.get("user", [None])[0], query.get("size", [None])[0])
        if data is None:
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=" + str(AVATAR_CACHE_SECONDS))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _check_origin(self) -> None:
        """Panoul rulează pe același origin cu hub-ul; orice alt Origin (sau `null`) este refuzat."""
        origin = self.headers.get("Origin")
        if not origin:
            return
        host = (self.headers.get("Host") or "").strip().lower()
        if not host or urlsplit(origin.strip()).netloc.lower() != host:
            raise HubError("Accesul din pagini web nu este permis.", 403)

    def _discard_body(self) -> None:
        """Consumă corpul necitit înainte de un refuz timpuriu (401/403/404): altfel, pe Windows, închiderea conexiunii cu date necitite poate anula răspunsul și clientul pierde JSON-ul."""
        if getattr(self, "body_consumed", False):
            return
        self.body_consumed = True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < length <= MAX_BODY:
            self.rfile.read(length)

    def _body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise HubError("Este necesar Content-Type application/json.", 415)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise HubError("Content-Length invalid.") from None
        if not 0 <= length <= MAX_BODY:
            raise HubError("Corpul cererii lipsește sau depășește limita.", 413)
        self.body_consumed = True
        try:
            # `parse_constant` refuză NaN/Infinity: altfel ar ajunge în stare și `json.dumps(allow_nan=False)` din `save` ar eșua.
            value = json.loads(self.rfile.read(length), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError):
            raise HubError("JSON invalid.") from None
        if not isinstance(value, dict):
            raise HubError("Corpul cererii trebuie să fie un obiect JSON.")
        return value

    def _release_file(self, hub: Hub, name: str) -> None:
        """Canalul de actualizare servit de hub: manifest.json și pachetele, fără antete, doar nume simple."""
        self._discard_body()
        if not RELEASE_NAME.fullmatch(name) or ".." in name:
            raise HubError(NO_ROUTE, 404)
        target = releases_dir(getattr(hub, "app_dir", None)) / name
        suffix = target.suffix.lower()
        if not target.is_file() or suffix not in RELEASE_TYPES:
            raise HubError(NO_ROUTE, 404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", RELEASE_TYPES[suffix])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if suffix == ".json" else "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _first(query: dict[str, list[str]], name: str) -> str | None:
        return query.get(name, [None])[0]

    def _handle(self) -> None:
        hub: Hub = self.server.hub
        try:
            self._check_origin()
            url = urlsplit(self.path)
            path = strip_base(url.path)
            query = parse_qs(url.query)
            get, post = self.command == "GET", self.command == "POST"
            if get and path == "/healthz":
                # Pentru reverse proxy și monitorizare: fără antete și fără date despre hub.
                self._json(200, {"ok": True, "version": VERSION})
                return
            if get and path in ("/panel", "/panel/"):
                self._panel(hub)
                return
            if get and path == "/":
                # Rădăcina (https://lostcube.pro/ sau hub-ul direct) duce la panou. `Location` relativ, ca să
                # rămână corect și când proxy-ul a scos prefixul; doar `/roblox/harness` fără bară finală primește calea absolută.
                self._redirect(BASE_PATH + "/panel" if url.path == BASE_PATH else "panel")
                return
            if get and path.startswith("/releases/"):
                self._release_file(hub, path[len("/releases/"):])
                return
            if get and path == "/hub/avatar":
                self._avatar(hub, query)
                return
            actor = hub.actor(self.headers.get(DEVICE_HEADER), self.headers.get(ADMIN_HEADER),
                              allow_unknown=post and path == "/hub/register", client=self.client_address[0] if self.client_address else None)
            code = 200
            if path.startswith("/hub/admin/"):
                if not actor.admin:
                    raise HubError(ADMIN_REQUIRED, 403)
                if get and path == "/hub/admin/devices":
                    result = hub.admin_devices(actor)
                elif post and path == "/hub/admin/devices/approve":
                    result = hub.approve_device(actor, self._body())
                elif post and path == "/hub/admin/devices/revoke":
                    result = hub.revoke_device(actor, self._body())
                elif post and path == "/hub/admin/devices/forget":
                    result = hub.forget_device(actor, self._body())
                elif post and path == "/hub/admin/enrollment":
                    result = hub.set_enrollment(actor, self._body())
                else:
                    raise HubError(NO_ROUTE, 404)
            elif get and path == "/hub/status":
                result = hub.status(actor)
            elif post and path == "/hub/register":
                result = hub.register(actor, self._body())
                code = 202 if result["status"] == "pending" else 200
            elif post and path == "/hub/sync":
                result = hub.sync(actor, self._body())
            elif get and path == "/hub/workspaces":
                result = hub.workspaces_summary(actor)
            elif get and path == "/hub/workspace":
                result = hub.workspace(actor, self._first(query, "key"))
            elif get and path == "/hub/project":
                result = hub.project(actor, self._first(query, "key"))
            elif get and path == "/hub/panel-data":
                result = hub.panel_data(actor, self._first(query, "workspace"))
            elif get and path == "/hub/session":
                after = self._first(query, "after")
                result = hub.session(actor, self._first(query, "job"), int(after) if after is not None and after.isdigit() else 0)
            elif post and path == "/hub/claims/claim":
                result = hub.claim(actor, self._body())
            elif post and path == "/hub/claims/release":
                result = hub.release(actor, self._body())
            elif post and path == "/hub/claims/touch":
                result = hub.touch(actor, self._body())
            elif post and path == "/hub/claims/wait":
                result = hub.wait(actor, self._body())
            else:
                raise HubError(NO_ROUTE, 404)
            self._json(code, result)
        except HubError as error:
            self._json(error.status, {"ok": False, "error": str(error), **error.payload})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            hub.log("eroare internă la " + self.command + " " + self.path.split("?")[0] + ": " + type(error).__name__)
            self._json(500, {"ok": False, "error": "Eroare internă a hub-ului."})

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._json(403, {"ok": False, "error": "Accesul din pagini web nu este permis."})


class HubServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """O filă de panou închisă în timpul polling-ului, un `claims/wait` abandonat sau un daemon oprit rup conexiunea înainte de
        `handle_one_request`: `socketserver` ar tipări stiva completă. Le trecem sub tăcere; restul erorilor merg mai departe."""
        if issubclass(sys.exc_info()[0] or Exception, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def __init__(self, listen: str, port: int, hub: Hub, inherited_fd: int | None = None):
        if inherited_fd is None:
            super().__init__((listen, port), HubHandler)
        else:
            # POSIX: socketul de ascultare vine de la procesul vechi (`--inherit-socket`): fără bind, fără gap de port.
            super().__init__((listen, port), HubHandler, bind_and_activate=False)
            self.socket.close()
            self.socket = socket.socket(fileno=inherited_fd)
            self.server_address = tuple(self.socket.getsockname()[:2])
            self.server_name, self.server_port = self.server_address[0], self.server_address[1]
        self.hub = hub
        self.inherited = inherited_fd is not None
        self.active_requests = 0
        self.idle = threading.Condition()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(120)
        return connection, address

    def process_request_thread(self, request, client_address):
        with self.idle:
            self.active_requests += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.idle:
                self.active_requests -= 1
                self.idle.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        """Așteaptă (cel mult `timeout`) încheierea cererilor în curs: la relansare ele se termină în procesul vechi."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self.idle:
            while self.active_requests > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.idle.wait(remaining)
            return True


def reexec_supported() -> bool:
    """POSIX: execv păstrează PID-ul și descriptorii; pe Windows execv nu citează argumentele sigur, deci hub-ul se repornește din afară."""
    return os.name != "nt" and hasattr(os, "execv")


def reexec_arguments(argv: list[str], fd: int) -> list[str]:
    """Argumentele procesului nou: cele curente fără `--inherit-socket`, plus fd-ul socketului moștenit."""
    arguments = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item == "--inherit-socket":
            skip = True
            continue
        if item.startswith("--inherit-socket="):
            continue
        arguments.append(item)
    return arguments + ["--inherit-socket", str(fd)]


def reexec(server: HubServer, script: Path) -> None:
    """POSIX: înlocuiește procesul cu codul nou, păstrând socketul de ascultare deschis (conexiunile noi așteaptă în backlog)."""
    server.wait_idle(DRAIN_SECONDS)
    fd = server.socket.fileno()
    os.set_inheritable(fd, True)
    log("relansez hub-ul cu socketul moștenit (fd " + str(fd) + "), fără întrerupere")
    os.execv(sys.executable, [sys.executable, "-u", str(script), *reexec_arguments(sys.argv[1:], fd)])


def valid_admin_code(code: str) -> bool:
    return 16 <= len(code) <= 512 and not any(character.isspace() for character in code)


def approve_pending(state: Path, port: int, admin_code: str, request: Callable[..., Any] | None = None, logger: Callable[[str], None] | None = None) -> int:
    """`--approve-pending`: aprobă dispozitivele în așteptare prin hub-ul în execuție (HTTP pe loopback), altfel direct în hub-state.json.

    Întoarce numărul aprobat. Un hub care răspunde, dar refuză (cod greșit) ridică HubError: starea nu se editează sub un hub activ."""
    call = request if request is not None else _OPENER.open
    logger = logger if logger is not None else log
    base = "http://127.0.0.1:" + str(port)
    headers = {ADMIN_HEADER: admin_code, "Content-Type": "application/json"}
    listing = None
    try:
        with call(Request(base + "/hub/admin/devices", headers=headers), timeout=5) as response:
            listing = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise HubError("Hub-ul în execuție a refuzat cererea (HTTP " + str(error.code) + "); verifică codul de administrator.", error.code) from None
    except ValueError:
        # Hub-ul (sau un proxy) a răspuns, dar corpul nu este JSON: nu edităm `hub-state.json` sub un proces care îl va suprascrie.
        raise HubError("Hub-ul în execuție a răspuns neinteligibil; nu editez starea sub un hub activ.", 502) from None
    except (URLError, OSError):
        listing = None
    if isinstance(listing, dict):
        pending = [row["device_id"] for row in listing.get("devices", []) if isinstance(row, dict) and row.get("status") == "pending"
                   and isinstance(row.get("device_id"), str)]
        for device_id in pending:
            data = json.dumps({"device_id": device_id}).encode("utf-8")
            try:
                with call(Request(base + "/hub/admin/devices/approve", data=data, headers=headers, method="POST"), timeout=5) as response:
                    response.read()
            except HTTPError as error:
                raise HubError("Hub-ul în execuție a refuzat aprobarea (HTTP " + str(error.code) + ").", error.code) from None
            except (URLError, OSError) as error:
                raise HubError("Hub-ul în execuție nu a mai răspuns la aprobare (" + type(error).__name__ + ").", 503) from None
        logger(str(len(pending)) + " dispozitive aprobate prin hub-ul în execuție (port " + str(port) + ")")
        return len(pending)
    path = Path(state) / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION or not isinstance(data.get("devices"), dict):
        logger("hub-ul nu rulează pe portul " + str(port) + " și " + str(path) + " lipsește sau nu are versiunea " + str(STATE_VERSION) + "; nimic de aprobat")
        return 0
    now = time.time()
    count = 0
    for device in data["devices"].values():
        if isinstance(device, dict) and device.get("status") == "pending":
            device.update(status="approved", approved_at=now, approved_by="cli")
            count += 1
    if count:
        temporary = path.with_name(path.name + ".part")
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        restrict_permissions(temporary)
        os.replace(temporary, path)
        restrict_permissions(path)
    logger(str(count) + " dispozitive aprobate direct în " + STATE_FILE + " (hub-ul nu rulează; pornește-l ca să preia starea)")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Hub central Studio Harness: dispozitive aprobate de admin, workspace-uri, claims și jurnal pentru toți developerii.")
    parser.add_argument("--listen", default="127.0.0.1",
                        help="Adresa de ascultare (implicit 127.0.0.1). Expunerea în rețea se cere explicit (--listen 0.0.0.0) și "
                             "doar în spatele unui proxy HTTPS: hub-ul vorbește HTTP simplu.")
    parser.add_argument("--port", type=int, default=34880)
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--show-admin-code", action="store_true", help="Afișează codul de administrator la pornire (implicit doar calea fișierului hub-admin-token).")
    parser.add_argument("--open-enrollment", action="store_true", help="Înrolare deschisă: dispozitivele noi sunt aprobate automat (persistat; panoul o poate schimba).")
    parser.add_argument("--approve-pending", action="store_true", help="Aprobă toate dispozitivele în așteptare (prin hub-ul în execuție sau direct în hub-state.json) și iese.")
    parser.add_argument("--no-auto-update", action="store_true", help="Nu verifica și nu aplica actualizări din canalul update-channel.json.")
    parser.add_argument("--inherit-socket", type=int, default=None,
                        help="(POSIX, intern) Descriptorul socketului de ascultare moștenit de la procesul vechi la relansarea fără întrerupere.")
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535:
        parser.error("Portul trebuie să fie între 1024 și 65535.")
    if options.inherit_socket is not None and options.inherit_socket < 0:
        parser.error("--inherit-socket cere un descriptor nenegativ.")
    state = options.state_dir or state_dir()
    from_env = os.environ.get(ADMIN_ENV) or ""
    admin_code = from_env or ensure_admin_token(state)
    if not valid_admin_code(admin_code):
        parser.error("Codul de administrator trebuie să aibă 16-512 de caractere, fără spații.")
    if options.approve_pending:
        try:
            approve_pending(state, options.port, admin_code)
        except HubError as error:
            log(str(error))
            return 1
        return 0
    hub = Hub(admin_code)
    state_path = Path(state) / STATE_FILE
    hub.load(state_path)
    if options.open_enrollment or os.environ.get(OPEN_ENROLLMENT_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        hub.enrollment = "open"
    try:
        server = HubServer(options.listen, options.port, hub, inherited_fd=options.inherit_socket)
    except OSError as error:
        if options.inherit_socket is None:
            raise
        log("socketul moștenit nu poate fi folosit (" + type(error).__name__ + "); ascult din nou pe port")
        server = HubServer(options.listen, options.port, hub)
    url = "http://" + options.listen + ":" + str(server.server_address[1])
    log("Studio Harness hub " + VERSION + " ascultă pe " + url + " (stare: " + str(state) + ")"
        + (" [socket moștenit, fără întrerupere]" if server.inherited else ""))
    if options.listen not in ("127.0.0.1", "::1", "localhost"):
        log("atenție: hub-ul ascultă în rețea pe HTTP simplu (" + options.listen + "); folosește-l doar în spatele unui proxy HTTPS")
    if from_env and len(admin_code) < RECOMMENDED_ADMIN_CODE:
        log("atenție: codul de administrator din " + ADMIN_ENV + " are sub " + str(RECOMMENDED_ADMIN_CODE)
            + " de caractere; folosește unul generat aleatoriu")
    if options.show_admin_code:
        log("Cod de administrator (pentru panou și --approve-pending): " + admin_code)
    elif from_env:
        log("Codul de administrator vine din " + ADMIN_ENV + " (pornește cu --show-admin-code ca să îl afișezi)")
    else:
        log("Codul de administrator: " + str(Path(state) / "hub-admin-token") + " (pornește cu --show-admin-code ca să îl afișezi)")
    log("înrolare: " + hub.enrollment + (" (dispozitivele noi sunt aprobate automat)" if hub.enrollment == "open"
                                        else " (dispozitivele noi așteaptă aprobarea adminului din panou sau --approve-pending)"))

    def stop(*_: Any) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    app_dir = Path(__file__).resolve().parent
    channel_root = app_dir if (app_dir / "update-channel.json").is_file() else app_dir.parent
    restart = {"requested": False, "exec": False}
    stop_saving = threading.Event()

    def save_state() -> None:
        try:
            hub.save(state_path)
        except (OSError, ValueError) as error:
            log("starea nu a putut fi salvată în " + STATE_FILE + ": " + type(error).__name__)

    def save_loop() -> None:
        while not stop_saving.wait(SAVE_INTERVAL):
            save_state()

    threading.Thread(target=save_loop, name="studio-harness-hub-state", daemon=True).start()

    def update_loop() -> None:
        time.sleep(30)
        while not restart["requested"]:
            try:
                settings = updater.channel(channel_root)
                upstream = settings.get("upstream_manifest_url") or settings.get("manifest_url")
                result = updater.check(channel_root, current=VERSION, manifest_url=upstream)
                if result["newer"] and result["auto"]:
                    # Întâi oglindim pachetele în /releases (developerii se actualizează de la acest hub), apoi ne actualizăm.
                    public = settings.get("manifest_url")
                    if public and public != upstream:
                        updater.mirror(result["manifest"], releases_dir(app_dir), public.rsplit("/", 1)[0])
                        log("canalul /releases oglindit pentru versiunea " + str(result["available"]))
                    data = updater.download(result["manifest"]["files"]["hub"])
                    updater.apply_bundle(data, app_dir, state / "backups", flatten="scripts/")
                    # POSIX: starea este salvată și procesul se relansează cu socketul moștenit (fără gap de port).
                    # Windows: fără execv sigur, hub-ul se închide și este repornit de systemd/docker/Popen cu starea persistată.
                    restart["exec"] = reexec_supported()
                    log("actualizare " + str(result["available"]) + " aplicată; "
                        + ("hub-ul se relansează fără întrerupere, cu socketul de ascultare moștenit" if restart["exec"]
                           else "hub-ul se închide ca să fie repornit de systemd/docker"))
                    restart["requested"] = True
                    server.shutdown()
                    return
                if result["error"]:
                    log("actualizare: " + result["error"])
            except (updater.UpdateError, KeyError) as error:
                log("actualizare eșuată: " + str(error))
            except Exception as error:
                log("actualizare: " + type(error).__name__)
            time.sleep(3600)

    auto = not options.no_auto_update and updater.channel(channel_root)["auto"] and updater.channel(channel_root)["manifest_url"]
    if auto and os.access(app_dir, os.W_OK) and not updater.is_git_checkout(channel_root):
        threading.Thread(target=update_loop, name="studio-harness-hub-update", daemon=True).start()
        log("actualizare automată: activă (verificare la fiecare oră)")
    else:
        log("actualizare automată: inactivă" + ("" if auto else " (canal neconfigurat sau dezactivat)") + ("" if os.access(app_dir, os.W_OK) else " (director doar-citire)"))
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        log("oprire cerută; hub-ul se închide")
    finally:
        stop_saving.set()
        save_state()
        if restart["exec"]:
            try:
                reexec(server, Path(__file__).resolve())
            except OSError as error:
                log("relansarea a eșuat (" + type(error).__name__ + "); hub-ul se închide ca să fie repornit de systemd/docker")
        server.server_close()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
