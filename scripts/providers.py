"""Adaptoare pentru CLI-urile oficiale, rulate cu configurația normală a utilizatorului (Mod S)."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from process_tree import WindowsProcessTree

PROVIDERS = ("claude", "codex")
MAX_LINE = 8 * 1024 * 1024
_EOF = object()
KEEP_ENV = {"CLAUDE_CONFIG_DIR", "CODEX_HOME"}


class ProviderError(RuntimeError):
    """Eroare de tură care păstrează identificatorul conversației native, ca să poată fi reluată."""

    def __init__(self, message: str, native_id: str | None = None):
        super().__init__(message)
        self.native_id = native_id


def _provider(value: str) -> str:
    if value not in PROVIDERS:
        raise RuntimeError("Provider necunoscut.")
    return value


def resolve_executable(provider: str, environ: dict[str, str] | None = None) -> Path | None:
    """Acceptăm executabile native, nu wrapper-ele PowerShell/CMD din PATH."""
    provider = _provider(provider)
    env = os.environ if environ is None else environ
    override = env.get(f"STUDIO_HARNESS_{provider.upper()}_EXE")
    expected_name = provider + (".exe" if os.name == "nt" else "")
    if override:
        path = Path(override).expanduser()
        return path.resolve() if path.is_absolute() and path.is_file() and path.name.lower() == expected_name else None
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    candidates = [home / ".local" / "bin" / expected_name]
    if provider == "codex":
        roaming = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
        package = roaming / "npm" / "node_modules" / "@openai" / "codex"
        architecture = "aarch64" if env.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "x86_64"
        npm_arch = "arm64" if architecture == "aarch64" else "x64"
        vendor = Path("vendor") / f"{architecture}-pc-windows-msvc" / "bin" / "codex.exe"
        candidates.extend([package / "node_modules" / "@openai" / f"codex-win32-{npm_arch}" / vendor,
                           package / vendor, home / ".cargo" / "bin" / expected_name])
    from_path = shutil.which(expected_name, path=env.get("PATH", ""))
    if from_path:
        candidates.append(Path(from_path))
    for path in candidates:
        if path.is_file() and path.name.lower() == expected_name:
            return path.resolve()
    return None


class _Process:
    """Proces deținut de daemon; stdout JSON și stderr drenat fără expunerea secretelor."""

    def __init__(self, argv: list[str], *, env: dict[str, str], cwd: Path, interactive: bool = False):
        self.process: subprocess.Popen[bytes] | None = None
        self.tree: WindowsProcessTree | None = None
        self.lines: queue.Queue[Any] = queue.Queue()
        self.closed = False
        self.threads: list[threading.Thread] = []
        self.write_lock = threading.Lock()
        try:
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_CONSOLE if interactive else subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(
                argv, cwd=str(cwd), env=env, shell=False,
                stdin=None if interactive else subprocess.PIPE,
                stdout=None if interactive else subprocess.PIPE,
                stderr=None if interactive else subprocess.PIPE,
                creationflags=flags, start_new_session=os.name != "nt",
            )
            if os.name == "nt" and not interactive:
                self.tree = WindowsProcessTree(self.process.pid)
            if not interactive:
                self.threads = [threading.Thread(target=self._stdout, daemon=True),
                                threading.Thread(target=self._stderr, daemon=True)]
                for thread in self.threads:
                    thread.start()
        except BaseException:
            self.close()
            raise RuntimeError("Nu am putut porni executabilul oficial al providerului.") from None

    def _stdout(self) -> None:
        assert self.process and self.process.stdout
        stream = self.process.stdout
        try:
            while True:
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    raise RuntimeError("Providerul a depășit limita unui mesaj.")
                self.lines.put(line)
        except Exception:
            self.lines.put(RuntimeError("Fluxul providerului nu a putut fi citit."))
        finally:
            self.lines.put(_EOF)
            stream.close()

    def _stderr(self) -> None:
        assert self.process and self.process.stderr
        stream = self.process.stderr
        try:
            while stream.read(8192):
                pass
        except OSError:
            pass
        finally:
            stream.close()

    def write(self, data: bytes) -> None:
        with self.write_lock:
            if not self.process or not self.process.stdin or self.process.poll() is not None:
                raise RuntimeError("Procesul providerului nu mai este disponibil.")
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise RuntimeError("Nu am putut trimite mesajul providerului.") from None

    def finish_input(self) -> None:
        with self.write_lock:
            if self.process and self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()

    def read_json(self, timeout: float = 0.2) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while True:
            line = self.lines.get(timeout=max(0.001, deadline - time.monotonic()))
            if line is _EOF:
                return None
            if isinstance(line, Exception):
                raise line
            if not line.strip():
                if time.monotonic() >= deadline:
                    raise queue.Empty
                continue
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                raise RuntimeError("Providerul a trimis un eveniment JSON invalid.") from None
            if not isinstance(value, dict):
                raise RuntimeError("Providerul a trimis un eveniment invalid.")
            return value

    def wait(self, timeout: float) -> int:
        assert self.process
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Procesul providerului nu s-a încheiat în timpul alocat.") from None

    def poll(self) -> int | None:
        return self.process.poll() if self.process else 1

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        process = self.process
        if not process:
            return
        if self.tree:
            self.tree.close()
            self.tree = None
        elif process.poll() is None:
            if os.name == "nt":
                process.terminate()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        try:
            self.finish_input()
        except (OSError, ValueError, RuntimeError):
            pass
        # Cititorul își închide propriul stream; nu blocăm close() pe lock-ul unui cititor activ.
        for thread in self.threads:
            thread.join(timeout=0.5)


def _rpc_error_message(method: str, error: Any) -> str:
    """Raportează metoda/codul și erori de schemă, niciodată payloadul brut."""
    safe_method = method if re.fullmatch(r"[A-Za-z0-9_/]{1,80}", method) else "necunoscută"
    code = error.get("code") if isinstance(error, dict) else None
    safe_code = str(code) if isinstance(code, int) and not isinstance(code, bool) else "necunoscut"
    message = error.get("message", "") if isinstance(error, dict) else ""
    detail = ""
    if isinstance(message, str):
        missing = re.search(r"missing field [`'\"]([A-Za-z_][A-Za-z0-9_]{0,79})[`'\"]", message)
        if missing:
            detail = f" Lipsește câmpul obligatoriu {missing.group(1)}."
        elif "unknown variant" in message.lower():
            detail = " O valoare de tip enum nu este acceptată de versiunea instalată."
        elif "invalid type" in message.lower():
            detail = " Un parametru are un tip incompatibil cu protocolul instalat."
    return f"Codex app-server a refuzat cererea {safe_method} (cod {safe_code}).{detail}"


class _RpcClient:
    """JSON-RPC app-server Codex, fără câmp jsonrpc și fără metode shell expuse UI-ului."""

    def __init__(self, process: Any, on_notification: Callable[[dict[str, Any]], None] | None = None):
        self.process = process
        self.on_notification = on_notification
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.pending: dict[int, queue.Queue[Any]] = {}
        self.lock = threading.Lock()
        self.next_id = 1
        self.closed = threading.Event()
        self.failed = False
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {"clientInfo": {"name": "studio_harness", "title": "Studio Harness", "version": "0.4.0"}})
            self.notify("initialized", {})
        except BaseException:
            self.close()
            raise

    def _send(self, value: dict[str, Any]) -> None:
        self.process.write((json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8"))

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def _read(self) -> None:
        try:
            while not self.closed.is_set():
                try:
                    message = self.process.read_json(timeout=0.2)
                except queue.Empty:
                    continue
                if message is None:
                    break
                if "method" in message:
                    if "id" in message:
                        self._send({"id": message["id"], "error": {"code": -32601, "message": "Client method not supported"}})
                        self.events.put({"method": "provider/blocked_request", "params": {}})
                    else:
                        if self.on_notification:
                            self.on_notification(message)
                        self.events.put(message)
                else:
                    with self.lock:
                        destination = self.pending.get(message.get("id"))
                    if destination:
                        destination.put(message)
        except Exception:
            pass
        finally:
            self.failed = True
            with self.lock:
                destinations = list(self.pending.values())
            for destination in destinations:
                destination.put(RuntimeError("Conexiunea Codex app-server s-a închis."))
            self.events.put({"method": "provider/closed", "params": {}})

    def request(self, method: str, params: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        with self.lock:
            if self.closed.is_set() or self.failed:
                raise RuntimeError("Conexiunea Codex app-server nu este disponibilă.")
            request_id = self.next_id
            self.next_id += 1
            destination: queue.Queue[Any] = queue.Queue()
            self.pending[request_id] = destination
        try:
            self._send({"id": request_id, "method": method, "params": params})
            try:
                message = destination.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError("Cererea către Codex app-server a expirat.") from None
            if isinstance(message, Exception):
                raise message
            if "error" in message:
                raise RuntimeError(_rpc_error_message(method, message["error"]))
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Codex app-server a întors un rezultat invalid.")
            return result
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        self.process.close()
        if threading.current_thread() is not self.reader:
            self.reader.join(timeout=1)


def _session_id(value: Any, provider: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise RuntimeError("Providerul a întors un identificator de conversație invalid.")
    if provider == "claude":
        try:
            uuid.UUID(value)
        except ValueError:
            raise RuntimeError("Claude Code a întors un identificator de conversație invalid.") from None
    return value


class ProviderRegistry:
    """Fără login și fără profile dedicate: CLI-ul folosește contul cu care este deja autentificat în terminal."""

    def __init__(self, *, environ: dict[str, str] | None = None, child_factory: Callable[..., Any] | None = None):
        self.environ = dict(os.environ if environ is None else environ)
        self.child_factory = child_factory or _Process
        self.lock = threading.RLock()
        self.children: set[Any] = set()
        self.closed = False

    def _environment(self, provider: str) -> dict[str, str]:
        # Cheile API și variabilele altor harness-uri nu ajung la CLI; directorul de configurare al utilizatorului rămâne.
        blocked_prefixes = ("ANTHROPIC", "CLAUDE", "CODEX", "OPENAI", "AZURE_OPENAI", "AWS_", "GOOGLE_", "GCLOUD_", "GCP_", "GEMINI_", "OPENROUTER_", "STUDIO_HARNESS_JOB", "STUDIO_HARNESS_URL")
        blocked = {"API_KEY", "API_BASE_URL", "MODEL_PROVIDER", "NODE_OPTIONS", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "OLLAMA_HOST"}
        env = {key: value for key, value in self.environ.items()
               if key.upper() in KEEP_ENV or (key.upper() not in blocked and not key.upper().startswith(blocked_prefixes))}
        env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY", ""), "127.0.0.1", "localhost"]))
        env["no_proxy"] = env["NO_PROXY"]
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _program(self, provider: str) -> Path:
        path = resolve_executable(provider, self.environ)
        if path is None:
            raise RuntimeError("Executabilul nativ oficial nu a fost găsit. Instalează CLI-ul sau configurează override-ul Studio Harness.")
        return path

    def _spawn(self, argv: list[str], env: dict[str, str], cwd: Path, interactive: bool = False) -> Any:
        with self.lock:
            if self.closed:
                raise RuntimeError("Registry-ul providerilor a fost închis.")
            try:
                child = self.child_factory(argv, env=env, cwd=cwd, interactive=interactive)
            except Exception:
                raise RuntimeError("Nu am putut porni aplicația oficială a providerului.") from None
            self.children.add(child)
            return child

    def _release(self, child: Any) -> None:
        try:
            child.close()
        finally:
            with self.lock:
                self.children.discard(child)

    def status(self) -> dict[str, Any]:
        # Numai metadate locale; fără pornire de CLI, browser sau inferență.
        with self.lock:
            return {provider: {"available": resolve_executable(provider, self.environ) is not None} for provider in PROVIDERS}

    def _codex_options(self) -> list[str]:
        settings = {
            "features.shell_tool": False, "features.unified_exec": False, "features.apps": False,
            "features.multi_agent": False, "features.remote_plugin": False,
            "features.skill_mcp_dependency_install": False, "agents.enabled": False,
            "tools.view_image": False, "web_search": "disabled", "sandbox_mode": "read-only",
            "approval_policy": "never",
        }
        arguments: list[str] = []
        for key, value in settings.items():
            arguments.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
        return arguments

    def run(self, provider: str, prompt: str, native_session_id: str | None, job_directory: Path,
            bridge_url: str, job_id: str, job_token: str,
            emit: Callable[..., None], cancelled: threading.Event) -> str | None:
        provider = _provider(provider)
        if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 1024 * 1024:
            raise RuntimeError("Promptul providerului este invalid sau prea mare.")
        if native_session_id:
            _session_id(native_session_id, provider)
        try:
            from urllib.parse import urlsplit
            parsed = urlsplit(bridge_url)
            valid_bridge = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                            and not parsed.username and not parsed.password and parsed.path in ("", "/")
                            and not parsed.query and not parsed.fragment)
        except (ValueError, TypeError):
            valid_bridge = False
        if (not valid_bridge or not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id)
                or not isinstance(job_token, str) or not job_token):
            raise RuntimeError("Configurația locală a jobului este invalidă.")
        if cancelled.is_set():
            return native_session_id
        environment = self._environment(provider)
        environment.update(STUDIO_HARNESS_URL=bridge_url, STUDIO_HARNESS_JOB_ID=job_id, STUDIO_HARNESS_JOB_TOKEN=job_token)
        proxy = Path(__file__).resolve().with_name("bridge_mcp.py")
        if not proxy.is_file():
            raise RuntimeError("Proxy-ul MCP al daemon-ului lipsește.")
        job_directory = Path(job_directory).resolve()
        if not job_directory.is_dir():
            raise RuntimeError("Directorul jobului nu există.")

        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace(job_token, "[redactat]")
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        def safe_emit(kind: str, text: str = "", **fields: Any) -> None:
            # Tokenul local nu este niciodată afișat chiar dacă un provider îl reflectă accidental.
            emit(kind, redact(text), **redact(fields))

        if provider == "claude":
            environment["MCP_TOOL_TIMEOUT"] = "1200000"
            return self._run_claude(prompt, native_session_id, job_directory, environment, proxy, safe_emit, cancelled)
        return self._run_codex(prompt, native_session_id, job_directory, environment, proxy, safe_emit, cancelled)

    def _run_claude(self, prompt: str, native_id: str | None, directory: Path, env: dict[str, str],
                    proxy: Path, emit: Callable[..., None], cancelled: threading.Event) -> str | None:
        config = {"mcpServers": {"studio_bridge": {"type": "stdio", "command": sys.executable,
                  "args": ["-I", str(proxy)], "env": {key: "${" + key + "}" for key in
                  ("STUDIO_HARNESS_URL", "STUDIO_HARNESS_JOB_ID", "STUDIO_HARNESS_JOB_TOKEN")}}}}
        config_path = directory / "claude-mcp.json"
        try:
            with config_path.open("x", encoding="utf-8") as stream:
                json.dump(config, stream, ensure_ascii=False)
        except OSError:
            raise RuntimeError("Configurația MCP efemeră nu poate fi creată în directorul jobului.") from None
        argv = [str(self._program("claude")), "-p", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--tools", "", "--strict-mcp-config", "--mcp-config", str(config_path),
                "--allowedTools", "mcp__studio_bridge__*", "--permission-mode", "dontAsk", "--permission-prompts", "none",
                "--disable-slash-commands", "--setting-sources", "", "--settings",
                '{"disableAllHooks":true,"autoMemoryEnabled":false}']
        if native_id:
            argv.extend(["--resume", native_id])
        child = self._spawn(argv, env, directory)
        session_id, initialized, result_seen, text_streamed = native_id, False, False, False
        final_deadline: float | None = None
        deadline = time.monotonic() + 3600
        try:
            child.write(prompt.encode("utf-8"))
            child.finish_input()
            while time.monotonic() < deadline:
                if cancelled.is_set():
                    emit("status", "Oprire solicitată pentru procesul Claude Code al acestei cereri.")
                    return session_id
                if final_deadline is not None and time.monotonic() > final_deadline:
                    raise RuntimeError("Claude Code nu a închis fluxul după rezultatul final.")
                try:
                    message = child.read_json(timeout=0.2)
                except queue.Empty:
                    continue
                if message is None:
                    break
                kind = message.get("type")
                if kind == "system" and message.get("subtype") == "init":
                    servers = message.get("mcp_servers", [])
                    if (message.get("mcp_server_errors") or not isinstance(servers, list)
                            or len(servers) != 1 or servers[0].get("name") != "studio_bridge"
                            or servers[0].get("status") != "connected"):
                        raise RuntimeError("Claude Code nu a conectat exclusiv proxy-ul MCP al jobului.")
                    tools = message.get("tools", [])
                    if not isinstance(tools, list) or any(not isinstance(tool, str) or
                            (not tool.startswith("mcp__studio_bridge__") and tool != "EndConversation") for tool in tools):
                        raise RuntimeError("Claude Code a expus instrumente în afara bridge-ului Studio.")
                    received_id = _session_id(message.get("session_id"), "claude")
                    if native_id and received_id != native_id:
                        raise RuntimeError("Claude Code nu a reluat conversația cerută.")
                    session_id, initialized = received_id, True
                    emit("status", "Claude Code este conectat la proxy-ul controlat de Studio.")
                elif kind == "system" and message.get("subtype") == "permission_denied":
                    raise RuntimeError("Claude Code a raportat o operație refuzată; cererea nu este reluată pe altă cale.")
                elif kind == "stream_event":
                    if not initialized:
                        raise RuntimeError("Claude Code a emis conținut înaintea verificării conexiunii MCP.")
                    event = message.get("event", {})
                    delta = event.get("delta", {}) if isinstance(event, dict) else {}
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                        emit("text", delta["text"])
                        text_streamed = True
                elif kind in ("assistant", "user"):
                    if not initialized:
                        raise RuntimeError("Claude Code a emis mesaje înaintea verificării conexiunii MCP.")
                    body = message.get("message", {})
                    content = body.get("content", []) if isinstance(body, dict) else []
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name == "EndConversation":
                                continue
                            if not isinstance(name, str) or not name.startswith("mcp__studio_bridge__"):
                                raise RuntimeError("Claude Code a încercat un instrument din afara bridge-ului.")
                            emit("tool", "Claude Code a solicitat un instrument Studio.", tool=name)
                        elif block.get("type") == "tool_result" and block.get("is_error"):
                            # O eroare de tool face parte din bucla agentului; nu încheie cererea.
                            emit("status", "Instrumentul a raportat o eroare; agentul continuă cu acest rezultat.")
                elif kind == "result":
                    if not initialized or message.get("is_error") or message.get("subtype") != "success" or message.get("permission_denials"):
                        raise RuntimeError("Claude Code nu a finalizat cererea cu succes sau a raportat refuzuri.")
                    received_id = _session_id(message.get("session_id", session_id), "claude")
                    if received_id != session_id:
                        raise RuntimeError("Identificatorul conversației Claude Code s-a schimbat neașteptat.")
                    if not text_streamed and isinstance(message.get("result"), str):
                        emit("text", message["result"])
                    result_seen = True
                    final_deadline = time.monotonic() + 35
            else:
                raise RuntimeError("Cererea Claude Code a depășit timpul maxim.")
            if not result_seen or child.wait(5) != 0:
                raise RuntimeError("Claude Code s-a închis fără un rezultat final valid.")
            return session_id
        except RuntimeError as error:
            raise ProviderError(str(error), session_id) from None
        finally:
            self._release(child)

    def _run_codex(self, prompt: str, native_id: str | None, directory: Path, env: dict[str, str],
                   proxy: Path, emit: Callable[..., None], cancelled: threading.Event) -> str | None:
        settings = {
            "mcp_servers.studio_bridge.command": sys.executable,
            "mcp_servers.studio_bridge.args": ["-I", str(proxy)],
            "mcp_servers.studio_bridge.env_vars": ["STUDIO_HARNESS_URL", "STUDIO_HARNESS_JOB_ID", "STUDIO_HARNESS_JOB_TOKEN"],
            "mcp_servers.studio_bridge.enabled": True, "mcp_servers.studio_bridge.required": True,
            "mcp_servers.studio_bridge.tool_timeout_sec": 1200,
        }
        argv = [str(self._program("codex")), *self._codex_options()]
        for key, value in settings.items():
            argv.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
        argv.append("app-server")
        child = self._spawn(argv, env, directory)
        client: _RpcClient | None = None
        thread_id, turn_id = native_id, None
        try:
            client = _RpcClient(child)
            params = {"cwd": str(directory), "sandbox": "read-only", "approvalPolicy": "never"}
            if native_id:
                params["threadId"] = native_id
            result = client.request("thread/resume" if native_id else "thread/start", params)
            thread = result.get("thread", {})
            thread_id = _session_id(thread.get("id") if isinstance(thread, dict) else None, "codex")
            if native_id and thread_id != native_id:
                raise RuntimeError("Codex nu a reluat conversația cerută.")
            readiness_deadline = time.monotonic() + 30
            while True:
                if cancelled.is_set():
                    return thread_id
                status = client.request("mcpServerStatus/list", {})
                rows = status.get("data", [])
                if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("name") != "studio_bridge" for row in rows) or status.get("nextCursor"):
                    raise RuntimeError("Codex a încărcat servere MCP în afara bridge-ului Studio.")
                if len(rows) == 1 and isinstance(rows[0].get("tools"), (dict, list)) and rows[0]["tools"]:
                    break
                if time.monotonic() >= readiness_deadline:
                    raise RuntimeError("Codex nu a conectat proxy-ul MCP al jobului.")
                cancelled.wait(0.5)
            emit("status", "Codex este conectat la proxy-ul controlat de Studio.")
            result = client.request("turn/start", {
                "threadId": thread_id, "input": [{"type": "text", "text": prompt}], "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly"},
            })
            turn = result.get("turn", {})
            turn_id = _session_id(turn.get("id") if isinstance(turn, dict) else None, "codex")
            streamed: set[str] = set()
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                if cancelled.is_set():
                    try:
                        client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
                    except RuntimeError:
                        pass
                    emit("status", "Anulare trimisă turei Codex; se închide numai procesul acestei cereri.")
                    return thread_id
                try:
                    message = client.events.get(timeout=0.2)
                except queue.Empty:
                    continue
                method, params = message.get("method"), message.get("params", {})
                if not isinstance(params, dict):
                    raise RuntimeError("Codex a trimis un eveniment invalid.")
                if method in ("provider/closed", "provider/blocked_request"):
                    raise RuntimeError("Codex s-a deconectat sau a cerut o operație în afara bridge-ului.")
                if params.get("threadId", thread_id) != thread_id or params.get("turnId", turn_id) != turn_id:
                    continue
                if method == "item/agentMessage/delta" and isinstance(params.get("delta"), str):
                    emit("text", params["delta"])
                    streamed.add(str(params.get("itemId", "")))
                elif method in ("item/started", "item/completed"):
                    item = params.get("item", {})
                    if not isinstance(item, dict):
                        raise RuntimeError("Codex a trimis un element invalid.")
                    item_type = item.get("type")
                    if item_type in {"commandExecution", "fileChange", "webSearch", "imageView", "collabAgentToolCall", "dynamicToolCall"}:
                        raise RuntimeError("Codex a încercat un instrument în afara proxy-ului Studio.")
                    if item_type == "mcpToolCall":
                        if item.get("server") != "studio_bridge":
                            raise RuntimeError("Codex a cerut un alt server MCP.")
                        tool_result = item.get("result")
                        if method == "item/started":
                            emit("tool", "Codex a solicitat un instrument Studio.", tool=str(item.get("tool", "")))
                        elif item.get("status") == "failed" or item.get("error") or (isinstance(tool_result, dict) and tool_result.get("isError")):
                            emit("status", "Instrumentul a raportat o eroare; agentul continuă cu acest rezultat.")
                    elif method == "item/completed" and item_type == "agentMessage" and str(item.get("id", "")) not in streamed and "" not in streamed:
                        if isinstance(item.get("text"), str):
                            emit("text", item["text"])
                elif method == "turn/completed":
                    turn = params.get("turn", {})
                    if not isinstance(turn, dict) or turn.get("id") != turn_id:
                        continue
                    if turn.get("status") != "completed" or turn.get("error"):
                        raise RuntimeError("Codex nu a finalizat tura cu succes.")
                    return thread_id
            raise RuntimeError("Cererea Codex a depășit timpul maxim.")
        except RuntimeError as error:
            raise ProviderError(str(error), thread_id if thread_id != native_id or native_id else None) from None
        finally:
            if client:
                client.close()
            self._release(child)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            children = list(self.children)
            self.children.clear()
        for child in children:
            child.close()
