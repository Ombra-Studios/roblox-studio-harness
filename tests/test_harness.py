import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import roblox_harness as harness
from mcp_client import McpError


class HarnessTests(unittest.TestCase):
    def options(self, *args):
        return harness.parser().parse_args(args)

    def test_mutation_requires_confirmation(self):
        with self.assertRaisesRegex(ValueError, "modifica jocul"):
            harness.validate_target("execute_luau", {}, "studio-1", False, False)

    def test_unknown_tools_are_not_read_only(self):
        with self.assertRaises(ValueError):
            harness.validate_target("future_tool", {}, "studio-1", False, False)

    def test_studio_must_be_selected(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            harness.validate_target("inspect_instance", {}, None, False, False)

    def test_conflicting_studio_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "diferă"):
            harness.validate_target("inspect_instance", {"studio_id": "other"}, "chosen", False, False)

    def test_target_is_added_without_mutating_input(self):
        arguments = {"path": "Workspace"}
        result = harness.validate_target("inspect_instance", arguments, "chosen", False, False)
        self.assertEqual(result["studio_id"], "chosen")
        self.assertNotIn("studio_id", arguments)

    def test_listing_does_not_need_studio(self):
        self.assertEqual(harness.validate_target("list_roblox_studios", {}, None, False, False), {})

    def test_generation_requires_separate_permission(self):
        with self.assertRaisesRegex(ValueError, "credite"):
            harness.validate_target("generate_mesh", {}, "chosen", True, False)
        self.assertEqual(harness.validate_target("generate_mesh", {}, "chosen", True, True)["studio_id"], "chosen")

    def test_arbitrary_execution_is_not_classified_read_only(self):
        self.assertNotIn("execute_luau", harness.READ_TOOLS)
        self.assertNotIn("multi_edit", harness.READ_TOOLS)
        self.assertNotIn("start_stop_play", harness.READ_TOOLS)

    def test_json_object_validation(self):
        for text in ("[]", "null", '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                harness.json_object(text)
        self.assertEqual(harness.json_object('{"ok":true}'), {"ok": True})

    def test_partial_script_requires_both_indices(self):
        with self.assertRaisesRegex(ValueError, "--start și --end"):
            harness.build_call(self.options("read-script", "Main", "--start", "1"))

    def test_partial_script_indices_are_one_based(self):
        for start, end in ((0, 1), (3, 2)):
            with self.subTest(start=start), self.assertRaises(ValueError):
                harness.build_call(self.options("read-script", "Main", "--start", str(start), "--end", str(end)))
        name, args = harness.build_call(self.options("read-script", "Main", "--start", "2", "--end", "4"))
        self.assertEqual(name, "script_read")
        self.assertFalse(args["should_read_entire_file"])
        self.assertEqual(args["end_line_one_indexed_inclusive"], 4)

    def test_camera_requires_look_at(self):
        with self.assertRaisesRegex(ValueError, "împreună"):
            harness.build_call(self.options("capture", "--camera", "1", "2", "3"))

    def test_camera_vectors(self):
        name, args = harness.build_call(self.options("capture", "--camera", "1", "2", "3", "--look-at", "0", "0", "0"))
        self.assertEqual(name, "screen_capture")
        self.assertEqual(args["camera_position"], [1, 2, 3])

    def test_tree_limit(self):
        for limit in (0, 201):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                harness.build_call(self.options("tree", "--limit", str(limit)))

    def test_create_part_rejects_invalid_values(self):
        for name, size, color in (("", [1, 1, 1], [0, 0, 0]), ("A.B", [1, 1, 1], [0, 0, 0]),
                                  ("A", [0, 1, 1], [0, 0, 0]), ("A", [1, 1, 1], [256, 0, 0])):
            with self.subTest(name=name, size=size), self.assertRaises(ValueError):
                harness.create_part_code(name, "Workspace", [0, 0, 0], size, color)

    def test_create_part_does_not_overwrite_and_parents_last(self):
        code = harness.create_part_code('Piesă"test', "Workspace", [0, 5, 0], [4, 1, 4], [65, 145, 240])
        self.assertIn('assert(child.Name ~= name', code)
        self.assertIn('assert(#matches == 1', code)
        self.assertLess(code.index('part.Color ='), code.index('part.Parent ='))
        self.assertIn('if not ok then part:Destroy()', code)
        self.assertNotIn('ClearAllChildren', code)
        self.assertIn('Piesă\\"test', code)
        self.assertNotIn('\\u0103', code)

    def test_empty_studio_list_is_distinct_from_connection_failure(self):
        class Client:
            def call_tool(self, *_):
                return {"content": [{"type": "text", "text": '{"studios":[]}'}]}
        self.assertEqual(harness.connected_studios(Client()), [])

    def test_structured_studio_list(self):
        class Client:
            def call_tool(self, *_):
                return {"structuredContent": {"studios": [{"id": "chosen", "name": "Ball"}]}}
        self.assertEqual(harness.connected_studios(Client())[0]["id"], "chosen")

    def test_invalid_studio_list_is_not_silent_empty(self):
        class Client:
            def call_tool(self, *_):
                return {"content": [{"type": "text", "text": "Not valid JSON"}]}
        with self.assertRaises(McpError):
            harness.connected_studios(Client())

    def test_tool_error_in_studio_discovery(self):
        class Client:
            def call_tool(self, *_):
                return {"isError": True, "content": []}
        with self.assertRaises(McpError):
            harness.connected_studios(Client())

    def test_image_saved_without_inline_base64(self):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFYsAAAAASUVORK5CYII=")
        result = {"content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()}]}
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temporary:
            output = harness.materialize_images(result, Path(temporary))
            output2 = harness.materialize_images(result, Path(temporary))
            image = output["content"][0]
            self.assertEqual(Path(image["path"]).read_bytes(), png)
            self.assertNotIn("data", image)
            self.assertNotEqual(image["path"], output2["content"][0]["path"])

    def test_image_mime_and_base64_validation(self):
        for mime, data in (("text/html", "AAAA"), ("image/png", "!!"), ("image/png", "aGVsbG8=")):
            with self.subTest(mime=mime, data=data), self.assertRaises(McpError):
                harness.materialize_images({"content": [{"type": "image", "mimeType": mime, "data": data}]}, Path("unused"))

    def test_refused_mutation_never_starts_transport(self):
        with patch.object(harness, "studio_command") as command, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(harness.main(["--studio", "chosen", "create-part", "Part"]), 1)
            command.assert_not_called()

    def test_missing_target_never_starts_transport(self):
        with patch.object(harness, "studio_command") as command, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(harness.main(["inspect", "Workspace"]), 1)
            command.assert_not_called()

    def test_invalid_timeout_never_starts_transport(self):
        with patch.object(harness, "studio_command") as command, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(harness.main(["--timeout", "0", "doctor"]), 1)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
