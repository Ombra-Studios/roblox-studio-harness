"""Client MCP stdio local, fără dependențe externe sau acces la credențiale."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from process_tree import WindowsProcessTree

MAX_MESSAGE_CHARS = 24 * 1024 * 1024
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}


class McpError(RuntimeError):
    """Eroare de protocol, transport sau execuție a unui instrument."""


class McpTimeout(McpError):
    """Serverul nu a răspuns în timpul alocat."""


def studio_command() -> str:
    if os.name != "nt":
        raise McpError("Acest launcher este destinat Roblox Studio pe Windows.")
    local = os.environ.get("LOCALAPPDATA")
    if not local or not (Path(local) / "Roblox" / "mcp.bat").is_file():
        raise McpError("mcp.bat lipsește. Activează MCP în Roblox Studio înainte de conectare.")
    # cmd.exe nu folosește regulile de escaping ale argv din runtime-ul C.
    comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    return f'"{comspec}" /d /s /c "cd /d "%LOCALAPPDATA%\\Roblox" && .\\mcp.bat"'


class McpClient:
    def __init__(self, command: str | list[str], timeout: float = 30.0):
        if timeout <= 0:
            raise ValueError("Timeout trebuie să fie pozitiv.")
        self.command = command
        self.timeout = timeout
        self.process: subprocess.Popen[bytes] | None = None
        self._process_tree: WindowsProcessTree | None = None
        self._closed = False
        self.messages: queue.Queue[Any] = queue.Queue()
        self.next_id = 1
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str | None = None
        self._threads: list[threading.Thread] = []

    def __enter__(self) -> McpClient:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            raise McpError("Clientul a fost deja pornit.")
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            if os.name == "nt":
                self._process_tree = WindowsProcessTree(self.process.pid)
        except OSError as error:
            self.process.kill()
            self.process.wait(timeout=3)
            self.close()
            raise McpError("Windows nu a permis izolarea arborelui procesului MCP.") from error
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._drain_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        try:
            result = self.request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "roblox-studio-harness", "version": "0.1.0"},
            })
            version = result.get("protocolVersion")
            if version not in SUPPORTED_PROTOCOLS:
                raise McpError(f"Versiune MCP nesuportată: {version!r}.")
            self.protocol_version = version
            self.server_info = result.get("serverInfo", {})
            self.notify("notifications/initialized")
        except BaseException:
            self.close()
            raise

    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while True:
                line = self.process.stdout.readline(MAX_MESSAGE_CHARS + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE_CHARS:
                    raise McpError("Răspunsul MCP depășește limita de dimensiune.")
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise McpError("Serverul a trimis JSON invalid pe stdout.") from None
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise McpError("Serverul a trimis un mesaj JSON-RPC invalid.")
                self.messages.put(message)
        except Exception as error:
            self.messages.put(error)
        finally:
            self.messages.put(None)

    def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        try:
            # Nu propagăm logurile brute: pot conține date ale utilizatorului.
            while self.process.stderr.read(4096):
                pass
        except (OSError, UnicodeError):
            pass

    def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin or self.process.poll() is not None:
            raise McpError("Conexiunea MCP nu este deschisă.")
        encoded = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(encoded) > MAX_MESSAGE_CHARS:
            raise McpError("Cererea MCP depășește limita de dimensiune.")
        try:
            self.process.stdin.write((encoded + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise McpError("Nu am putut trimite cererea către MCP.") from error

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpTimeout(f"Timeout MCP după {self.timeout:g}s pentru {method}.")
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise McpTimeout(f"Timeout MCP după {self.timeout:g}s pentru {method}.") from None
            if message is None:
                raise McpError("Serverul MCP a închis conexiunea.")
            if isinstance(message, Exception):
                raise McpError(str(message)) from message
            if "method" in message:
                if "id" in message:
                    if message["method"] == "ping":
                        self._send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                    else:
                        # Nu acceptăm sampling, citiri de fișiere sau cereri de credențiale.
                        self._send({"jsonrpc": "2.0", "id": message["id"], "error": {
                            "code": -32601, "message": "Client capability not supported",
                        }})
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                code = error.get("code", "necunoscut") if isinstance(error, dict) else "necunoscut"
                raise McpError(f"Serverul a returnat eroarea JSON-RPC {code} pentru {method}.")
            result = message.get("result")
            if not isinstance(result, dict):
                raise McpError("Rezultatul JSON-RPC nu este un obiect.")
            return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        params: dict[str, Any] = {}
        for _ in range(64):
            page = self.request("tools/list", params)
            entries = page.get("tools")
            if not isinstance(entries, list) or any(not isinstance(tool, dict) for tool in entries):
                raise McpError("Lista instrumentelor MCP este invalidă.")
            tools.extend(entries)
            cursor = page.get("nextCursor")
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise McpError("Paginarea instrumentelor MCP este invalidă.")
            seen_cursors.add(cursor)
            params = {"cursor": cursor}
        raise McpError("Prea multe pagini în lista instrumentelor MCP.")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        process = self.process
        if process is None or self._closed:
            return
        self._closed = True
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if self._process_tree:
                    self._process_tree.close()
                elif os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.kill()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            # Închidem descendenții chiar dacă launcherul a ieșit înaintea lor.
            if self._process_tree:
                self._process_tree.close()
            elif os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for index, stream in enumerate((process.stdout, process.stderr)):
                thread = self._threads[index] if index < len(self._threads) else None
                if thread:
                    thread.join(timeout=1)
                # close() pe un BufferedReader citit încă poate aștepta lock-ul la infinit.
                if stream and (thread is None or not thread.is_alive()):
                    stream.close()
