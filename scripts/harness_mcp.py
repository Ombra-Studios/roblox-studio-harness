"""Shim MCP stdio pentru CLI-ul din terminal (Mod T).

Pornește sau se atașează la daemon-ul local, deschide o sesiune `terminal` (jobul primește workspace-ul jocului deschis în
Studio, iar hub-ul central îl vede automat când dispozitivul este aprobat) și rutează cererile MCP spre `/agent/JOB/*`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# `python -I` nu adaugă directorul scriptului în sys.path pe Python 3.11+.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_mcp import serve  # noqa: E402
from local_state import ensure_local_token, state_dir
from process_tree import host_process_id

ROOT = Path(__file__).resolve().parent
PORT = 34871
BASE = f"http://127.0.0.1:{PORT}"
STARTUP_SECONDS = 25


def request(method: str, path: str, header: str, token: str, body: dict | None = None, timeout: float = 10) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    call = Request(BASE + path, data=data, method=method, headers={header: token, "Content-Type": "application/json"})
    with urlopen(call, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Răspuns invalid de la daemon.")
    return value


def daemon_status(token: str) -> dict | None:
    """None dacă nimic nu ascultă pe port; eroare explicită dacă ascultă altceva."""
    try:
        return request("GET", "/v1/status", "X-Studio-Harness-Token", token, timeout=5)
    except HTTPError as error:
        if error.code == 401:
            raise RuntimeError(f"Pe portul {PORT} rulează un daemon cu alt token local (un daemon pornit cu alt --state-dir sau un bridge vechi 0.3). "
                               "Închide-l și reîncearcă.") from None
        raise RuntimeError(f"Daemon-ul a răspuns HTTP {error.code}.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        return None


def start_daemon(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    log = open(state / "daemon.log", "ab")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, "-u", str(ROOT / "studio_bridge.py"), "--state-dir", str(state)],
                     cwd=str(ROOT.parent), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     creationflags=flags, close_fds=True, start_new_session=os.name != "nt")
    log.close()


def ensure_daemon(state: Path, token: str) -> dict:
    status = daemon_status(token)
    if status is None:
        start_daemon(state)
        deadline = time.monotonic() + STARTUP_SECONDS
        while status is None and time.monotonic() < deadline:
            time.sleep(0.4)
            status = daemon_status(token)
        if status is None:
            raise RuntimeError("Daemon-ul nu a pornit; vezi " + str(state / "daemon.log") + ".")
    version = str(status.get("version", ""))
    if not compatible_version(version):
        raise RuntimeError("Daemon-ul de pe port este versiunea " + version + "; închide-l și lasă CLI-ul să pornească versiunea curentă.")
    return status


def compatible_version(version: str) -> bool:
    """Daemon ≥ 1.0 (workspace-uri, hub central) sau 0.x ≥ 0.4 (contractul terminal/claims, neschimbat în 1.0).

    Doar `major.minor` contează; un sufix (`1.0.0-rc1`) este ignorat, iar orice altceva este refuzat.
    """
    match = re.match(r"(\d+)\.(\d+)(?:\.|-|$)", version) if isinstance(version, str) else None
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (0, 4)


def main() -> int:
    provider = os.environ.get("STUDIO_HARNESS_PROVIDER", "claude")
    if provider not in ("claude", "codex"):
        provider = "claude"
    state = state_dir()
    try:
        token = ensure_local_token(state)
        ensure_daemon(state, token)
        session = request("POST", "/v1/terminal/sessions", "X-Studio-Harness-Token", token,
                          {"provider": provider, "cwd": os.getcwd(), "host_pid": host_process_id()})
        job_id, job_token = session["job_id"], session["job_token"]
    except Exception as error:
        print("Studio Harness: " + str(error), file=sys.stderr, flush=True)
        return 1
    try:
        serve(sys.stdin, sys.stdout, config=(BASE, job_id, job_token))
    except (ValueError, OSError):
        return 1
    finally:
        try:
            request("POST", f"/v1/terminal/sessions/{job_id}/close", "X-Studio-Harness-Job", job_token, {}, timeout=3)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
