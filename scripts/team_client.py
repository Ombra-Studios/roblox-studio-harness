"""Clientul hub al daemon-ului (1.0): înregistrarea dispozitivului, sync la 2 s, claims delegate hub-ului pe workspace și oglinda locală.

Numele fișierului rămâne `team_client.py` pentru continuitatea canalului de actualizare; clasa este `HubClient` (`TeamClient` = alias).

Un singur fir (`HubClient` este un `threading.Thread` daemon) parcurge stările din brief §2.4 / contract §4.1:

    connecting → pending (register la 15 s) → approved (sync la 2 s, 5 s când pluginul lipsește și nu rulează sesiuni din terminal)
               → offline (register cu backoff 2 → 4 → 8 → 16 → 30 s) → revoked (register la 60 s) → disabled (fără hub: niciun fir)

În orice stare diferită de `approved` firul reîncearcă `POST /hub/register`: hub-ul răspunde mereu cu starea dispozitivului, iar la
`approved` urmează imediat un sync. Un 401 `unknown` la sync (hub repornit fără stare) duce la reînregistrare imediată.

Deciziile de claims se iau în hub (`/hub/claims/*`, cu `workspace` = cheia jobului); citirile (`held_by`, `covers`, `related_to`,
`holder`, `snapshot`) folosesc oglinda adusă de sync, cu aceeași interfață ca `ClaimTable`. Când hub-ul nu este `approved`, apelurile
remote ridică `HubError` 503 fără să atingă rețeaua; daemon-ul folosește atunci o `ClaimTable` locală (contract §6.3).

Ce cere de la `bridge` (daemon-ul):

- `bridge_id`, `version`;
- `plugin_connected()` → bool (ritmul sync-ului);
- `hub_payload(pushed)` → sesiunile proprii cu evenimentele noi după cursoarele din `pushed` (dicționar actualizat pe loc);
- opțional `identity_payload()` → `{"roblox": {"user_id", "name"} | None, "workspace": meta | None}`, citit la fiecare ciclu; altfel
  identitatea vine doar prin `set_identity(roblox, workspace)`, care oricum trezește ciclul imediat;
- opțional `project_digest()`, `project_payload()` (harta proiectului, trimisă când hub-ul cere `want_project`);
- opțional `job_workspace(job_id)` → cheia workspace-ului jobului (altfel workspace-ul curent al daemon-ului);
- opțional `hub_state_changed(previous, current)` → apelat în afara lock-ului la fiecare schimbare de stare (reconcilierea claims-urilor
  locale la revenirea în `approved` este treaba daemon-ului).

Niciun token nu ajunge în `status()`, în rânduri, în mesaje de eroare sau în excepții: tokenul de dispozitiv trăiește doar în
închiderea funcției de transport.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claims import Claim, conflicts, display, normalize, related, within  # noqa: E402
from local_state import device_id_for  # noqa: E402

STATES = ("connecting", "pending", "approved", "offline", "revoked", "disabled")
TERMINAL_STATES = {"completed", "failed", "cancelled"}
# Sesiunile altora primesc de la hub și `lost` (dispozitiv offline); nu le mai cerem evenimente până revin.
REMOTE_TERMINAL_STATES = TERMINAL_STATES | {"lost"}
ENROLLMENT_MODES = ("approve", "open")
DEVICE_HEADER = "X-Studio-Harness-Device"

PENDING_INTERVAL = 15.0
REVOKED_INTERVAL = 60.0
BACKOFF_START = 2.0
BACKOFF_MAX = 30.0
REGISTER_TIMEOUT = 10.0
SYNC_TIMEOUT = 15.0
PROJECT_SYNC_TIMEOUT = 30.0
CLAIM_TIMEOUT = 10.0
WAIT_TIMEOUT = 75.0
FETCH_TIMEOUT = 15.0
FETCH_PROJECT_TIMEOUT = 30.0
MAX_WAIT_SECONDS = 60.0
MAX_SYNC_SESSIONS = 64
MAX_WANT = 64
MAX_REMOTE_EVENTS = 1024
MAX_JOURNAL = 500
MAX_PENDING_JOURNAL = 500
# Hub-ul acceptă cel mult 32 de intrări de jurnal pe sync (contract §3.4) și le ignoră tăcut pe următoarele: trimitem exact
# atâtea, iar restul rămân în coadă pentru sync-ul următor, ca nicio intrare să nu se piardă după o cădere a hub-ului.
MAX_SYNC_JOURNAL = 32
MAX_REASON = 200
MAX_MACHINE = 64
MAX_NAME = 64

HUB_DOWN = "Hub-ul nu răspunde; claims-urile sunt locale până revine."
HUB_UNREACHABLE = "Hub-ul nu răspunde."
HUB_OLD = "Hub-ul rulează o versiune mai veche."
# Un hub 0.8 la aceeași adresă nu cunoaște `/hub/*`: după server răspunde 404 (rută necunoscută), 405 (metoda nu este
# permisă pe calea aceea) sau 501. Toate înseamnă același lucru pentru noi: hub vechi, nu o pană de rețea.
OLD_HUB_STATUS = frozenset({404, 405, 501})
HUB_RESTARTED = "Hub-ul a fost repornit; reînregistrare."
HUB_INVALID = "Răspuns invalid de la hub."
HUB_DISABLED = "Hub dezactivat (daemon pornit cu --no-hub)."
WORKSPACE_FIELDS = ("key", "game_id", "place_id", "name", "creator_id", "creator_type")

_UNSET = object()


def unavailable(state: str) -> str:
    """Mesajul daemon-ului și al toolurilor `hub_*` când hub-ul nu este `approved` (contract §4.7, §6.3)."""
    return "Hub-ul nu este disponibil (stare: " + state + ")."


class HubError(RuntimeError):
    """Eroare de transport sau răspuns de eroare al hub-ului: `status` = codul HTTP (503 când hub-ul nu răspunde), `payload` = JSON-ul lui."""

    def __init__(self, message: str = HUB_DOWN, status: int = 503, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload if isinstance(payload, dict) else {}

    @property
    def unreachable(self) -> bool:
        """Rețea, timeout sau un proxy care răspunde 502/503/504 fără JSON: hub-ul nu a fost atins."""
        return self.status in (502, 503, 504) and not self.payload


TeamError = HubError


class _NoRedirect(HTTPRedirectHandler):
    """Fără redirectări: `urllib` copiază antetele cererii (deci și tokenul dispozitivului) către noua gazdă și acceptă și `http`.
    Un proxy greșit configurat sau un portal captiv ar primi astfel tokenul, eventual în clar. Aici un 30x rămâne `HTTPError`."""

    def redirect_request(self, *_: Any) -> None:
        return None


_OPENER = build_opener(_NoRedirect)


def http_request(url: str, device_token: str, method: str, path: str, body: dict[str, Any] | None = None,
                 timeout: float = 10.0) -> dict[str, Any]:
    """O cerere JSON către hub cu antetul dispozitivului; răspunsul (obiect JSON) sau `HubError` cu codul și JSON-ul hub-ului.

    Redirectările nu sunt urmate (hub-ul real nu redirectează `/hub/*`): un 30x ajunge la apelant ca `HubError` cu codul lui."""
    data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    headers = {DEVICE_HEADER: device_token, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        call = Request(url.rstrip("/") + path, data=data, method=method, headers=headers)
        with _OPENER.open(call, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except (ValueError, OSError):
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        message = payload.get("error") if isinstance(payload.get("error"), str) and payload.get("error") else None
        raise HubError(message or f"Hub-ul a răspuns HTTP {error.code}.", error.code, payload) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise HubError(HUB_DOWN, 503) from None
    try:
        value = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HubError(HUB_INVALID, 502, {"error": HUB_INVALID}) from None
    if not isinstance(value, dict):
        raise HubError(HUB_INVALID, 502, {"error": HUB_INVALID})
    return value


def _claim_from_row(row: dict[str, Any]) -> Claim | None:
    try:
        path = normalize(row["path"])
    except (KeyError, ValueError, TypeError):
        return None
    since = row.get("since")
    since = float(since) if isinstance(since, (int, float)) and not isinstance(since, bool) else 0.0
    return Claim(path, str(row.get("job_id", "")), str(row.get("developer", "")), since, str(row.get("reason", "")), since)


def _clean_roblox(value: Any) -> dict[str, Any] | None:
    """`{"user_id", "name"}` sau None; `user_id` 0 = identitate necunoscută, pe care hub-ul o primește ca `null` (contract §1.5)."""
    if not isinstance(value, dict):
        return None
    user_id = value.get("user_id")
    if type(user_id) is float and user_id.is_integer():
        user_id = int(user_id)
    if type(user_id) is not int or user_id <= 0:
        return None
    name = value.get("name")
    name = name.strip()[:MAX_NAME] if isinstance(name, str) else ""
    return {"user_id": user_id, "name": name or ("user_" + str(user_id))}


def _clean_workspace(value: Any) -> dict[str, Any] | None:
    """Meta workspace-ului (câmpurile din contract §2.2) sau None; cheia trebuie să fie text nevid."""
    if not isinstance(value, dict) or not isinstance(value.get("key"), str) or not value["key"]:
        return None
    return {field: copy.deepcopy(value[field]) for field in WORKSPACE_FIELDS if field in value}


def _workspace_key(meta: dict[str, Any] | None) -> str | None:
    return meta["key"] if meta else None


class HubClient(threading.Thread):
    """Clientul hub 1.0: fir de înregistrare/sync, backend de claims remote și oglinda pentru tablă, poll și tooluri."""

    def __init__(self, bridge: Any, hub_url: str | None, device_token: str | None, machine: str,
                 interval: float = 2.0, idle_interval: float = 5.0,
                 request: Callable[..., dict[str, Any]] | None = None, autostart: bool = True, *,
                 pending_interval: float = PENDING_INTERVAL, revoked_interval: float = REVOKED_INTERVAL,
                 backoff_start: float = BACKOFF_START, backoff_max: float = BACKOFF_MAX,
                 disabled_error: str | None = None, clock: Callable[[], float] = time.time):
        super().__init__(name="studio-harness-hub", daemon=True)
        self.bridge = bridge
        self.hub_url = hub_url.rstrip("/") if isinstance(hub_url, str) and hub_url else None
        self.machine = str(machine)[:MAX_MACHINE]
        self.interval = interval
        self.idle_interval = idle_interval
        self.pending_interval = pending_interval
        self.revoked_interval = revoked_interval
        self.backoff_start = backoff_start
        self.backoff_max = backoff_max
        self.clock = clock
        # Tokenul rămâne doar în închiderea transportului: nu este atribut al clientului și nu apare în status()/rânduri/erori.
        token = device_token if isinstance(device_token, str) else ""
        url = self.hub_url or ""
        self._request = request or (lambda method, path, body=None, timeout=10.0: http_request(url, token, method, path, body, timeout))
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self._wake = threading.Event()
        self.state = "disabled" if self.hub_url is None else "connecting"
        self.error: str | None = (disabled_error or HUB_DISABLED) if self.state == "disabled" else None
        self.hub_id: str | None = None
        self.device_id: str | None = device_id_for(token) if token else None
        self.enrollment: str | None = None
        self.last_sync: float | None = None
        self.registered = False
        self.roblox: dict[str, Any] | None = None
        self.workspace: dict[str, Any] | None = None
        self.hub_workspace: str | None = None
        self._sent_roblox: Any = _UNSET
        self._synced_workspace: Any = _UNSET
        self._backoff = backoff_start
        self._unknown_streak = 0
        self._just_approved = False
        self._transition: tuple[str, str] | None = None
        self._last_sessions: list[dict[str, Any]] = []
        self.pushed: dict[str, int] = {}
        self.pending_journal: deque[dict[str, Any]] = deque(maxlen=MAX_PENDING_JOURNAL)
        self.touch_pending: set[str] = set()
        self.held: dict[str, list[Claim]] = {}
        self.held_workspace: dict[str, str | None] = {}
        self.remote_sessions: dict[str, dict[str, Any]] = {}
        self.remote_events: dict[str, list[dict[str, Any]]] = {}
        self.claim_rows: list[dict[str, Any]] = []
        self.journal: dict[int, dict[str, Any]] = {}
        self.journal_cursor = 0
        self.members: list[dict[str, Any]] = []
        self.workspaces: list[dict[str, Any]] = []
        self.want_project = False
        # Digest-ul unui proiect refuzat de hub cu 413 (corp prea mare): nu îl retrimitem până la o scanare nouă, altfel
        # fiecare sync ar cădea la fel și dispozitivul nu ar mai sincroniza nimic. Oglindește `project_rejected` din hub.
        self._project_refused: str | None = None
        self.project_cache: dict[str, dict[str, Any]] = {}
        if autostart and self.state != "disabled":
            self.start()

    # ----- ciclu -----

    def stop(self) -> None:
        self.stop_event.set()
        self._wake.set()

    def wake(self) -> None:
        """Forțează un ciclu imediat (identitate/workspace schimbate, jurnal nou, proiect nou, sesiune nouă a unui coleg)."""
        self._wake.set()

    def run(self) -> None:
        while not self.stop_event.is_set():
            delay = self.step()
            if delay is None:
                return
            self._wake.wait(delay)
            self._wake.clear()

    def step(self) -> float | None:
        """Un ciclu: `register` când nu suntem `approved`, altfel `sync_once`; întoarce pauza până la următorul (None = fără hub)."""
        with self.lock:
            state = self.state
        if state == "disabled":
            return None
        try:
            if state == "approved":
                self.sync_once()
            else:
                self.register()
        except HubError:
            pass  # starea și mesajul au fost actualizate de _fail
        except Exception as error:  # noqa: BLE001 - bridge-ul a aruncat; firul nu se oprește, iar cauza apare în status().
            self._fail(HubError("Sincronizarea cu hub-ul a eșuat: " + type(error).__name__, 503, {"error": type(error).__name__}), "sync")
        connected = self._plugin_connected()
        with self.lock:
            state = self.state
            if state == "approved":
                if self._just_approved:
                    self._just_approved = False
                    return 0.0
                return self.interval if connected or self._terminal_open_locked() else self.idle_interval
            if state == "pending":
                return self.pending_interval
            if state == "revoked":
                return self.revoked_interval
            if state == "connecting":
                # Reînregistrare imediată după 401 `unknown`; dacă se repetă fără un sync reușit între ele, nu strângem hub-ul într-o buclă.
                return 0.0 if self._unknown_streak <= 1 else self.interval
            delay = self._backoff
            self._backoff = min(self._backoff * 2, self.backoff_max)
            return delay

    def _plugin_connected(self) -> bool:
        """Apelat în afara lock-ului: daemon-ul poate ține propriul lock când ne citește starea."""
        try:
            return bool(self.bridge.plugin_connected())
        except Exception:  # noqa: BLE001 - un bridge fără plugin_connected înseamnă doar ritm lent
            return False

    def _terminal_open_locked(self) -> bool:
        return any(row.get("kind") == "terminal" and row.get("state") not in TERMINAL_STATES for row in self._last_sessions)

    # ----- stări -----

    def _enter_locked(self, state: str, error: str | None) -> None:
        previous = self.state
        self.state = state
        self.error = error
        if state != "offline":
            self._backoff = self.backoff_start
        if state != previous:
            self._transition = (previous, state)

    def _notify(self) -> None:
        with self.lock:
            transition = self._transition
            self._transition = None
        if transition is None:
            return
        hook = getattr(self.bridge, "hub_state_changed", None)
        if callable(hook):
            try:
                hook(*transition)
            except Exception:  # noqa: BLE001 - un hook defect nu oprește firul hub-ului
                pass

    def _adopt_locked(self, payload: dict[str, Any]) -> None:
        """Reține `device_id`, `hub_id` și `enrollment` din orice răspuns al hub-ului; un `hub_id` nou înseamnă cursoare de la 0."""
        device_id = payload.get("device_id")
        if isinstance(device_id, str) and device_id:
            self.device_id = device_id
        hub_id = payload.get("hub_id")
        if isinstance(hub_id, str) and hub_id and hub_id != self.hub_id:
            self.hub_id = hub_id
            self._reset_cursors_locked()
        enrollment = payload.get("enrollment")
        if enrollment in ENROLLMENT_MODES:
            self.enrollment = enrollment

    def _reset_cursors_locked(self) -> None:
        self.pushed = {}
        self.remote_events = {}
        self.journal_cursor = 0
        self.want_project = False

    def _fail(self, error: HubError, phase: str) -> None:
        """Traduce o eroare în stare: `phase` este `register`, `sync` sau `call` (claims/fetch, unde 400/404/409/5xx nu schimbă starea)."""
        payload_status = error.payload.get("status")
        with self.lock:
            if error.status == 403 and payload_status in ("pending", "revoked"):
                self._adopt_locked(error.payload)
                self.registered = False
                self._enter_locked(payload_status, None)
            elif error.status == 401:
                self.registered = False
                if phase == "register":
                    # Un hub 0.8 refuză /hub/* cu 401 fără `status`; un hub 1.0 pune mereu `status: "unknown"`.
                    self._enter_locked("offline", HUB_OLD if "status" not in error.payload else str(error))
                else:
                    self._unknown_streak += 1
                    self._enter_locked("connecting", None)
            elif error.unreachable:
                self._enter_locked("offline", HUB_UNREACHABLE)
            elif phase != "call":
                self._enter_locked("offline", HUB_OLD if error.status in OLD_HUB_STATUS else str(error))
        self._notify()

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 10.0,
              phase: str = "call") -> dict[str, Any]:
        try:
            response = self._request(method, path, body, timeout)
        except HubError as error:
            self._fail(error, phase)
            raise
        if not isinstance(response, dict):
            error = HubError(HUB_INVALID, 502, {"error": HUB_INVALID})
            self._fail(error, phase)
            raise error
        return response

    def _require_approved(self) -> None:
        with self.lock:
            state = self.state
        if state != "approved":
            raise HubError(unavailable(state), 503, {"status": state})

    # ----- identitate -----

    def set_identity(self, roblox: dict[str, Any] | None, workspace: dict[str, Any] | None) -> None:
        """Identitatea Roblox și workspace-ul curent; o schimbare trezește ciclul (re-register sau sync imediat)."""
        cleaned = (_clean_roblox(roblox), _clean_workspace(workspace))
        with self.lock:
            changed = cleaned != (self.roblox, self.workspace)
            self.roblox, self.workspace = cleaned
        if changed:
            self.wake()

    def _identity(self) -> dict[str, Any]:
        """Identitatea curentă: din `bridge.identity_payload()` când există (sursa de adevăr rămâne daemon-ul), altfel cea setată."""
        payload = self._bridge("identity_payload")
        with self.lock:
            if isinstance(payload, dict):
                self.roblox, self.workspace = _clean_roblox(payload.get("roblox")), _clean_workspace(payload.get("workspace"))
            return {"roblox": copy.deepcopy(self.roblox), "workspace": copy.deepcopy(self.workspace)}

    def workspace_key(self) -> str | None:
        with self.lock:
            return _workspace_key(self.workspace)

    def _bridge(self, name: str, *arguments: Any) -> Any:
        method = getattr(self.bridge, name, None)
        return method(*arguments) if callable(method) else None

    def _workspace_of(self, job_id: str | None) -> str | None:
        """Cheia workspace-ului unui job (`bridge.job_workspace`), altfel workspace-ul curent al daemon-ului."""
        if job_id is not None:
            key = self._bridge("job_workspace", job_id)
            if isinstance(key, str) and key:
                return key
        return self.workspace_key()

    # ----- înregistrare și sync -----

    def register(self) -> dict[str, Any]:
        """`POST /hub/register`: 200 → approved (sync imediat), 202 → pending, 403 revoked → revoked, altfel offline."""
        identity = self._identity()
        body = {"roblox": identity["roblox"], "machine": self.machine, "bridge_id": getattr(self.bridge, "bridge_id", ""),
                "version": str(getattr(self.bridge, "version", "")), "workspace": identity["workspace"]}
        response = self._call("POST", "/hub/register", body, timeout=REGISTER_TIMEOUT, phase="register")
        status = response.get("status")
        with self.lock:
            self._adopt_locked(response)
            self._sent_roblox = identity["roblox"]
            if status == "approved":
                self.registered = True
                self._just_approved = True
                self._enter_locked("approved", None)
            elif status == "pending":
                self.registered = False
                self._enter_locked("pending", None)
            else:
                self.registered = False
                self._enter_locked("offline", HUB_INVALID)
        self._notify()
        if status not in ("approved", "pending"):
            raise HubError(HUB_INVALID, 502, response)
        return response

    def sync_once(self) -> dict[str, Any]:
        """Un sync complet (contract §3.4 `/hub/sync`): trimite sesiunile proprii, jurnalul și cursoarele; primește oglinda workspace-ului."""
        with self.lock:
            ready = self.registered and self.state == "approved"
        if not ready:
            self.register()
            with self.lock:
                state = self.state
            if state != "approved":
                raise HubError(unavailable(state), 503, {"status": state})
        identity = self._identity()
        key = _workspace_key(identity["workspace"])
        with self.lock:
            pushed = dict(self.pushed)
            if self._synced_workspace is not _UNSET and key != self._synced_workspace:
                # Workspace nou: cerem din nou ultimele 50 de intrări de jurnal (ale noului workspace), nu doar ce e mai nou decât cursorul.
                self.journal_cursor = 0
        sessions = self.bridge.hub_payload(pushed)
        sessions = [row for row in sessions if isinstance(row, dict)][:MAX_SYNC_SESSIONS] if isinstance(sessions, list) else []
        digest = self._bridge("project_digest")
        digest = digest if isinstance(digest, str) and digest else None
        with self.lock:
            journal = [self.pending_journal.popleft() for _ in range(min(MAX_SYNC_JOURNAL, len(self.pending_journal)))]
            touched = sorted(self.touch_pending)
            self.touch_pending.clear()
            want = {job_id: (events[-1]["seq"] if events else 0) for job_id, events in self.remote_events.items()}
            for job_id, row in self.remote_sessions.items():
                if row.get("state") not in REMOTE_TERMINAL_STATES:
                    want.setdefault(job_id, 0)
            want = dict(list(want.items())[:MAX_WANT])
            body: dict[str, Any] = {"workspace": identity["workspace"], "sessions": sessions, "want": want, "journal": journal,
                                    "journal_after": self.journal_cursor, "touch": touched, "project_digest": digest}
            if identity["roblox"] != self._sent_roblox:
                body["roblox"] = identity["roblox"]
            send_project = self.want_project and digest is not None and digest != self._project_refused
        if send_project:
            project = self._bridge("project_payload")
            if isinstance(project, dict):
                # O singură dată per cerere a hub-ului; dacă tot nu îl are, îl cere din nou la sync-ul următor.
                body["project"] = project
        try:
            response = self._call("POST", "/hub/sync", body, timeout=PROJECT_SYNC_TIMEOUT if "project" in body else SYNC_TIMEOUT, phase="sync")
        except HubError as error:
            with self.lock:
                self.pending_journal.extendleft(reversed(journal))
                self.touch_pending.update(touched)
                if error.status == 413 and "project" in body:
                    self._project_refused = digest
            raise
        restarted = False
        with self.lock:
            hub_id = response.get("hub_id")
            if isinstance(hub_id, str) and hub_id and self.hub_id is not None and hub_id != self.hub_id:
                # Hub repornit cu altă identitate: ne reînregistrăm și retrimitem evenimentele de la cursor 0.
                self.hub_id = hub_id
                self.registered = False
                self._reset_cursors_locked()
                self._enter_locked("connecting", None)
                restarted = True
            else:
                self._apply_sync_locked(response, pushed, sessions, identity, key, digest)
        self._notify()
        if restarted:
            raise HubError(HUB_RESTARTED, 401, {"status": "unknown"})
        with self.lock:
            more = bool(self.pending_journal)
        if more:
            # Coada a depășit plafonul de 32 pe sync: restul pleacă imediat, nu la intervalul următor.
            self.wake()
        return response

    def _apply_sync_locked(self, response: dict[str, Any], pushed: dict[str, int], sessions: list[dict[str, Any]],
                           identity: dict[str, Any], key: str | None, digest: str | None) -> None:
        self._adopt_locked(response)
        self.pushed = pushed
        self._last_sessions = sessions
        self._sent_roblox = identity["roblox"]
        self._synced_workspace = key
        self._unknown_streak = 0
        self.last_sync = self.clock()
        device = response.get("device")
        device_status = device.get("status") if isinstance(device, dict) else None
        if device_status in ("pending", "revoked"):
            self.registered = False
            self._enter_locked(device_status, None)
        else:
            self._enter_locked("approved", None)
        self.hub_workspace = response.get("workspace") if isinstance(response.get("workspace"), str) else None
        self.want_project = response.get("want_project") is True
        if self.want_project and digest:
            # Hub-ul nu are snapshot-ul nostru: trimitem proiectul imediat, nu la următorul ciclu programat.
            self._wake.set()
        self.members = [row for row in response.get("members", []) if isinstance(row, dict)] if isinstance(response.get("members"), list) else []
        workspaces = response.get("workspaces")
        self.workspaces = [row for row in workspaces if isinstance(row, dict) and isinstance(row.get("key"), str)] if isinstance(workspaces, list) else []
        remote: dict[str, dict[str, Any]] = {}
        for row in response.get("sessions", []) if isinstance(response.get("sessions"), list) else []:
            if isinstance(row, dict) and isinstance(row.get("job_id"), str) and row["job_id"]:
                row["remote"] = True
                remote[row["job_id"]] = row
        # O sesiune nouă a unui coleg: cerem evenimentele ei imediat, nu la următorul ciclu.
        if any(job_id not in self.remote_events and row.get("state") not in REMOTE_TERMINAL_STATES for job_id, row in remote.items()):
            self._wake.set()
        self.remote_sessions = remote
        for job_id in list(self.remote_events):
            if job_id not in remote:
                del self.remote_events[job_id]
        events = response.get("events")
        for job_id, rows in (events.items() if isinstance(events, dict) else []):
            if not isinstance(rows, list) or job_id not in remote:
                continue
            store = self.remote_events.setdefault(job_id, [])
            last = store[-1]["seq"] if store else 0
            for event in rows:
                if isinstance(event, dict) and type(event.get("seq")) is int and event["seq"] > last:
                    store.append(event)
                    last = event["seq"]
            if len(store) > MAX_REMOTE_EVENTS:
                del store[:len(store) - MAX_REMOTE_EVENTS]
        claims = response.get("claims")
        self.claim_rows = [row for row in claims if isinstance(row, dict)] if isinstance(claims, list) else []
        scope = self.hub_workspace or key
        # Claims-urile jobului dintr-un alt workspace (Studio a schimbat jocul) rămân cunoscute din răspunsurile claim/touch.
        self.held = {job_id: rows for job_id, rows in self.held.items() if self.held_workspace.get(job_id) not in (None, scope)}
        self.held_workspace = {job_id: workspace for job_id, workspace in self.held_workspace.items() if job_id in self.held}
        for row in self.claim_rows:
            claim = _claim_from_row(row)
            if claim and claim.job_id:
                self.held.setdefault(claim.job_id, []).append(claim)
                self.held_workspace[claim.job_id] = row.get("workspace") if isinstance(row.get("workspace"), str) else scope
        journal = response.get("journal")
        for row in journal if isinstance(journal, list) else []:
            if isinstance(row, dict) and type(row.get("seq")) is int:
                self.journal[row["seq"]] = row
        if len(self.journal) > MAX_JOURNAL:
            for seq in sorted(self.journal)[:len(self.journal) - MAX_JOURNAL]:
                del self.journal[seq]
        journal_seq = response.get("journal_seq")
        if type(journal_seq) is int and journal_seq > self.journal_cursor:
            self.journal_cursor = journal_seq

    # ----- oglinda pentru tablă, poll și tooluri -----

    def status(self) -> dict[str, Any]:
        """`hub` din `/v1/status` (contract §4.3): fără token, `device_id` = id-ul hub-ului (derivat local până la primul răspuns)."""
        with self.lock:
            return {"url": self.hub_url, "status": self.state, "hub_id": self.hub_id, "device_id": self.device_id,
                    "error": self.error, "last_sync": self.last_sync, "enrollment": self.enrollment}

    def remote_rows(self) -> list[dict[str, Any]]:
        """Sesiunile altora din workspace-ul curent (`remote: true`, `mine: false`)."""
        with self.lock:
            return [dict(row, mine=False) for row in self.remote_sessions.values()]

    def member_rows(self) -> list[dict[str, Any]]:
        """Dispozitivele prezente în workspace-ul curent, cu `me` pentru dispozitivul propriu."""
        with self.lock:
            return [dict(row, me=row.get("device_id") == self.device_id) for row in self.members]

    def workspace_rows(self) -> list[dict[str, Any]]:
        """Sumarul tuturor workspace-urilor (formatul hub-ului, `members_online` listă) cu `mine` pe cheia curentă a daemon-ului."""
        with self.lock:
            current = _workspace_key(self.workspace)
            return [dict(copy.deepcopy(row), mine=row.get("key") == current) for row in self.workspaces]

    def journal_rows(self, limit: int, workspace: str | None = None) -> list[dict[str, Any]]:
        """Ultimele `limit` intrări din oglindă, opțional doar cele ale unui workspace."""
        with self.lock:
            rows = [self.journal[seq] for seq in sorted(self.journal)]
        if workspace is not None:
            rows = [row for row in rows if row.get("workspace") == workspace]
        return [dict(row) for row in rows[-max(0, limit):]] if limit > 0 else []

    def record(self, entry: dict[str, Any]) -> None:
        """O intrare de jurnal locală (`{time, job_id, provider, tool, paths, summary, workspace}`), trimisă la sync-ul următor."""
        with self.lock:
            self.pending_journal.append(dict(entry))
        self.wake()

    def is_remote(self, job_id: str) -> bool:
        with self.lock:
            return job_id in self.remote_sessions

    def remote_snapshot(self, job_id: str, after: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.remote_sessions.get(job_id)
            if not row:
                return None
            store = self.remote_events.get(job_id, [])
            events = [dict(event) for event in store if event["seq"] > after]
            last = store[-1]["seq"] if store else after
            return {"ok": True, "job_id": job_id, "state": row.get("state", "running"), "events": events,
                    "last_seq": max(last, after), "remote": True}

    # ----- apeluri sincrone pentru daemon (proxy și tooluri) -----

    def fetch_workspaces(self) -> dict[str, Any]:
        """Răspunsul `GET /hub/workspaces` (`{"ok", "workspaces": [...]}`), pentru `/v1/hub/workspaces`."""
        self._require_approved()
        response = self._call("GET", "/hub/workspaces", timeout=FETCH_TIMEOUT)
        if not isinstance(response.get("workspaces"), list):
            raise HubError(HUB_INVALID, 502, response)
        return response

    def fetch_workspace(self, key: str) -> dict[str, Any]:
        """Răspunsul `GET /hub/workspace?key=` (404 `HubError` când cheia nu există), pentru `/v1/hub/workspace`."""
        if not isinstance(key, str) or not key:
            raise HubError("Cheia workspace-ului lipsește.", 400)
        self._require_approved()
        return self._call("GET", "/hub/workspace?key=" + quote(key, safe=""), timeout=FETCH_TIMEOUT)

    def fetch_project(self, key: str) -> dict[str, Any] | None:
        """Proiectul complet al unui workspace din hub (None dacă hub-ul are doar sumarul); păstrat local cât timp `digest` nu se schimbă."""
        if not isinstance(key, str) or not key:
            raise HubError("Cheia workspace-ului lipsește.", 400)
        with self.lock:
            row = next((row for row in self.workspaces if row.get("key") == key), None)
            summary = row.get("project") if row and isinstance(row.get("project"), dict) else None
            digest = summary.get("digest") if summary and isinstance(summary.get("digest"), str) else None
            cached = self.project_cache.get(key)
        if cached and (digest is None or cached.get("snapshot_id") == digest):
            return cached
        self._require_approved()
        response = self._call("GET", "/hub/project?key=" + quote(key, safe=""), timeout=FETCH_PROJECT_TIMEOUT)
        project = response.get("project")
        if not isinstance(project, dict) or not isinstance(project.get("groups"), dict):
            return None
        with self.lock:
            # Un singur proiect complet în memorie: cel cerut ultima dată (poate avea câțiva MB).
            self.project_cache = {key: project}
        return project

    # ----- claims (aceeași interfață ca ClaimTable, decisă în hub per workspace) -----

    def _refresh_held(self, job_id: str, held: Any, developer: str, workspace: str | None) -> None:
        now = self.clock()
        rows = [{"path": path, "job_id": job_id, "developer": developer, "since": now} for path in (held if isinstance(held, list) else [])
                if isinstance(path, str)]
        with self.lock:
            self.held[job_id] = [claim for claim in (_claim_from_row(row) for row in rows) if claim]
            self.held_workspace[job_id] = workspace

    def _developer_of(self, job_id: str) -> str:
        with self.lock:
            claims = self.held.get(job_id)
            if claims:
                return claims[0].developer
            return self.roblox["name"] if self.roblox else self.machine

    def claim(self, job_id: str, developer: str, paths: list[Any], reason: str = "", *,
              workspace: str | None = None) -> tuple[list[Claim], list[dict[str, Any]]]:
        """Toate sau nimic, decis în hub; întoarce (claims deținute de job, conflicte) exact ca `ClaimTable.claim`."""
        normalized: list[str] = []
        for path in paths:
            candidate = display(normalize(path))
            if candidate not in normalized:
                normalized.append(candidate)
        if not normalized:
            raise ValueError("Este necesară cel puțin o cale.")
        if not isinstance(reason, str):
            raise ValueError("reason trebuie să fie text.")
        self._require_approved()
        scope = workspace or self._workspace_of(job_id)
        body: dict[str, Any] = {"job_id": job_id, "paths": normalized, "reason": reason[:MAX_REASON]}
        if scope:
            body["workspace"] = scope
        try:
            response = self._call("POST", "/hub/claims/claim", body, timeout=CLAIM_TIMEOUT)
        except HubError as error:
            if error.status == 409 and isinstance(error.payload.get("conflicts"), list):
                return [], [row for row in error.payload["conflicts"] if isinstance(row, dict)]
            raise
        self._refresh_held(job_id, response.get("held"), developer, scope)
        return self.held_by(job_id), []

    def release(self, job_id: str, paths: list[Any] | None = None, *, workspace: str | None = None) -> list[Claim]:
        self._require_approved()
        scope = workspace or self._workspace_of(job_id)
        body: dict[str, Any] = {"job_id": job_id}
        if paths is not None:
            body["paths"] = [display(normalize(path)) for path in paths]
        if scope:
            body["workspace"] = scope
        before = self.held_by(job_id)
        developer = before[0].developer if before else self._developer_of(job_id)
        response = self._call("POST", "/hub/claims/release", body, timeout=CLAIM_TIMEOUT)
        released_names = {name for name in (response.get("released") if isinstance(response.get("released"), list) else []) if isinstance(name, str)}
        self._refresh_held(job_id, response.get("held"), developer, scope)
        released = [claim for claim in before if display(claim.path) in released_names]
        if not released and released_names:
            released = [claim for claim in (_claim_from_row({"path": name, "job_id": job_id, "developer": developer}) for name in sorted(released_names)) if claim]
        return released

    def touch(self, job_id: str, *, workspace: str | None = None) -> None:
        self._require_approved()
        scope = workspace or self._workspace_of(job_id)
        body: dict[str, Any] = {"job_id": job_id}
        if scope:
            body["workspace"] = scope
        response = self._call("POST", "/hub/claims/touch", body, timeout=CLAIM_TIMEOUT)
        self._refresh_held(job_id, response.get("held"), self._developer_of(job_id), scope)

    def held_by(self, job_id: str) -> list[Claim]:
        with self.lock:
            return list(self.held.get(job_id, []))

    def covers(self, job_id: str, path: tuple[str, ...]) -> bool:
        return any(within(path, claim.path) for claim in self.held_by(job_id))

    def related_to(self, job_id: str, path: tuple[str, ...]) -> bool:
        return any(related(path, claim.path) for claim in self.held_by(job_id))

    def holder(self, path: tuple[str, ...], exclude_job: str | None = None, *, workspace: str | None = None) -> Claim | None:
        """Claim-ul altui job care se bate cu `path`, în workspace-ul dat (implicit al lui `exclude_job`, altfel cel curent)."""
        scope = workspace
        if scope is None:
            with self.lock:
                scope = self.held_workspace.get(exclude_job) if exclude_job in self.held_workspace else None
            if scope is None:
                scope = self._workspace_of(exclude_job) or self.hub_workspace
        with self.lock:
            for job_id, claims in self.held.items():
                if job_id == exclude_job or self.held_workspace.get(job_id) not in (None, scope):
                    continue
                for claim in claims:
                    if conflicts(path, claim.path):
                        return claim
        return None

    def wait_free(self, path: tuple[str, ...], timeout: float, cancelled: threading.Event | None = None, *,
                  workspace: str | None = None) -> bool:
        """Ține conexiunea în hub (≤ 60 s per apel) până când calea devine liberă în workspace, expiră `timeout` sau jobul e anulat."""
        self._require_approved()
        scope = workspace or self.workspace_key()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (cancelled is not None and cancelled.is_set()):
                return False
            body: dict[str, Any] = {"path": display(path), "timeout_seconds": min(remaining, MAX_WAIT_SECONDS)}
            if scope:
                body["workspace"] = scope
            response = self._call("POST", "/hub/claims/wait", body, timeout=WAIT_TIMEOUT)
            if response.get("free") is True:
                return True

    def snapshot(self) -> list[dict[str, Any]]:
        """Claims-urile workspace-ului curent, așa cum le-a dat hub-ul la ultimul sync (cu `workspace` pe fiecare rând)."""
        with self.lock:
            return [dict(row) for row in self.claim_rows]

    def expire(self) -> list[Claim]:
        """Expirarea (600 s fără touch) o face hub-ul; local nu există nimic de expirat."""
        return []


TeamClient = HubClient
