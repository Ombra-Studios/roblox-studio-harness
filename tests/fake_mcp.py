"""Server MCP minimal, folosit numai de testele offline."""

import json
import subprocess
import sys

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
initialized = False
refused_sampling = False


def send(message):
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if request.get("id") == "server-sampling" and "error" in request:
        refused_sampling = request["error"]["code"] == -32601
        continue
    if method == "notifications/initialized":
        initialized = True
        continue
    if "id" not in request:
        continue
    request_id = request["id"]
    if method == "initialize":
        if mode == "stderr-binary":
            sys.stderr.buffer.write(b"\xff" + b"x" * (128 * 1024))
            sys.stderr.buffer.flush()
        if mode == "stdout-binary":
            sys.stdout.buffer.write(b"\xff\n")
            sys.stdout.buffer.flush()
            continue
        if mode == "eof":
            break
        if mode == "malformed":
            print("not-json", flush=True)
            continue
        if mode == "timeout":
            continue
        version = "2099-01-01" if mode == "protocol" else "2024-11-05"
        result = {"protocolVersion": version, "serverInfo": {"name": "fake", "version": "1"}, "capabilities": {"tools": {}}}
    elif method == "tools/list":
        if not initialized:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600}})
            continue
        params = request.get("params", {})
        if mode == "pagination":
            result = {"tools": [{"name": "second" if "cursor" in params else "first", "inputSchema": {"type": "object"}}]}
            if "cursor" not in params:
                result["nextCursor"] = "page2"
        elif mode == "repeated-cursor":
            result = {"tools": [], "nextCursor": "same"}
        elif mode == "bad-tools":
            result = {"tools": "invalid"}
        else:
            result = {"tools": [{"name": "test_tool", "inputSchema": {"type": "object"}}]}
        if mode == "notification":
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        if mode == "sampling":
            send({"jsonrpc": "2.0", "id": "server-sampling", "method": "sampling/createMessage", "params": {}})
    elif method == "tools/call":
        if mode == "orphan-pipes":
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        if mode == "rpc-error":
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "test"}})
            continue
        result = {"content": [{"type": "text", "text": json.dumps({"arguments": request["params"]["arguments"], "refusedSampling": refused_sampling})}], "isError": mode == "tool-error"}
    else:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601}})
        continue
    send({"jsonrpc": "2.0", "id": request_id, "result": result})
