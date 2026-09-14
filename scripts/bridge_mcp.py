"""Proxy MCP stdio scoped la un singur job din pluginul Studio."""

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_LINE = 2 * 1024 * 1024
MAX_RESPONSE = 32 * 1024 * 1024


def settings():
    base = os.environ.get("STUDIO_HARNESS_URL", "")
    job_id = os.environ.get("STUDIO_HARNESS_JOB_ID", "")
    token = os.environ.get("STUDIO_HARNESS_JOB_TOKEN", "")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Bridge-ul trebuie să fie un URL HTTP loopback valid.")
    if not re.fullmatch(r"[a-f0-9]{32}", job_id) or not token:
        raise ValueError("Configurația jobului MCP lipsește.")
    return base.rstrip("/"), job_id, token


def bridge_request(base, job_id, token, operation, body=None):
    data = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
    request = Request(
        f"{base}/agent/{job_id}/{operation}", data=data,
        headers={"X-Studio-Harness-Job": token, "Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urlopen(request, timeout=1100) as response:
            encoded = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        # Nu includem URL-uri, tokenuri sau antete în erorile expuse modelului.
        if error.code in (401, 404, 409):
            raise RuntimeError("Jobul nu mai este autorizat sau activ în Studio.") from None
        raise RuntimeError(f"Bridge-ul a refuzat cererea (HTTP {error.code}).") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Bridge-ul local nu răspunde.") from None
    if len(encoded) > MAX_RESPONSE:
        raise RuntimeError("Răspunsul bridge-ului este prea mare.")
    result = json.loads(encoded)
    if not isinstance(result, dict):
        raise RuntimeError("Răspunsul bridge-ului este invalid.")
    return result


def serve(input_stream, output_stream, config=None):
    base, job_id, token = config or settings()
    while True:
        line = input_stream.readline(MAX_LINE + 1)
        if not line:
            return
        if len(line) > MAX_LINE:
            raise ValueError("Cererea MCP este prea mare.")
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise ValueError("Mesaj MCP invalid.")
            request_id = request.get("id")
            method = request.get("method")
            if request_id is None:
                continue
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("Parametri MCP invalizi.")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "StudioHarness", "version": "1.0.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = bridge_request(base, job_id, token, "tools")
            elif method == "tools/call":
                result = bridge_request(base, job_id, token, "call", params)
            else:
                response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
                output_stream.write(json.dumps(response) + "\n")
                output_stream.flush()
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ValueError, RuntimeError, OSError):
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32603, "message": "Operația nu a fost acceptată de bridge. Verifică panoul Studio."}}
        output_stream.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
        output_stream.flush()


if __name__ == "__main__":
    try:
        serve(sys.stdin, sys.stdout)
    except (ValueError, OSError):
        print("Proxy MCP indisponibil: verifică pornirea bridge-ului și jobul activ.", file=sys.stderr)
        raise SystemExit(1)
