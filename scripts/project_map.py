"""Harta proiectului Roblox (0.7) și cheia de workspace (1.0). Model pur, fără rețea.

Clasificarea: inventarul compact primit de la plugin este `nodes = [[id, parent, className, name, flags]]`, cu `id` = indexul din listă,
`parent` = -1 pentru rădăcinile serviciilor și `flags` un bitmask (1 script cu Source, 2 are atribute, 4 RunContext Client,
8 RunContext Server). Fiecare nod ajunge într-o singură grupă: întâi după `className`, apoi după serviciul rădăcină.

Workspace-ul = jocul: `workspace_key(game_id, place_id)` și `workspace_meta(body)` (din corpul `/v1/identity`) sunt folosite de daemon
și de hub ca să grupeze totul pe joc; cheia se calculează mereu din id-uri, niciodată din ce declară clientul.
"""

from __future__ import annotations

from typing import Any

MAX_ENTRIES = 500
MAX_NODES = 20000
MAX_TEXT = 200
FLAG_SOURCE = 1
FLAG_ATTRIBUTES = 2
FLAG_CLIENT = 4
FLAG_SERVER = 8

# Workspace (1.0): id-urile Roblox rămân sub 2^53 ca panoul (JavaScript) să le reprezinte exact.
MAX_ID = 2**53 - 1
MAX_WORKSPACE_NAME = 120
LOCAL_WORKSPACE = "local"
CREATOR_UNKNOWN = "necunoscut"
CREATOR_TYPES = ("User", "Group", CREATOR_UNKNOWN)

# Ordinea este și ordinea grupelor din rezultat.
GROUP_LABELS = {
    "graphics": "Grafică", "assets": "Asseturi", "audio": "Audio", "ui": "Interfață",
    "scripts_server": "Scripturi server", "scripts_client": "Scripturi client", "scripts_shared": "Module partajate",
    "networking": "Rețea", "data": "Date", "physics": "Fizică", "gameplay": "Gameplay", "settings": "Setări", "other": "Altele",
}
GROUP_KEYS = tuple(GROUP_LABELS)

GRAPHICS_CLASSES = frozenset({
    "Lighting", "Atmosphere", "Sky", "Clouds", "Bloom", "BloomEffect", "ColorCorrection", "ColorCorrectionEffect",
    "DepthOfField", "DepthOfFieldEffect", "SunRays", "SunRaysEffect", "BlurEffect", "Terrain", "MaterialService", "MaterialVariant",
    "Decal", "Texture", "SurfaceAppearance", "ParticleEmitter", "Beam", "Trail", "Highlight", "Fire", "Smoke", "Sparkles",
    "PointLight", "SpotLight", "SurfaceLight",
})
ASSET_CLASSES = frozenset({
    "MeshPart", "Model", "Part", "UnionOperation", "NegateOperation", "IntersectOperation", "WedgePart", "CornerWedgePart",
    "TrussPart", "Accessory", "Tool", "Animation", "AnimationController", "Animator", "Shirt", "Pants", "ShirtGraphic", "Bone",
})
AUDIO_CLASSES = frozenset({"Sound", "SoundGroup"})
UI_CLASSES = frozenset({
    "ScreenGui", "BillboardGui", "SurfaceGui", "Frame", "ScrollingFrame", "CanvasGroup", "TextLabel", "TextButton", "TextBox",
    "ImageLabel", "ImageButton", "ViewportFrame", "VideoFrame",
})
SCRIPT_CLASSES = frozenset({"Script", "LocalScript", "ModuleScript"})
NETWORKING_CLASSES = frozenset({"RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent", "BindableEvent", "BindableFunction"})
DATA_CLASSES = frozenset({"Configuration"})
PHYSICS_CLASSES = frozenset({
    "Weld", "ManualWeld", "WeldConstraint", "Motor6D", "Motor", "Snap", "Glue", "Rotate", "RotateP", "RotateV", "VelocityMotor",
    "BodyVelocity", "BodyPosition", "BodyGyro", "BodyAngularVelocity", "BodyForce", "BodyThrust", "RocketPropulsion",
    "LinearVelocity", "AngularVelocity", "AlignPosition", "AlignOrientation", "VectorForce", "Torque", "LineForce",
})
GAMEPLAY_CLASSES = frozenset({
    "SpawnLocation", "Seat", "VehicleSeat", "Humanoid", "HumanoidDescription", "ProximityPrompt", "ClickDetector", "DragDetector",
    "Team", "Teams", "StarterPack", "StarterPlayerScripts", "StarterCharacterScripts",
})
# Părțile sub care un Attachment ține de fizică (sudură, constrângeri), nu de altceva.
PART_CLASSES = frozenset({
    "Part", "MeshPart", "UnionOperation", "NegateOperation", "IntersectOperation", "WedgePart", "CornerWedgePart", "TrussPart",
    "SpawnLocation", "Seat", "VehicleSeat", "Terrain",
})
SERVER_ROOTS = frozenset({"ServerScriptService", "ServerStorage"})
SHARED_ROOTS = frozenset({"ReplicatedStorage", "ReplicatedFirst"})
CLIENT_ROOTS = frozenset({"StarterPlayer", "StarterGui", "StarterPack"})
ROOT_GROUPS = {"Lighting": "graphics", "MaterialService": "graphics", "SoundService": "audio", "StarterGui": "ui",
               "StarterPack": "gameplay", "Teams": "gameplay"}


def validate_nodes(nodes: Any, limit: int = MAX_NODES) -> None:
    """Ridică ValueError dacă inventarul nu are formatul compact al contractului."""
    if not isinstance(nodes, list):
        raise ValueError("nodes trebuie să fie o listă de noduri [id, parent, className, name, flags].")
    if len(nodes) > limit:
        raise ValueError(f"Inventarul depășește plafonul de {limit} noduri.")
    for index, node in enumerate(nodes):
        if not isinstance(node, list) or len(node) != 5:
            raise ValueError(f"Nodul {index} trebuie să fie o listă cu 5 elemente: [id, parent, className, name, flags].")
        identifier, parent, class_name, name, flags = node
        if type(identifier) is not int or identifier != index:
            raise ValueError(f"Nodul {index} are id {identifier!r}; id-urile trebuie să fie consecutive de la 0.")
        if type(parent) is not int or parent < -1 or parent >= index:
            raise ValueError(f"Nodul {index} are părintele {parent!r}; părintele trebuie să fie -1 sau un id mai mic.")
        if not isinstance(class_name, str) or not class_name or len(class_name) > MAX_TEXT:
            raise ValueError(f"Nodul {index} are un className invalid (text nevid de cel mult {MAX_TEXT} caractere).")
        if not isinstance(name, str) or len(name) > MAX_TEXT:
            raise ValueError(f"Nodul {index} are un nume invalid (text de cel mult {MAX_TEXT} caractere).")
        if type(flags) is not int or flags < 0:
            raise ValueError(f"Nodul {index} are flags {flags!r}; flags trebuie să fie un întreg nenegativ.")


def _script_group(class_name: str, root: str, flags: int) -> str:
    if class_name == "LocalScript" or flags & FLAG_CLIENT:
        return "scripts_client"
    if flags & FLAG_SERVER or root in SERVER_ROOTS:
        return "scripts_server"
    if root in CLIENT_ROOTS:
        return "scripts_client"
    # ModuleScript în ReplicatedStorage/ReplicatedFirst (sau oriunde altundeva) este modul partajat; Script fără context este legacy server.
    return "scripts_shared" if class_name == "ModuleScript" else "scripts_server"


def group_of(class_name: str, name: str, flags: int, root: str, parent_class: str | None, is_root: bool) -> str:
    """Grupa unui nod: întâi după className (cu contextul minim cerut de contract), apoi după serviciul rădăcină."""
    if class_name in SCRIPT_CLASSES:
        return _script_group(class_name, root, flags)
    if class_name in GRAPHICS_CLASSES:
        return "graphics"
    if class_name in UI_CLASSES or class_name.startswith("UI"):
        return "ui"
    if class_name in AUDIO_CLASSES or class_name.startswith("Audio") or class_name.endswith("SoundEffect"):
        return "audio"
    if class_name in NETWORKING_CLASSES:
        return "networking"
    if class_name in DATA_CLASSES or class_name.endswith("Value"):
        return "data"
    if class_name in PHYSICS_CLASSES or class_name.endswith("Constraint"):
        return "physics"
    if class_name == "Attachment" and parent_class in PART_CLASSES:
        return "physics"
    if class_name in GAMEPLAY_CLASSES or (root == "StarterPlayer" and name == "StarterCharacter" and parent_class == "StarterPlayer"):
        return "gameplay"
    if class_name in ASSET_CLASSES:
        return "assets"
    if class_name == "Folder" and root in SERVER_ROOTS | SHARED_ROOTS:
        return "data"
    if is_root:
        # Rădăcinile serviciilor (Workspace, SoundService, Chat, TextChatService, StarterPlayer…) sunt noduri de configurare.
        return "settings"
    return ROOT_GROUPS.get(root, "other")


def classify(nodes: list[list[Any]]) -> dict[str, dict[str, Any]]:
    """`{key: {key, label, count, by_class, entries}}` în ordinea grupelor; cel mult MAX_ENTRIES intrări per grupă."""
    validate_nodes(nodes)
    groups = {key: {"key": key, "label": label, "count": 0, "by_class": {}, "entries": []} for key, label in GROUP_LABELS.items()}
    paths: list[str] = []
    roots: list[str] = []
    for identifier, parent, class_name, name, flags in nodes:
        if parent < 0:
            path, root, parent_class = name, class_name, None
        else:
            path, root, parent_class = paths[parent] + "." + name, roots[parent], nodes[parent][2]
        paths.append(path)
        roots.append(root)
        group = groups[group_of(class_name, name, flags, root, parent_class, parent < 0)]
        group["count"] += 1
        group["by_class"][class_name] = group["by_class"].get(class_name, 0) + 1
        if len(group["entries"]) < MAX_ENTRIES:
            group["entries"].append({"id": identifier, "path": path, "className": class_name, "name": name})
    return groups


def summary(groups: Any) -> dict[str, int]:
    """`{key: count}` din rezultatul lui `classify` (dicționar) sau dintr-o listă de grupe."""
    rows = groups.values() if isinstance(groups, dict) else (groups or [])
    result = {}
    for group in rows:
        if isinstance(group, dict) and isinstance(group.get("key"), str):
            count = group.get("count", 0)
            result[group["key"]] = count if type(count) is int else 0
    return result


def overview(project: dict[str, Any] | None, sample: int = 50) -> dict[str, Any] | None:
    """Sumarul pentru agenți: fiecare grupă cu etichetă, număr, clase și primele `sample` căi."""
    if not project:
        return None
    groups = {}
    for key, group in (project.get("groups") or {}).items():
        if not isinstance(group, dict):
            continue
        entries = group.get("entries") or []
        groups[key] = {"label": group.get("label", GROUP_LABELS.get(key, key)), "count": group.get("count", 0),
                       "by_class": dict(group.get("by_class") or {}),
                       "sample": [entry["path"] for entry in entries[:sample] if isinstance(entry, dict) and isinstance(entry.get("path"), str)]}
    return {"snapshot_id": project.get("snapshot_id"), "place_name": project.get("place_name"), "place_id": project.get("place_id"),
            "developer": project.get("developer"), "machine": project.get("machine"), "taken": project.get("taken"),
            "count": project.get("count", 0), "truncated": bool(project.get("truncated")), "groups": groups}


def _workspace_id(value: Any, name: str) -> int:
    """Id Roblox: întreg nenegativ (sau lipsă = 0). Un float integral (JSON-ul din Luau) este acceptat; bool, text și restul nu."""
    if value is None:
        return 0
    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is not int:
        raise ValueError(f"Câmpul {name} trebuie să fie un întreg nenegativ.")
    if value < 0 or value > MAX_ID:
        raise ValueError(f"Câmpul {name} trebuie să fie între 0 și {MAX_ID}.")
    return value


def _workspace_name(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Numele workspace-ului trebuie să fie text.")
    cleaned = "".join(" " if ord(character) < 32 or ord(character) == 127 else character for character in value)
    return cleaned.strip()[:MAX_WORKSPACE_NAME].rstrip()


def _creator_type(value: Any) -> str:
    if value is None:
        return CREATOR_UNKNOWN
    if not isinstance(value, str):
        raise ValueError("Câmpul creator_type trebuie să fie text.")
    lowered = value.strip().lower()
    for known in CREATOR_TYPES:
        if lowered == known.lower():
            return known
    return CREATOR_UNKNOWN


def workspace_key(game_id: Any, place_id: Any) -> str:
    """Cheia workspace-ului: `game:<game_id>` pentru un joc publicat, altfel `place:<place_id>`, altfel `local` (fișier nepublicat)."""
    game, place = _workspace_id(game_id, "game_id"), _workspace_id(place_id, "place_id")
    if game > 0:
        return "game:" + str(game)
    if place > 0:
        return "place:" + str(place)
    return LOCAL_WORKSPACE


def workspace_meta(body: Any) -> dict[str, Any]:
    """Meta workspace-ului `{key, game_id, place_id, name, creator_id, creator_type}` din corpul `/v1/identity` sau dintr-un meta deja calculat.

    Numele afișat este `place_name` (ultimul raportat de plugin); când corpul este deja un meta, `name`. Câmpurile lipsă devin 0, "" și
    `necunoscut`; tipurile greșite ridică ValueError, numele se curăță de caractere de control și se scurtează la MAX_WORKSPACE_NAME,
    iar `creator_type` se normalizează la User/Group/necunoscut. Cheia se recalculează din id-uri: o cheie trimisă de client se ignoră.
    """
    if not isinstance(body, dict):
        raise ValueError("Meta workspace-ului trebuie să fie un obiect JSON.")
    game_id = _workspace_id(body.get("game_id"), "game_id")
    place_id = _workspace_id(body.get("place_id"), "place_id")
    creator_id = _workspace_id(body.get("creator_id"), "creator_id")
    name = _workspace_name(body["place_name"] if "place_name" in body else body.get("name"))
    return {"key": workspace_key(game_id, place_id), "game_id": game_id, "place_id": place_id, "name": name,
            "creator_id": creator_id, "creator_type": _creator_type(body.get("creator_type"))}
