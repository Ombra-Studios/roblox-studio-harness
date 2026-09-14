"""Harness pentru MCP-ul oficial Roblox Studio. Python 3.10+, fără dependențe."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import math
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from mcp_client import McpClient, McpError, studio_command

READ_TOOLS = frozenset({
    "list_roblox_studios", "get_studio_state", "search_game_tree", "inspect_instance",
    "script_read", "script_search", "script_grep", "get_console_output", "http_get",
    "search_asset", "screen_capture", "wait_job_finished",
})
NO_STUDIO_TOOLS = frozenset({"list_roblox_studios"})
DATAMODEL_TOOLS = frozenset({
    "search_game_tree", "inspect_instance", "script_read", "script_search", "script_grep",
    "get_console_output", "screen_capture", "multi_edit", "execute_luau",
})
GENERATION_TOOLS = frozenset({"generate_mesh", "generate_material", "generate_procedural_model", "subagent"})
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def reject_constant(value: str) -> None:
    raise ValueError(f"Număr invalid: {value}")


def json_object(text: str) -> dict[str, Any]:
    value = json.loads(text, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Argumentele trebuie să fie un obiect JSON.")
    return value


def finite_number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("Coordonatele trebuie să fie numere finite.")
    return value


def lua_string(value: str) -> str:
    # Luau acceptă Unicode direct, nu escape-urile JSON de forma \uXXXX.
    return json.dumps(value, ensure_ascii=False)


def validate_target(name: str, arguments: dict[str, Any], studio_id: str | None,
                    allow_mutation: bool, allow_generation: bool,
                    datamodel_type: str = "Edit") -> dict[str, Any]:
    if name not in READ_TOOLS and not allow_mutation:
        raise ValueError("Instrumentul poate modifica jocul. Adaugă --confirm sau --allow-mutation numai după aprobare.")
    if name in GENERATION_TOOLS and not allow_generation:
        raise ValueError("Generarea poate consuma cote sau credite. Este necesar și --allow-generation.")
    result = dict(arguments)
    if name not in NO_STUDIO_TOOLS:
        if not studio_id:
            raise ValueError("Selectează explicit instanța cu --studio ID. Rulează doctor pentru listă.")
        if result.get("studio_id", studio_id) != studio_id:
            raise ValueError("studio_id din argumente diferă de --studio; apelul a fost oprit.")
        result["studio_id"] = studio_id
    if name in DATAMODEL_TOOLS:
        # Cerut de runtime inclusiv pentru tree/inspect, deși schema unor versiuni îl omite.
        result.setdefault("datamodel_type", datamodel_type)
        if result["datamodel_type"] not in ("Edit", "Client", "Server"):
            raise ValueError("datamodel_type trebuie să fie Edit, Client sau Server.")
    return result


def connected_studios(client: McpClient) -> list[dict[str, Any]]:
    result = client.call_tool("list_roblox_studios", {})
    if result.get("isError"):
        raise McpError("Serverul nu a putut lista instanțele Studio.")
    candidates = [result.get("structuredContent")]
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                candidates.append(json.loads(item.get("text", "")))
            except (json.JSONDecodeError, TypeError):
                continue
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("studios"), list):
            studios = candidate["studios"]
            if any(not isinstance(studio, dict) or not isinstance(studio.get("id"), str) for studio in studios):
                raise McpError("Lista instanțelor Studio are un format invalid.")
            return studios
    raise McpError("Serverul nu a întors o listă Studio recunoscută.")


def wait_for_studios(client: McpClient, wait_seconds: float,
                     target_id: str | None = None) -> list[dict[str, Any]]:
    # Studio se reconectează asincron; păstrăm același proces MCP pe durata ferestrei.
    deadline = time.monotonic() + wait_seconds
    while True:
        studios = connected_studios(client)
        if studios and (target_id is None or any(studio["id"] == target_id for studio in studios)):
            return studios
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return studios
        time.sleep(min(1.0, remaining))


def materialize_images(result: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Salvează imaginile fără să trimită base64 în terminal și fără overwrite."""
    content = result.get("content", [])
    if not isinstance(content, list):
        raise McpError("Conținutul răspunsului MCP nu este o listă.")
    rewritten = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image":
            rewritten.append(item)
            continue
        mime = item.get("mimeType")
        data = item.get("data")
        if mime not in IMAGE_TYPES or not isinstance(data, str):
            raise McpError("Format de imagine MCP nesuportat.")
        if len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise McpError("Imaginea MCP depășește limita de dimensiune.")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise McpError("Imaginea MCP nu conține base64 valid.") from None
        signatures = {
            "image/png": decoded.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": decoded.startswith(b"\xff\xd8\xff"),
            "image/webp": decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP",
        }
        if len(decoded) > MAX_IMAGE_BYTES or not signatures[mime]:
            raise McpError("Conținutul imaginii nu corespunde tipului declarat.")
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / ("capture-" + uuid.uuid4().hex + IMAGE_TYPES[mime])
        with destination.open("xb") as stream:
            stream.write(decoded)
        rewritten.append({"type": "saved_image", "mimeType": mime, "path": str(destination.resolve()), "bytes": len(decoded)})
    return {**result, "content": rewritten}


def create_part_code(name: str, parent: str, position: list[float], size: list[float],
                     color: list[float]) -> str:
    if not name or any(character in name for character in (".", "/", "\\", "\x00")):
        raise ValueError("Numele piesei nu poate fi gol și nu poate conține punct sau separator de cale.")
    if not parent or any(not segment for segment in parent.split(".")):
        raise ValueError("Calea părintelui este invalidă.")
    if any(not math.isfinite(value) for value in position + size + color):
        raise ValueError("Toate valorile numerice trebuie să fie finite.")
    if any(value <= 0 for value in size):
        raise ValueError("Dimensiunile trebuie să fie pozitive.")
    if any(value < 0 or value > 255 for value in color):
        raise ValueError("Culoarea RGB trebuie să fie între 0 și 255.")
    vector = lambda values: ", ".join(repr(value) for value in values)
    return f'''local function resolveUnique(path)
    local current = game
    for index, segment in ipairs(string.split(path, ".")) do
        if not (index == 1 and segment == "game") then
            local matches = {{}}
            for _, child in ipairs(current:GetChildren()) do
                if child.Name == segment then table.insert(matches, child) end
            end
            assert(#matches == 1, "Cale absentă sau ambiguă: " .. segment)
            current = matches[1]
        end
    end
    return current
end
local parent = resolveUnique({lua_string(parent)})
local name = {lua_string(name)}
for _, child in ipairs(parent:GetChildren()) do
    assert(child.Name ~= name, "Există deja un obiect cu acest nume; nu suprascriem.")
end
local part = Instance.new("Part")
local ok, message = pcall(function()
    part.Name = name
    part.Anchored = true
    part.Size = Vector3.new({vector(size)})
    part.Position = Vector3.new({vector(position)})
    part.Color = Color3.fromRGB({vector(color)})
    part:SetAttribute("CreatedByHarness", "roblox-studio-harness")
    part.Parent = parent
end)
if not ok then part:Destroy(); error(message) end
return game:GetService("HttpService"):JSONEncode({{
    path = part:GetFullName(), className = part.ClassName, anchored = part.Anchored
}})
'''


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Controlează instanța aleasă de Roblox Studio prin MCP-ul oficial.")
    root.add_argument("--studio", help="ID-ul exact întors de doctor; obligatoriu pentru operații asupra scenei")
    root.add_argument("--datamodel", choices=("Edit", "Client", "Server"), default="Edit", help="Contextul operațiilor de inspecție; implicit Edit")
    root.add_argument("--timeout", type=finite_number, default=30, help="Timeout pentru fiecare cerere MCP, în secunde")
    root.add_argument("--connect-wait", type=finite_number, default=15, help="Fereastra de reconectare Studio, în secunde (0–60)")
    root.add_argument("--output-dir", type=Path, default=Path(".harness-output"), help="Director local pentru capturi")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Testează conexiunea și listează instanțele Studio, fără modificări")
    tools = commands.add_parser("tools", help="Listează instrumentele reale ale serverului")
    tools.add_argument("--name", help="Afișează schema completă a unui singur instrument")
    for name in ("state", "logs"):
        commands.add_parser(name)
    tree = commands.add_parser("tree")
    tree.add_argument("--path", default="Workspace")
    tree.add_argument("--depth", type=int, choices=range(0, 11), default=3)
    tree.add_argument("--limit", type=int, default=100)
    tree.add_argument("--class-name")
    tree.add_argument("--keywords")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("path")
    read = commands.add_parser("read-script")
    read.add_argument("path")
    read.add_argument("--start", type=int)
    read.add_argument("--end", type=int)
    search = commands.add_parser("find-script")
    search.add_argument("keywords")
    grep = commands.add_parser("grep")
    grep.add_argument("query")
    capture = commands.add_parser("capture")
    capture.add_argument("--camera", nargs=3, type=finite_number)
    capture.add_argument("--look-at", nargs=3, type=finite_number)
    for name in ("play", "stop"):
        command = commands.add_parser(name)
        command.add_argument("--confirm", action="store_true")
    run = commands.add_parser("run-luau", help="Execută cod de încredere; nu este un sandbox și nu oferă undo universal")
    run.add_argument("file", type=Path)
    run.add_argument("--context", choices=("Edit", "Client", "Server"), default="Edit")
    run.add_argument("--confirm", action="store_true")
    part = commands.add_parser("create-part")
    part.add_argument("name")
    part.add_argument("--parent", default="Workspace")
    part.add_argument("--position", nargs=3, type=finite_number, default=[0.0, 5.0, 0.0])
    part.add_argument("--size", nargs=3, type=finite_number, default=[4.0, 1.0, 4.0])
    part.add_argument("--color", nargs=3, type=finite_number, default=[65.0, 145.0, 240.0])
    part.add_argument("--confirm", action="store_true")
    edit = commands.add_parser("edit-script", help="Citește scriptul, apoi aplică editări exacte prin multi_edit")
    edit.add_argument("path")
    edit.add_argument("edits_file", type=Path, help='JSON: {"edits": [{"old_string": "...", "new_string": "..."}]}')
    edit.add_argument("--confirm", action="store_true")
    call = commands.add_parser("call", help="Apel explicit către orice instrument oferit de server")
    call.add_argument("name")
    args_group = call.add_mutually_exclusive_group()
    args_group.add_argument("--args", default="{}")
    args_group.add_argument("--args-file", type=Path)
    call.add_argument("--allow-mutation", action="store_true")
    call.add_argument("--allow-generation", action="store_true")
    return root


def build_call(options: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    command = options.command
    if command == "state":
        return "get_studio_state", {}
    if command == "logs":
        return "get_console_output", {}
    if command == "tree":
        if not 1 <= options.limit <= 200:
            raise ValueError("Limita arborelui trebuie să fie între 1 și 200.")
        result = {"path": options.path, "max_depth": options.depth, "head_limit": options.limit}
        if options.class_name:
            result["instance_type"] = options.class_name
        if options.keywords:
            result["keywords"] = options.keywords
        return "search_game_tree", result
    if command == "inspect":
        return "inspect_instance", {"path": options.path}
    if command == "read-script":
        result = {"target_file": options.path, "should_read_entire_file": True}
        if options.start is not None or options.end is not None:
            if options.start is None or options.end is None or not 1 <= options.start <= options.end:
                raise ValueError("Citirea parțială necesită --start și --end, cu 1 ≤ start ≤ end.")
            result.update(should_read_entire_file=False, start_line_one_indexed=options.start, end_line_one_indexed_inclusive=options.end)
        return "script_read", result
    if command == "find-script":
        return "script_search", {"keywords": options.keywords}
    if command == "grep":
        return "script_grep", {"query": options.query}
    if command == "capture":
        if bool(options.camera) != bool(options.look_at):
            raise ValueError("Folosește --camera și --look-at împreună.")
        result = {"capture_id": "Harness_" + uuid.uuid4().hex}
        if options.camera:
            result.update(camera_position=options.camera, look_at_position=options.look_at)
        return "screen_capture", result
    if command in ("play", "stop"):
        return "start_stop_play", {"is_start": command == "play"}
    if command == "run-luau":
        return "execute_luau", {"code": options.file.read_text(encoding="utf-8-sig"), "datamodel_type": options.context}
    if command == "create-part":
        return "execute_luau", {"code": create_part_code(options.name, options.parent, options.position, options.size, options.color), "datamodel_type": "Edit"}
    if command == "edit-script":
        edits = json_object(options.edits_file.read_text(encoding="utf-8-sig")).get("edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError("Fișierul trebuie să conțină o listă edits nevidă.")
        for edit in edits:
            if not isinstance(edit, dict) or not isinstance(edit.get("old_string"), str) or not edit["old_string"] or not isinstance(edit.get("new_string"), str):
                raise ValueError("Fiecare editare necesită old_string nevid și new_string de tip text.")
            if "replace_all" in edit and not isinstance(edit["replace_all"], bool):
                raise ValueError("replace_all trebuie să fie boolean.")
        return "multi_edit", {"file_path": options.path, "edits": edits, "datamodel_type": "Edit"}
    if command == "call":
        arguments = json_object(options.args_file.read_text(encoding="utf-8-sig") if options.args_file else options.args)
        return options.name, arguments
    raise ValueError("Comandă necunoscută.")


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv)
    try:
        if not 0 < options.timeout <= 300:
            raise ValueError("Timeout trebuie să fie între 0 și 300 de secunde.")
        if not 0 <= options.connect_wait <= 60:
            raise ValueError("Fereastra de reconectare trebuie să fie între 0 și 60 de secunde.")
        invocation = None
        if options.command not in ("doctor", "tools"):
            name, arguments = build_call(options)
            invocation = (name, validate_target(
                name, arguments, options.studio,
                getattr(options, "confirm", False) or getattr(options, "allow_mutation", False),
                getattr(options, "allow_generation", False),
                options.datamodel,
            ))
        with McpClient(studio_command(), timeout=options.timeout) as client:
            tools = client.list_tools()
            available = {tool["name"]: tool for tool in tools}
            if options.command == "tools":
                if options.name:
                    if options.name not in available:
                        raise McpError("Instrumentul cerut nu este oferit de server.")
                    output: Any = available[options.name]
                else:
                    output = {"server": client.server_info, "tools": sorted(available)}
            elif options.command == "doctor":
                if "list_roblox_studios" not in available:
                    raise McpError("Serverul nu oferă list_roblox_studios.")
                studios = wait_for_studios(client, options.connect_wait)
                output = {"server": client.server_info, "protocol": client.protocol_version,
                          "tool_count": len(tools), "studio_connected": bool(studios), "studios": studios}
                if not studios:
                    output["next_step"] = "Serverul răspunde, dar nicio instanță Studio nu este conectată. Verifică activarea MCP în Studio."
                    print(json.dumps(output, ensure_ascii=False, indent=2))
                    return 2
            else:
                assert invocation is not None
                name, arguments = invocation
                if name not in available:
                    raise McpError("Instrumentul cerut nu este oferit de server.")
                if name not in NO_STUDIO_TOOLS:
                    studios = wait_for_studios(client, options.connect_wait, options.studio)
                    if not any(studio["id"] == options.studio for studio in studios):
                        raise McpError("Instanța aleasă nu este conectată. Rulează doctor și folosește ID-ul actual.")
                if options.command == "edit-script":
                    before = client.call_tool("script_read", {"target_file": options.path, "studio_id": options.studio, "datamodel_type": "Edit"})
                    if before.get("isError"):
                        print(json.dumps(before, ensure_ascii=False, indent=2))
                        return 1
                result = client.call_tool(name, arguments)
                output = materialize_images(result, options.output_dir)
                if result.get("isError"):
                    print(json.dumps(output, ensure_ascii=False, indent=2))
                    return 1
            print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (McpError, ValueError, OSError) as error:
        print(f"Eroare: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
