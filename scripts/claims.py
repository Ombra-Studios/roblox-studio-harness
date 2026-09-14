"""Claims exclusive pe subarbori ai scenei. Model pur: fără rețea, fără Studio."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

CLAIM_IDLE_SECONDS = 600
MAX_SEGMENTS = 64
MAX_LENGTH = 512
SPECIAL = ("@play", "@all")
CHECKED_ROOTS = frozenset({
    "Workspace", "ReplicatedStorage", "ServerScriptService", "ServerStorage", "StarterGui", "StarterPack",
    "StarterPlayer", "Lighting", "ReplicatedFirst", "SoundService", "Teams", "Chat", "TextChatService",
    "MaterialService", "Players",
})
_LITERAL = re.compile(r"\b(game|workspace)((?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+)")
_SERVICE = re.compile(r"\bgame\s*:\s*GetService\s*\(\s*[\"']([A-Za-z]+)[\"']\s*\)((?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

Path = tuple[str, ...]


def normalize(path: Any) -> Path:
    if not isinstance(path, str):
        raise ValueError("Calea unui claim trebuie să fie text.")
    text = path.strip()
    if not text or len(text) > MAX_LENGTH or _CONTROL.search(text):
        raise ValueError("Calea este goală, prea lungă sau conține caractere de control.")
    if text.startswith("@"):
        head, _, tail = text.partition(":")
        if head not in SPECIAL:
            raise ValueError(f"Claim special necunoscut: {head}")
        return (head, tail) if tail else (head,)
    segments = [segment.strip() for segment in text.replace("/", ".").split(".")]
    if segments and segments[0] == "game":
        segments = segments[1:]
    if not segments or any(not segment for segment in segments) or len(segments) > MAX_SEGMENTS:
        raise ValueError(f"Cale invalidă: {text}")
    if segments[0].lower() == "workspace":
        segments[0] = "Workspace"
    return tuple(segments)


def display(path: Path) -> str:
    return ":".join(path) if path[0].startswith("@") else ".".join(path)


def conflicts(first: Path, second: Path) -> bool:
    if first[0] == "@all" or second[0] == "@all":
        return True
    if first[0].startswith("@") or second[0].startswith("@"):
        return first == second
    shared = min(len(first), len(second))
    return first[:shared] == second[:shared]


def within(path: Path, claim: Path) -> bool:
    """`path` este claim-ul însuși sau un descendent al lui."""
    if claim[0] == "@all":
        return True
    if claim[0].startswith("@") or path[0].startswith("@"):
        return path == claim
    return len(path) >= len(claim) and path[:len(claim)] == claim


def related(path: Path, claim: Path) -> bool:
    """`path` este în claim sau este un strămoș al claim-ului (folosit pentru literalele din Luau)."""
    if within(path, claim):
        return True
    if claim[0].startswith("@") or path[0].startswith("@"):
        return False
    return len(path) < len(claim) and claim[:len(path)] == path


def luau_literals(code: str) -> list[Path]:
    """Căile literale `game.A.B`, `workspace.A` și `game:GetService("X").Y` din codul Luau.

    Detecția este best-effort, nu o barieră de securitate: previne greșeli („am uitat să revendic Workspace.Map”), nu ocoliri
    intenționate. Indexarea cu paranteze (`game.Workspace["Map"]`), `FindFirstChild("Workspace")`, o variabilă intermediară sau
    un `GetService` cu argument calculat nu sunt văzute. Claims-urile sunt oricum self-service (`@all` se poate cere oricând),
    deci nu constituie o graniță de încredere (contract §7)."""
    found: list[Path] = []

    def chain(text: str) -> list[str]:
        return [segment for segment in re.split(r"\s*\.\s*", text.strip()) if segment]

    for match in _LITERAL.finditer(code):
        segments = chain(match.group(2))
        if match.group(1) == "workspace":
            segments = ["Workspace"] + segments
        if segments and segments[0] in CHECKED_ROOTS:
            found.append(tuple(segments))
    for match in _SERVICE.finditer(code):
        segments = [match.group(1)] + chain(match.group(2))
        if segments[0] in CHECKED_ROOTS:
            found.append(tuple(segments))
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


@dataclass
class Claim:
    path: Path
    job_id: str
    developer: str
    since: float
    reason: str
    last_touch: float

    def describe(self, idle_seconds: float) -> dict[str, Any]:
        return {"path": display(self.path), "job_id": self.job_id, "developer": self.developer,
                "since": self.since, "reason": self.reason, "expires": self.last_touch + idle_seconds}


class ClaimTable:
    def __init__(self, idle_seconds: float = CLAIM_IDLE_SECONDS, clock: Callable[[], float] = time.time):
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.claims: dict[Path, Claim] = {}

    def _expire_locked(self, now: float) -> list[Claim]:
        expired = [claim for claim in self.claims.values() if now - claim.last_touch > self.idle_seconds]
        for claim in expired:
            del self.claims[claim.path]
        if expired:
            self.changed.notify_all()
        return expired

    def expire(self) -> list[Claim]:
        with self.lock:
            return self._expire_locked(self.clock())

    def claim(self, job_id: str, developer: str, paths: list[Any], reason: str = "") -> tuple[list[Claim], list[dict[str, Any]]]:
        """Toate sau nimic. Întoarce (claims adăugate, conflicte)."""
        normalized: list[Path] = []
        for path in paths:
            candidate = normalize(path)
            if candidate not in normalized:
                normalized.append(candidate)
        if not normalized:
            raise ValueError("Este necesară cel puțin o cale.")
        now = self.clock()
        with self.lock:
            self._expire_locked(now)
            found: list[dict[str, Any]] = []
            for path in normalized:
                for other in self.claims.values():
                    if other.job_id != job_id and conflicts(path, other.path):
                        found.append({"path": display(path), "holder": other.job_id, "developer": other.developer,
                                      "held_path": display(other.path), "since": other.since})
            if found:
                return [], found
            added = []
            for path in normalized:
                existing = self.claims.get(path)
                if existing and existing.job_id == job_id:
                    existing.last_touch = now
                    added.append(existing)
                    continue
                claim = Claim(path, job_id, developer, now, reason[:200], now)
                self.claims[path] = claim
                added.append(claim)
            return added, []

    def release(self, job_id: str, paths: list[Any] | None = None) -> list[Claim]:
        wanted = None if paths is None else {normalize(path) for path in paths}
        with self.lock:
            released = [claim for claim in self.claims.values()
                        if claim.job_id == job_id and (wanted is None or claim.path in wanted)]
            for claim in released:
                del self.claims[claim.path]
            if released:
                self.changed.notify_all()
            return released

    def touch(self, job_id: str) -> None:
        now = self.clock()
        with self.lock:
            for claim in self.claims.values():
                if claim.job_id == job_id:
                    claim.last_touch = now

    def held_by(self, job_id: str) -> list[Claim]:
        with self.lock:
            self._expire_locked(self.clock())
            return [claim for claim in self.claims.values() if claim.job_id == job_id]

    def covers(self, job_id: str, path: Path) -> bool:
        return any(within(path, claim.path) for claim in self.held_by(job_id))

    def related_to(self, job_id: str, path: Path) -> bool:
        return any(related(path, claim.path) for claim in self.held_by(job_id))

    def holder(self, path: Path, exclude_job: str | None = None) -> Claim | None:
        with self.lock:
            self._expire_locked(self.clock())
            for claim in self.claims.values():
                if claim.job_id != exclude_job and conflicts(path, claim.path):
                    return claim
            return None

    def wait_free(self, path: Path, timeout: float, cancelled: threading.Event | None = None) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self.changed:
            while True:
                self._expire_locked(self.clock())
                if not any(conflicts(path, claim.path) for claim in self.claims.values()):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (cancelled is not None and cancelled.is_set()):
                    return False
                self.changed.wait(min(remaining, 0.5))

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            self._expire_locked(self.clock())
            return [claim.describe(self.idle_seconds) for claim in sorted(self.claims.values(), key=lambda item: item.since)]

    def export(self) -> list[dict[str, Any]]:
        """Claims-urile curente pentru persistență (0.8): cu `since` și `last_touch`, nu cu `expires`."""
        with self.lock:
            self._expire_locked(self.clock())
            return [{"path": display(claim.path), "job_id": claim.job_id, "developer": claim.developer, "since": claim.since,
                     "reason": claim.reason, "last_touch": claim.last_touch}
                    for claim in sorted(self.claims.values(), key=lambda item: item.since)]

    def restore(self, rows: Any) -> int:
        """Înlocuiește tabela cu rândurile salvate; cele invalide sau expirate (după `last_touch`) sunt lăsate deoparte."""
        now = self.clock()
        restored: dict[Path, Claim] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("job_id"), str) or not row["job_id"]:
                continue
            try:
                path = normalize(row.get("path"))
            except ValueError:
                continue
            since, last_touch = row.get("since"), row.get("last_touch")
            if not isinstance(since, (int, float)) or isinstance(since, bool):
                continue
            if not isinstance(last_touch, (int, float)) or isinstance(last_touch, bool):
                last_touch = since
            if now - float(last_touch) > self.idle_seconds or path in restored:
                continue
            developer = row.get("developer") if isinstance(row.get("developer"), str) else ""
            reason = row.get("reason") if isinstance(row.get("reason"), str) else ""
            restored[path] = Claim(path, row["job_id"], developer, float(since), reason[:200], float(last_touch))
        with self.lock:
            self.claims = restored
            self.changed.notify_all()
            return len(restored)
