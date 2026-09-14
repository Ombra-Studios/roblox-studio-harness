"""Harta proiectului (0.7) și cheia de workspace (1.0): clasificarea inventarului compact și meta workspace-ului. Model pur, fără daemon și fără rețea."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project_map
from project_map import GROUP_KEYS, GROUP_LABELS, MAX_ENTRIES, classify, overview, summary, validate_nodes, workspace_key, workspace_meta


class Tree:
    """Construiește inventarul compact în ordinea contractului: id = index, părintele are id mai mic."""

    def __init__(self):
        self.nodes = []

    def add(self, parent, class_name, name=None, flags=0):
        identifier = len(self.nodes)
        self.nodes.append([identifier, -1 if parent is None else parent, class_name, class_name if name is None else name, flags])
        return identifier

    def root(self, class_name):
        return self.add(None, class_name)


def where(groups, path):
    """Cheia grupei în care a ajuns calea (o singură grupă per nod)."""
    found = [key for key, group in groups.items() if any(entry["path"] == path for entry in group["entries"])]
    assert len(found) == 1, (path, found)
    return found[0]


class ClassifyTests(unittest.TestCase):
    def test_groups_come_in_contract_order_with_romanian_labels_even_when_empty(self):
        groups = classify([])
        self.assertEqual(tuple(groups), GROUP_KEYS)
        self.assertEqual(GROUP_KEYS, ("graphics", "assets", "audio", "ui", "scripts_server", "scripts_client", "scripts_shared",
                                      "networking", "data", "physics", "gameplay", "settings", "other"))
        self.assertEqual([group["label"] for group in groups.values()],
                         ["Grafică", "Asseturi", "Audio", "Interfață", "Scripturi server", "Scripturi client", "Module partajate",
                          "Rețea", "Date", "Fizică", "Gameplay", "Setări", "Altele"])
        for key, group in groups.items():
            self.assertEqual(group, {"key": key, "label": GROUP_LABELS[key], "count": 0, "by_class": {}, "entries": []})
        self.assertEqual(summary(groups), {key: 0 for key in GROUP_KEYS})

    def test_graphics_by_class_and_under_lighting_or_material_service(self):
        tree = Tree()
        lighting = tree.root("Lighting")
        for class_name in ("Atmosphere", "Sky", "Clouds", "BloomEffect", "ColorCorrectionEffect", "DepthOfFieldEffect", "SunRaysEffect", "BlurEffect"):
            tree.add(lighting, class_name)
        tree.add(lighting, "Folder", "Presets")
        workspace = tree.root("Workspace")
        tree.add(workspace, "Terrain")
        part = tree.add(workspace, "Part", "Wall")
        for class_name in ("Decal", "Texture", "SurfaceAppearance", "ParticleEmitter", "Beam", "Trail", "Highlight", "Fire", "Smoke", "Sparkles",
                           "PointLight", "SpotLight", "SurfaceLight"):
            tree.add(part, class_name)
        material = tree.root("MaterialService")
        tree.add(material, "MaterialVariant", "Grass")
        groups = classify(tree.nodes)
        graphics = groups["graphics"]
        self.assertEqual(graphics["count"], 1 + 8 + 1 + 1 + 13 + 1 + 1)
        self.assertEqual(where(groups, "Lighting"), "graphics")
        self.assertEqual(where(groups, "Lighting.Presets"), "graphics")
        self.assertEqual(where(groups, "Workspace.Terrain"), "graphics")
        self.assertEqual(where(groups, "Workspace.Wall.SpotLight"), "graphics")
        self.assertEqual(where(groups, "MaterialService"), "graphics")
        self.assertEqual(where(groups, "MaterialService.Grass"), "graphics")
        self.assertEqual(where(groups, "Workspace.Wall"), "assets")
        self.assertEqual(where(groups, "Workspace"), "settings")
        self.assertEqual(graphics["by_class"]["Folder"], 1)

    def test_assets_by_class_anywhere(self):
        tree = Tree()
        workspace, storage = tree.root("Workspace"), tree.root("ServerStorage")
        model = tree.add(workspace, "Model", "Map")
        for class_name in ("MeshPart", "Part", "UnionOperation", "WedgePart", "CornerWedgePart", "TrussPart"):
            tree.add(model, class_name)
        tool = tree.add(storage, "Tool", "Sword")
        tree.add(tool, "Animation", "Swing")
        rig = tree.add(storage, "Model", "Rig")
        controller = tree.add(rig, "AnimationController")
        tree.add(controller, "Animator")
        for class_name in ("Accessory", "Shirt", "Pants", "ShirtGraphic", "Bone"):
            tree.add(rig, class_name)
        groups = classify(tree.nodes)
        self.assertEqual(groups["assets"]["count"], 1 + 6 + 2 + 1 + 2 + 5)
        self.assertEqual(where(groups, "Workspace.Map.MeshPart"), "assets")
        self.assertEqual(where(groups, "ServerStorage.Sword.Swing"), "assets")
        self.assertEqual(where(groups, "ServerStorage.Rig.AnimationController.Animator"), "assets")
        self.assertEqual(groups["assets"]["by_class"], {"Model": 2, "MeshPart": 1, "Part": 1, "UnionOperation": 1, "WedgePart": 1, "CornerWedgePart": 1,
                                                        "TrussPart": 1, "Tool": 1, "Animation": 1, "AnimationController": 1, "Animator": 1,
                                                        "Accessory": 1, "Shirt": 1, "Pants": 1, "ShirtGraphic": 1, "Bone": 1})

    def test_audio_by_class_prefix_suffix_and_under_sound_service(self):
        tree = Tree()
        sound = tree.root("SoundService")
        group = tree.add(sound, "SoundGroup", "Music")
        tree.add(group, "Folder", "Playlists")
        workspace = tree.root("Workspace")
        part = tree.add(workspace, "Part", "Speaker")
        music = tree.add(part, "Sound", "Loop")
        for class_name in ("ReverbSoundEffect", "EchoSoundEffect", "EqualizerSoundEffect"):
            tree.add(music, class_name)
        for class_name in ("AudioPlayer", "AudioEmitter", "AudioDeviceInput"):
            tree.add(part, class_name)
        groups = classify(tree.nodes)
        self.assertEqual(groups["audio"]["count"], 1 + 1 + 1 + 3 + 3)
        self.assertEqual(where(groups, "SoundService.Music.Playlists"), "audio")
        self.assertEqual(where(groups, "Workspace.Speaker.Loop.EchoSoundEffect"), "audio")
        self.assertEqual(where(groups, "Workspace.Speaker.AudioEmitter"), "audio")
        # Rădăcina SoundService este nod de configurare.
        self.assertEqual(where(groups, "SoundService"), "settings")

    def test_ui_by_class_prefix_and_everything_under_starter_gui(self):
        tree = Tree()
        gui = tree.root("StarterGui")
        screen = tree.add(gui, "ScreenGui", "HUD")
        frame = tree.add(screen, "Frame", "Root")
        for class_name in ("ScrollingFrame", "TextLabel", "TextButton", "TextBox", "ImageLabel", "ImageButton", "ViewportFrame", "VideoFrame",
                           "UIListLayout", "UICorner", "UIStroke", "UIPadding", "UISizeConstraint", "UIAspectRatioConstraint"):
            tree.add(frame, class_name)
        tree.add(gui, "Folder", "Themes")
        tree.add(gui, "Configuration", "Layout")
        workspace = tree.root("Workspace")
        part = tree.add(workspace, "Part", "Sign")
        tree.add(part, "SurfaceGui")
        tree.add(part, "BillboardGui")
        groups = classify(tree.nodes)
        self.assertEqual(where(groups, "StarterGui.HUD.Root.UISizeConstraint"), "ui")
        self.assertEqual(where(groups, "StarterGui.HUD.Root.UIAspectRatioConstraint"), "ui")
        self.assertEqual(where(groups, "StarterGui.Themes"), "ui")
        self.assertEqual(where(groups, "Workspace.Sign.SurfaceGui"), "ui")
        self.assertEqual(where(groups, "Workspace.Sign.BillboardGui"), "ui")
        # className are prioritate față de rădăcină: Configuration sub StarterGui rămâne în date.
        self.assertEqual(where(groups, "StarterGui.Layout"), "data")
        self.assertEqual(where(groups, "StarterGui"), "settings")
        self.assertEqual(groups["ui"]["count"], 1 + 1 + 14 + 1 + 2)
        self.assertEqual(groups["physics"]["count"], 0)

    def test_scripts_by_class_flags_and_service(self):
        tree = Tree()
        sss, storage, replicated, first = tree.root("ServerScriptService"), tree.root("ServerStorage"), tree.root("ReplicatedStorage"), tree.root("ReplicatedFirst")
        player, gui, pack, workspace = tree.root("StarterPlayer"), tree.root("StarterGui"), tree.root("StarterPack"), tree.root("Workspace")
        tree.add(sss, "Script", "Main", 1)
        tree.add(sss, "ModuleScript", "ServerUtil", 1)
        tree.add(storage, "Script", "Disabled", 1)
        tree.add(replicated, "ModuleScript", "Shared", 1)
        tree.add(first, "ModuleScript", "Loader", 1)
        tree.add(replicated, "Script", "ClientContext", 1 | 4)
        tree.add(replicated, "Script", "ServerContext", 1 | 8)
        tree.add(replicated, "LocalScript", "Local", 1)
        scripts = tree.add(player, "StarterPlayerScripts")
        tree.add(scripts, "LocalScript", "Input", 1)
        tree.add(scripts, "ModuleScript", "ClientUtil", 1)
        screen = tree.add(gui, "ScreenGui", "HUD")
        tree.add(screen, "Script", "GuiScript", 1)
        tool = tree.add(pack, "Tool", "Sword")
        tree.add(tool, "Script", "ToolScript", 1)
        tree.add(workspace, "Script", "Legacy", 1)
        tree.add(workspace, "ModuleScript", "WorldModule", 1)
        groups = classify(tree.nodes)
        self.assertEqual({entry["path"] for entry in groups["scripts_server"]["entries"]},
                         {"ServerScriptService.Main", "ServerScriptService.ServerUtil", "ServerStorage.Disabled", "ReplicatedStorage.ServerContext",
                          "Workspace.Legacy"})
        self.assertEqual({entry["path"] for entry in groups["scripts_client"]["entries"]},
                         {"ReplicatedStorage.ClientContext", "ReplicatedStorage.Local", "StarterPlayer.StarterPlayerScripts.Input",
                          "StarterPlayer.StarterPlayerScripts.ClientUtil", "StarterGui.HUD.GuiScript", "StarterPack.Sword.ToolScript"})
        self.assertEqual({entry["path"] for entry in groups["scripts_shared"]["entries"]},
                         {"ReplicatedStorage.Shared", "ReplicatedFirst.Loader", "Workspace.WorldModule"})
        self.assertEqual(groups["scripts_server"]["by_class"], {"Script": 4, "ModuleScript": 1})
        self.assertEqual(where(groups, "StarterPlayer.StarterPlayerScripts"), "gameplay")

    def test_networking_data_and_folders_by_service(self):
        tree = Tree()
        replicated, storage, workspace, gui = tree.root("ReplicatedStorage"), tree.root("ServerStorage"), tree.root("Workspace"), tree.root("StarterGui")
        remotes = tree.add(replicated, "Folder", "Remotes")
        for class_name in ("RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent", "BindableEvent", "BindableFunction"):
            tree.add(remotes, class_name)
        config = tree.add(storage, "Configuration", "Settings")
        for class_name in ("StringValue", "NumberValue", "IntValue", "BoolValue", "ObjectValue", "CFrameValue", "Vector3Value", "Color3Value",
                           "BrickColorValue", "RayValue"):
            tree.add(config, class_name)
        tree.add(storage, "Folder", "Data")
        tree.add(workspace, "Folder", "Zones")
        tree.add(gui, "Folder", "Screens")
        tree.add(workspace, "IntValue", "Score")
        groups = classify(tree.nodes)
        self.assertEqual(groups["networking"]["count"], 5)
        self.assertEqual(where(groups, "ReplicatedStorage.Remotes.BindableFunction"), "networking")
        self.assertEqual(groups["data"]["count"], 1 + 1 + 10 + 1 + 1)
        self.assertEqual(where(groups, "ReplicatedStorage.Remotes"), "data")
        self.assertEqual(where(groups, "ServerStorage.Data"), "data")
        self.assertEqual(where(groups, "ServerStorage.Settings.CFrameValue"), "data")
        self.assertEqual(where(groups, "Workspace.Score"), "data")
        # Folder în afara ReplicatedStorage/ServerStorage urmează rădăcina: Workspace → altele, StarterGui → interfață.
        self.assertEqual(where(groups, "Workspace.Zones"), "other")
        self.assertEqual(where(groups, "StarterGui.Screens"), "ui")

    def test_physics_by_class_suffix_and_attachments_under_parts(self):
        tree = Tree()
        workspace = tree.root("Workspace")
        part = tree.add(workspace, "Part", "Base")
        for class_name in ("HingeConstraint", "RopeConstraint", "SpringConstraint", "WeldConstraint", "Weld", "Motor6D", "BodyVelocity",
                           "BodyGyro", "LinearVelocity", "AngularVelocity", "AlignPosition", "AlignOrientation", "VectorForce", "Torque"):
            tree.add(part, class_name)
        tree.add(part, "Attachment", "Anchor")
        mesh = tree.add(workspace, "MeshPart", "Rock")
        tree.add(mesh, "Attachment", "Top")
        model = tree.add(workspace, "Model", "Rig")
        tree.add(model, "Attachment", "Loose")
        groups = classify(tree.nodes)
        self.assertEqual(groups["physics"]["count"], 14 + 2)
        self.assertEqual(where(groups, "Workspace.Base.HingeConstraint"), "physics")
        self.assertEqual(where(groups, "Workspace.Base.Anchor"), "physics")
        self.assertEqual(where(groups, "Workspace.Rock.Top"), "physics")
        # Attachment care nu stă sub o parte nu este fizică; sub Workspace ajunge la altele.
        self.assertEqual(where(groups, "Workspace.Rig.Loose"), "other")

    def test_gameplay_by_class_teams_starter_pack_and_starter_character(self):
        tree = Tree()
        workspace, teams, pack, player = tree.root("Workspace"), tree.root("Teams"), tree.root("StarterPack"), tree.root("StarterPlayer")
        for class_name in ("SpawnLocation", "Seat", "VehicleSeat", "ProximityPrompt", "ClickDetector"):
            tree.add(workspace, class_name)
        rig = tree.add(workspace, "Model", "Dummy")
        tree.add(rig, "Humanoid")
        tree.add(teams, "Team", "Red")
        tree.add(pack, "Tool", "Sword")
        tree.add(pack, "Folder", "Misc")
        character = tree.add(player, "Model", "StarterCharacter")
        tree.add(character, "Humanoid")
        tree.add(player, "StarterCharacterScripts")
        tree.add(player, "Model", "Preview")
        groups = classify(tree.nodes)
        self.assertEqual({entry["path"] for entry in groups["gameplay"]["entries"]},
                         {"Workspace.SpawnLocation", "Workspace.Seat", "Workspace.VehicleSeat", "Workspace.ProximityPrompt", "Workspace.ClickDetector",
                          "Workspace.Dummy.Humanoid", "Teams", "Teams.Red", "StarterPack", "StarterPack.Misc", "StarterPlayer.StarterCharacter",
                          "StarterPlayer.StarterCharacter.Humanoid", "StarterPlayer.StarterCharacterScripts"})
        self.assertEqual(where(groups, "StarterPack.Sword"), "assets")
        self.assertEqual(where(groups, "StarterPlayer.Preview"), "assets")
        self.assertEqual(where(groups, "StarterPlayer"), "settings")

    def test_service_roots_are_settings_and_unknown_classes_are_other(self):
        tree = Tree()
        roots = ["Workspace", "ReplicatedStorage", "ReplicatedFirst", "ServerScriptService", "ServerStorage", "StarterGui", "StarterPlayer",
                 "SoundService", "TextChatService", "Chat"]
        for class_name in roots:
            tree.root(class_name)
        workspace = 0
        tree.add(workspace, "Camera")
        chat = roots.index("TextChatService")
        tree.add(chat, "TextChannel", "General")
        tree.add(workspace, "SomethingNew", "Mystery")
        groups = classify(tree.nodes)
        self.assertEqual([entry["path"] for entry in groups["settings"]["entries"]], roots)
        self.assertEqual([entry["path"] for entry in groups["other"]["entries"]], ["Workspace.Camera", "TextChatService.General", "Workspace.Mystery"])
        self.assertEqual(groups["other"]["by_class"], {"Camera": 1, "TextChannel": 1, "SomethingNew": 1})

    def test_class_name_takes_precedence_over_the_root_service(self):
        tree = Tree()
        lighting, gui, sound = tree.root("Lighting"), tree.root("StarterGui"), tree.root("SoundService")
        tree.add(lighting, "Sound", "Ambient")
        tree.add(lighting, "Script", "DayNight", 1)
        tree.add(gui, "RemoteEvent", "Open")
        tree.add(gui, "Part", "Odd")
        tree.add(sound, "ScreenGui", "Mixer")
        tree.add(sound, "LocalScript", "Volume", 1)
        groups = classify(tree.nodes)
        self.assertEqual(where(groups, "Lighting.Ambient"), "audio")
        self.assertEqual(where(groups, "Lighting.DayNight"), "scripts_server")
        self.assertEqual(where(groups, "StarterGui.Open"), "networking")
        self.assertEqual(where(groups, "StarterGui.Odd"), "assets")
        self.assertEqual(where(groups, "SoundService.Mixer"), "ui")
        self.assertEqual(where(groups, "SoundService.Volume"), "scripts_client")

    def test_paths_follow_the_parent_chain_and_use_names_not_classes(self):
        tree = Tree()
        workspace = tree.root("Workspace")
        map_id = tree.add(workspace, "Folder", "Map")
        zone = tree.add(map_id, "Model", "Zone 3")
        part = tree.add(zone, "Part", "Ștefan.cel.Mare")
        tree.add(part, "PointLight", "")
        groups = classify(tree.nodes)
        self.assertEqual(groups["graphics"]["entries"], [{"id": 4, "path": "Workspace.Map.Zone 3.Ștefan.cel.Mare.", "className": "PointLight", "name": ""}])
        self.assertEqual(groups["assets"]["entries"], [{"id": 2, "path": "Workspace.Map.Zone 3", "className": "Model", "name": "Zone 3"},
                                                        {"id": 3, "path": "Workspace.Map.Zone 3.Ștefan.cel.Mare", "className": "Part", "name": "Ștefan.cel.Mare"}])
        self.assertEqual(groups["other"]["entries"][0]["path"], "Workspace.Map")
        # Un nod nu apare în două grupe și nu se pierde niciunul.
        self.assertEqual(sum(group["count"] for group in groups.values()), len(tree.nodes))
        self.assertEqual(sum(len(group["entries"]) for group in groups.values()), len(tree.nodes))

    def test_entries_are_capped_at_500_but_counts_and_classes_cover_everything(self):
        tree = Tree()
        workspace = tree.root("Workspace")
        for index in range(MAX_ENTRIES + 37):
            tree.add(workspace, "Part" if index % 2 else "MeshPart", "P" + str(index))
        groups = classify(tree.nodes)
        assets = groups["assets"]
        self.assertEqual(MAX_ENTRIES, 500)
        self.assertEqual(assets["count"], 537)
        self.assertEqual(len(assets["entries"]), 500)
        self.assertEqual(assets["entries"][0]["path"], "Workspace.P0")
        self.assertEqual(assets["entries"][-1]["path"], "Workspace.P499")
        self.assertEqual(assets["by_class"], {"MeshPart": 269, "Part": 268})
        self.assertEqual(summary(groups), {**{key: 0 for key in GROUP_KEYS}, "assets": 537, "settings": 1})

    def test_summary_accepts_lists_and_ignores_garbage(self):
        self.assertEqual(summary([{"key": "ui", "count": 3}, {"key": "data"}, {"count": 2}, "x", {"key": "audio", "count": "4"}]),
                         {"ui": 3, "data": 0, "audio": 0})
        self.assertEqual(summary(None), {})
        self.assertEqual(summary({}), {})

    def test_overview_samples_the_first_fifty_paths_per_group(self):
        tree = Tree()
        workspace = tree.root("Workspace")
        for index in range(60):
            tree.add(workspace, "Part", "P" + str(index))
        project = {"snapshot_id": "s1", "place_name": "Locul", "place_id": 7, "developer": "ana", "machine": "pc", "taken": 1.5,
                   "count": 61, "truncated": True, "groups": classify(tree.nodes)}
        result = overview(project)
        self.assertEqual({key: result[key] for key in ("snapshot_id", "place_name", "place_id", "developer", "machine", "taken", "count", "truncated")},
                         {"snapshot_id": "s1", "place_name": "Locul", "place_id": 7, "developer": "ana", "machine": "pc", "taken": 1.5, "count": 61, "truncated": True})
        self.assertEqual(tuple(result["groups"]), GROUP_KEYS)
        assets = result["groups"]["assets"]
        self.assertEqual((assets["label"], assets["count"], assets["by_class"], len(assets["sample"])), ("Asseturi", 60, {"Part": 60}, 50))
        self.assertEqual(assets["sample"][:2], ["Workspace.P0", "Workspace.P1"])
        self.assertNotIn("entries", assets)
        self.assertEqual(result["groups"]["settings"]["sample"], ["Workspace"])
        self.assertEqual(len(overview(project, 5)["groups"]["assets"]["sample"]), 5)
        self.assertIsNone(overview(None))


class ValidationTests(unittest.TestCase):
    def good(self):
        return [[0, -1, "Workspace", "Workspace", 0], [1, 0, "Part", "A", 3], [2, 1, "Attachment", "B", 0]]

    def test_valid_inventories_pass(self):
        validate_nodes([])
        validate_nodes(self.good())
        self.assertEqual(classify(self.good())["settings"]["count"], 1)

    def test_bad_shapes_raise_value_error(self):
        bad = {
            "nu e listă": {"a": 1},
            "nod care nu e listă": [[0, -1, "Workspace", "Workspace", 0], "x"],
            "nod cu 4 elemente": [[0, -1, "Workspace", "Workspace"]],
            "nod cu 6 elemente": [[0, -1, "Workspace", "Workspace", 0, 1]],
            "id neconsecutiv": [[0, -1, "Workspace", "Workspace", 0], [2, 0, "Part", "A", 0]],
            "id bool": [[False, -1, "Workspace", "Workspace", 0]],
            "id text": [["0", -1, "Workspace", "Workspace", 0]],
            "părinte egal": [[0, 0, "Workspace", "Workspace", 0]],
            "părinte mai mare": [[0, -1, "Workspace", "Workspace", 0], [1, 2, "Part", "A", 0], [2, 0, "Part", "B", 0]],
            "părinte -2": [[0, -2, "Workspace", "Workspace", 0]],
            "părinte text": [[0, "-1", "Workspace", "Workspace", 0]],
            "className gol": [[0, -1, "", "Workspace", 0]],
            "className lung": [[0, -1, "C" * 201, "Workspace", 0]],
            "className numeric": [[0, -1, 5, "Workspace", 0]],
            "nume lung": [[0, -1, "Workspace", "n" * 201, 0]],
            "nume None": [[0, -1, "Workspace", None, 0]],
            "flags text": [[0, -1, "Workspace", "Workspace", "1"]],
            "flags bool": [[0, -1, "Workspace", "Workspace", True]],
            "flags negativ": [[0, -1, "Workspace", "Workspace", -1]],
            "flags float": [[0, -1, "Workspace", "Workspace", 1.0]],
        }
        for label, nodes in bad.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_nodes(nodes)
            with self.subTest(label=label, via="classify"), self.assertRaises(ValueError):
                classify(nodes)

    def test_node_limit(self):
        nodes = [[index, -1 if index == 0 else 0, "Part", "P", 0] for index in range(5)]
        with self.assertRaisesRegex(ValueError, "plafonul de 4"):
            validate_nodes(nodes, limit=4)
        validate_nodes(nodes, limit=5)
        self.assertEqual(project_map.MAX_NODES, 20000)
        self.assertEqual(project_map.MAX_TEXT, 200)


IDENTITY = {"user_id": 12345, "name": "ellob", "place_id": 129160346456700, "game_id": 987654, "place_name": "Ball",
            "creator_id": 555, "creator_type": "User"}


class WorkspaceKeyTests(unittest.TestCase):
    def test_game_then_place_then_local(self):
        self.assertEqual(workspace_key(987654, 129160346456700), "game:987654")
        self.assertEqual(workspace_key(987654, 0), "game:987654")
        self.assertEqual(workspace_key(0, 129160346456700), "place:129160346456700")
        self.assertEqual(workspace_key(0, 0), "local")
        self.assertEqual(workspace_key(None, None), "local")
        self.assertEqual(workspace_key(None, 7), "place:7")
        self.assertEqual(project_map.LOCAL_WORKSPACE, "local")

    def test_integral_floats_from_luau_json_are_accepted(self):
        self.assertEqual(workspace_key(987654.0, 0), "game:987654")
        self.assertEqual(workspace_key(0.0, 1.291603464567e14), "place:129160346456700")

    def test_wrong_types_and_ranges_raise_value_error(self):
        bad = {"bool": (True, 0), "text": ("987654", 0), "text place": (0, "7"), "negativ": (-1, 0), "negativ place": (0, -5),
               "float neîntreg": (1.5, 0), "inf": (float("inf"), 0), "nan": (float("nan"), 0), "listă": ([1], 0), "dict": (0, {"id": 1}),
               "prea mare": (2**53, 0), "prea mare place": (0, 2**63)}
        for label, (game_id, place_id) in bad.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                workspace_key(game_id, place_id)
        self.assertEqual(project_map.MAX_ID, 2**53 - 1)
        self.assertEqual(workspace_key(2**53 - 1, 0), "game:" + str(2**53 - 1))


class WorkspaceMetaTests(unittest.TestCase):
    def test_meta_from_the_identity_body_of_the_brief(self):
        self.assertEqual(workspace_meta(IDENTITY), {"key": "game:987654", "game_id": 987654, "place_id": 129160346456700, "name": "Ball",
                                                    "creator_id": 555, "creator_type": "User"})

    def test_place_name_is_the_displayed_name_and_the_roblox_user_name_never_leaks(self):
        self.assertEqual(workspace_meta(IDENTITY)["name"], "Ball")
        self.assertEqual(workspace_meta({**IDENTITY, "place_name": ""})["name"], "")
        self.assertEqual(workspace_meta({**IDENTITY, "place_name": None})["name"], "")
        # Un meta deja calculat (cum îl primește hub-ul) își păstrează `name`.
        meta = workspace_meta(IDENTITY)
        self.assertEqual(workspace_meta(meta), meta)
        self.assertEqual(workspace_meta({"name": "Ball", "game_id": 1})["name"], "Ball")

    def test_missing_fields_get_neutral_defaults(self):
        self.assertEqual(workspace_meta({}), {"key": "local", "game_id": 0, "place_id": 0, "name": "", "creator_id": 0, "creator_type": "necunoscut"})
        self.assertEqual(workspace_meta({"place_id": 42, "place_name": "Baseplate"}),
                         {"key": "place:42", "game_id": 0, "place_id": 42, "name": "Baseplate", "creator_id": 0, "creator_type": "necunoscut"})
        self.assertEqual(workspace_meta({"game_id": None, "place_id": None, "creator_id": None, "creator_type": None, "place_name": None}),
                         {"key": "local", "game_id": 0, "place_id": 0, "name": "", "creator_id": 0, "creator_type": "necunoscut"})

    def test_key_is_recomputed_from_ids_never_taken_from_the_body(self):
        self.assertEqual(workspace_meta({**IDENTITY, "key": "game:1"})["key"], "game:987654")
        self.assertEqual(workspace_meta({"key": "game:1", "place_id": 9})["key"], "place:9")
        self.assertEqual(workspace_meta({"key": "game:1"})["key"], "local")
        # Câmpurile străine (user_id, name, orice altceva) nu ajung în meta.
        self.assertEqual(set(workspace_meta({**IDENTITY, "extra": 1})), {"key", "game_id", "place_id", "name", "creator_id", "creator_type"})

    def test_name_is_cleaned_and_capped_at_120_characters(self):
        self.assertEqual(project_map.MAX_WORKSPACE_NAME, 120)
        self.assertEqual(workspace_meta({"place_name": "  Ștefan cel Mare  "})["name"], "Ștefan cel Mare")
        self.assertEqual(workspace_meta({"place_name": "Ball\x00\x1f\x7f\nv2"})["name"], "Ball    v2")
        self.assertEqual(workspace_meta({"place_name": "\t\n"})["name"], "")
        long_name = "ă" * 130
        self.assertEqual(workspace_meta({"place_name": long_name})["name"], "ă" * 120)
        self.assertEqual(workspace_meta({"place_name": "a" * 119 + " b"})["name"], "a" * 119)

    def test_creator_type_is_user_group_or_unknown(self):
        self.assertEqual(project_map.CREATOR_TYPES, ("User", "Group", "necunoscut"))
        expected = {"User": "User", "Group": "Group", "necunoscut": "necunoscut", "user": "User", "GROUP": "Group", " Group ": "Group",
                    "Necunoscut": "necunoscut", "": "necunoscut", "Altceva": "necunoscut", None: "necunoscut"}
        for value, creator_type in expected.items():
            with self.subTest(value=value):
                self.assertEqual(workspace_meta({"creator_type": value})["creator_type"], creator_type)

    def test_wrong_types_raise_value_error_with_the_field_name(self):
        bad = {"corp listă": [IDENTITY], "corp None": None, "corp text": "x", "game_id text": {**IDENTITY, "game_id": "987654"},
               "game_id bool": {**IDENTITY, "game_id": True}, "place_id negativ": {**IDENTITY, "place_id": -1},
               "place_id float": {**IDENTITY, "place_id": 1.5}, "creator_id text": {**IDENTITY, "creator_id": "555"},
               "creator_id uriaș": {**IDENTITY, "creator_id": 2**53}, "creator_type număr": {**IDENTITY, "creator_type": 1},
               "creator_type listă": {**IDENTITY, "creator_type": ["User"]}, "place_name număr": {**IDENTITY, "place_name": 5},
               "place_name listă": {**IDENTITY, "place_name": ["Ball"]}, "name număr (meta)": {"name": 5}}
        for label, body in bad.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                workspace_meta(body)
        with self.assertRaisesRegex(ValueError, "creator_id"):
            workspace_meta({**IDENTITY, "creator_id": "555"})
        with self.assertRaisesRegex(ValueError, "creator_type"):
            workspace_meta({**IDENTITY, "creator_type": 1})

    def test_local_files_and_group_games(self):
        self.assertEqual(workspace_meta({"place_id": 0, "game_id": 0, "place_name": "Prototip", "creator_id": 0, "creator_type": "User"}),
                         {"key": "local", "game_id": 0, "place_id": 0, "name": "Prototip", "creator_id": 0, "creator_type": "User"})
        self.assertEqual(workspace_meta({"place_id": 10, "game_id": 20, "place_name": "Studio Echipa", "creator_id": 30, "creator_type": "Group"}),
                         {"key": "game:20", "game_id": 20, "place_id": 10, "name": "Studio Echipa", "creator_id": 30, "creator_type": "Group"})

    def test_classification_is_untouched_by_the_workspace_helpers(self):
        groups = classify([[0, -1, "Workspace", "Workspace", 0], [1, 0, "Part", "A", 0]])
        self.assertEqual((groups["settings"]["count"], groups["assets"]["count"]), (1, 1))
        self.assertEqual(tuple(groups), GROUP_KEYS)


if __name__ == "__main__":
    unittest.main()
