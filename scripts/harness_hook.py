"""Hook Claude Code și notify Codex: trimit promptul, textul asistentului și închiderea sesiunii către daemon.

Niciodată nu blochează CLI-ul: orice eroare este ignorată, ieșirea este mereu 0 și nu se scrie pe stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# `python -I` nu adaugă directorul scriptului în sys.path pe Python 3.11+.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_state import read_local_token, state_dir  # noqa: E402
from process_tree import host_process_id  # noqa: E402

BASE = "http://127.0.0.1:34871"
MAX_TEXT = 64000


def post(path: str, header: str, token: str, body: dict, timeout: float = 3) -> tuple[int, dict | None]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    call = Request(BASE + path, data=data, method="POST", headers={header: token, "Content-Type": "application/json"})
    try:
        with urlopen(call, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
            return response.status, value if isinstance(value, dict) else None
    except HTTPError as error:
        return error.code, None
    except Exception:
        return 0, None


def session_file(state: Path, key: str) -> Path:
    return state / "cli-sessions" / (re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120] + ".json")


def load_record(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("job_id"), str) else None


def save_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def register(state: Path, token: str, provider: str, cwd: str, key: str, host_names: tuple[str, ...]) -> dict | None:
    path = session_file(state, key)
    record = load_record(path)
    if record:
        return record
    status, response = post("/v1/terminal/sessions", "X-Studio-Harness-Token", token,
                            {"provider": provider, "cwd": cwd, "host_pid": host_process_id(host_names), "cli_session_id": key})
    if status != 200 or not response or not isinstance(response.get("job_id"), str):
        return None
    record = {"job_id": response["job_id"], "job_token": response.get("job_token", ""), "offset": 0}
    save_record(path, record)
    return record


def send_event(state: Path, key: str, record: dict, kind: str, text: str) -> bool:
    text = text.strip()
    if not text:
        return True
    status, _ = post(f"/v1/terminal/sessions/{record['job_id']}/events", "X-Studio-Harness-Job", record["job_token"],
                     {"type": kind, "text": text[:4 * MAX_TEXT]})
    if status in (401, 404, 409):
        # Daemon repornit sau sesiune închisă: înregistrarea locală nu mai este valabilă.
        try:
            session_file(state, key).unlink()
        except OSError:
            pass
        return False
    return status == 200


def assistant_texts(transcript_path: str | None, record: dict) -> list[str]:
    """Textul asistentului din transcriptul JSONL, de la ultimul offset citit."""
    if not transcript_path:
        return []
    offset = record.get("offset", 0) if isinstance(record.get("offset"), int) else 0
    try:
        with open(transcript_path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            if offset > size:
                offset = 0
            stream.seek(offset)
            chunk = stream.read(16 * 1024 * 1024)
    except OSError:
        return []
    end = chunk.rfind(b"\n")
    if end < 0:
        return []
    record["offset"] = offset + end + 1
    texts: list[str] = []
    for line in chunk[:end].split(b"\n"):
        try:
            entry = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        parts = [block["text"] for block in content
                 if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip()]
        if parts:
            texts.append("\n".join(parts))
    return texts


def handle_claude(payload: dict) -> None:
    event = payload.get("hook_event_name")
    key = payload.get("session_id")
    if not isinstance(key, str) or not key:
        return
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    state = state_dir()
    token = read_local_token(state)
    if not token:
        return
    record = register(state, token, "claude", cwd, key, ("claude.exe", "node.exe"))
    if not record:
        return
    if event == "UserPromptSubmit":
        prompt = payload.get("prompt")
        if isinstance(prompt, str):
            send_event(state, key, record, "prompt", prompt)
    elif event == "Stop":
        for text in assistant_texts(payload.get("transcript_path"), record):
            if not send_event(state, key, record, "text", text):
                return
        save_record(session_file(state, key), record)
    elif event == "SessionEnd":
        post(f"/v1/terminal/sessions/{record['job_id']}/close", "X-Studio-Harness-Job", record["job_token"], {})
        try:
            session_file(state, key).unlink()
        except OSError:
            pass


def handle_codex(argument: str) -> None:
    try:
        payload = json.loads(argument)
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    state = state_dir()
    token = read_local_token(state)
    if not token:
        return
    key = payload.get("thread-id") if isinstance(payload.get("thread-id"), str) else "codex-" + str(host_process_id(("codex.exe", "node.exe")) or os.getppid())
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    record = register(state, token, "codex", cwd, key, ("codex.exe", "node.exe"))
    if not record:
        return
    messages = payload.get("input-messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, str) and not send_event(state, key, record, "prompt", message):
                return
    last = payload.get("last-assistant-message")
    if isinstance(last, str):
        send_event(state, key, record, "text", last)


def main(argv: list[str]) -> int:
    try:
        if len(argv) >= 2 and argv[1] == "--codex":
            handle_codex(argv[2] if len(argv) > 2 else "{}")
        else:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
            if isinstance(payload, dict):
                handle_claude(payload)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
