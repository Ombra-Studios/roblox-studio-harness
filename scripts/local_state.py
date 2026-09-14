"""Stare locală per mașină (1.0): directorul de stare, codul local UI, token-ul de dispozitiv, codul de administrator și adresa hub-ului.

Fișiere în `state_dir()` (`%LOCALAPPDATA%\\StudioHarness\\` pe Windows, `~/.local/state/studio-harness` pe Linux):

- `local-token`      token UI plugin↔daemon (16–512 caractere URL-safe), injectat în pluginul instalat;
- `device-token`     64 hex generat local; identifică PC-ul la hub, care reține doar `sha256(token)`;
- `hub-admin-token`  codul de administrator, creat de hub la prima pornire (doar pe server);
- `config.json`      opțional, `{"hub_url": "https://…"}` pentru self-hosting; env `STUDIO_HARNESS_HUB_URL` are prioritate.

Fără echipe: `team.json`, `ensure_team_token`, `read_team` și `developer_name` au dispărut în 1.0 (identitatea vine de la contul Roblox).
Funcțiile întorc valorile; apelanții nu le scriu niciodată în log-uri.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

DEFAULT_HUB_URL = "https://lostcube.pro/roblox/harness"
HUB_URL_ENV = "STUDIO_HARNESS_HUB_URL"
MAX_HUB_URL = 512
# Singurele gazde acceptate fără TLS: hub-ul de dezvoltare/test de pe același PC.
LOCAL_HTTP_HOSTS = frozenset({"127.0.0.1", "localhost"})

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-]{16,512}")
DEVICE_TOKEN_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
DEVICE_ID_LENGTH = 16


def state_dir(environ: dict[str, str] | None = None, platform: str | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("STUDIO_HARNESS_STATE_DIR")
    if override:
        return Path(override)
    if (platform or os.name) == "nt":
        local = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "StudioHarness"
    # Linux/macOS (hub-ul găzduit pe server): XDG_STATE_HOME sau ~/.local/state.
    base = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local" / "state")
    return Path(base) / "studio-harness"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _new_device_token() -> str:
    return secrets.token_hex(32)


def restrict_permissions(path: Path) -> None:
    """Pe POSIX fișierul devine 0600 (doar proprietarul); pe Windows ACL-ul din %LOCALAPPDATA% este deja per utilizator.

    Public, ca și hub-ul să îl poată folosi pentru `hub-state.json` (token-uri hash-uite, identități, conținut de sesiuni)."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass



def ensure_token_file(path: Path, pattern: re.Pattern[str] = TOKEN_PATTERN, generate: Callable[[], str] = _new_token) -> str:
    """Întoarce token-ul din `path` sau îl creează o singură dată (atomic la concurență). Un conținut care nu respectă `pattern` este înlocuit."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            text = None
        except UnicodeDecodeError:
            text = ""
        if text is not None and pattern.fullmatch(text):
            return text
        candidate = generate()
        try:
            mode = "w" if text is not None else "x"
            with path.open(mode, encoding="utf-8") as stream:
                stream.write(candidate)
            restrict_permissions(path)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"Fișierul {path.name} nu poate fi creat în {directory}.")


def _read_token(path: Path, pattern: re.Pattern[str]) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return text if pattern.fullmatch(text) else None


def ensure_local_token(directory: Path) -> str:
    """Codul local UI este creat o singură dată și refolosit de daemon, shim, hook-uri și pluginul instalat."""
    return ensure_token_file(directory / "local-token")


def read_local_token(directory: Path) -> str | None:
    return _read_token(directory / "local-token", TOKEN_PATTERN)


def ensure_device_token(directory: Path) -> str:
    """Token-ul de dispozitiv (64 hex), creat o singură dată per PC; hub-ul îl ține în așteptare până îl aprobă adminul."""
    return ensure_token_file(directory / "device-token", DEVICE_TOKEN_PATTERN, _new_device_token)


def read_device_token(directory: Path) -> str | None:
    return _read_token(directory / "device-token", DEVICE_TOKEN_PATTERN)


def device_id_for(token: str) -> str:
    """`device_id` derivat din token exact ca în hub (`sha256(token)[:16]`), ca daemon-ul să îl poată afișa înainte de răspunsul hub-ului."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:DEVICE_ID_LENGTH]


def ensure_admin_token(directory: Path) -> str:
    """Codul de administrator al hub-ului, creat o singură dată în `hub-admin-token`; se afișează doar la `--show-admin-code`."""
    return ensure_token_file(directory / "hub-admin-token")


def read_config(directory: Path) -> dict:
    """`config.json` ca dicționar; fișier lipsă, JSON invalid sau alt tip decât obiect → `{}`."""
    try:
        value = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_config(directory: Path, changes: dict) -> dict:
    """Scrie `config.json` cu cheile date peste cele existente (scriere atomică, permisiuni restrânse ca la tokenuri).

    Cheile cu valoarea None se șterg, ca o setare revenită la implicit să nu rămână scrisă în fișier."""
    config = read_config(directory)
    for key, value in changes.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "config.json.part"
    body = json.dumps(config, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    temporary.write_bytes(body.encode("utf-8"))
    restrict_permissions(temporary)
    os.replace(temporary, directory / "config.json")
    restrict_permissions(directory / "config.json")
    return config


def normalize_hub_url(value: object) -> str | None:
    """Adresa hub-ului fără slash final, sau None dacă nu este acceptată.

    Acceptat: `https://` către orice gazdă, `http://` doar către 127.0.0.1/localhost (hub local de test); fără utilizator:parolă,
    query, fragment sau spații. Schema este normalizată la litere mici.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_HUB_URL or "?" in text or "#" in text:
        return None
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in text):
        return None
    try:
        parts = urlsplit(text)
        host, port = parts.hostname, parts.port  # `port` ridică ValueError când nu este numeric sau depășește 65535
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or port == 0 or parts.username is not None or parts.password is not None:
        return None
    if parts.scheme == "http" and host not in LOCAL_HTTP_HOSTS:
        return None
    return parts.scheme + "://" + parts.netloc + parts.path.rstrip("/")


def resolve_hub_url(directory: Path, environ: dict[str, str] | None = None) -> tuple[str | None, str]:
    """`(hub_url, sursă)`, cu sursa `env`, `config` sau `default`.

    Prima valoare setată (nevidă) câștigă: env `STUDIO_HARNESS_HUB_URL`, apoi `hub_url` din `config.json`, apoi `DEFAULT_HUB_URL`.
    O valoare setată dar neacceptată dă `None` (daemon-ul trece în `disabled`), nu implicitul public: nu trimitem date unde nu s-a cerut.
    """
    env = os.environ if environ is None else environ
    candidates = (("env", env.get(HUB_URL_ENV)), ("config", read_config(directory).get("hub_url")))
    for source, candidate in candidates:
        if candidate is None or (isinstance(candidate, str) and not candidate.strip()):
            continue
        return normalize_hub_url(candidate), source
    return DEFAULT_HUB_URL, "default"


def hub_url(directory: Path, environ: dict[str, str] | None = None) -> str | None:
    """Adresa hub-ului (env → config.json → DEFAULT_HUB_URL), validată și fără slash final; None dacă valoarea setată nu este acceptată."""
    return resolve_hub_url(directory, environ)[0]
