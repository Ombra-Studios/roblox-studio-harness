import os
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_studio_plugin import build


class PluginPackageTests(unittest.TestCase):
    def test_source_round_trip_with_unicode_xml_and_cdata_end(self):
        source_code = '-- plugin de test\nlocal x = "Știință < & > ]]>"\n'
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temp:
            source = Path(temp) / "Plugin.server.luau"
            source.write_text(source_code, encoding="utf-8")
            destination = build(source, Path(temp) / "dist" / "StudioHarness.rbxmx")
            root = ET.parse(destination).getroot()
            self.assertEqual(root.tag, "roblox")
            self.assertEqual(root.attrib["version"], "4")
            self.assertEqual(root.find(".//ProtectedString[@name='Source']").text, source_code)
            self.assertEqual(root.find(".//Item[@class='Script']/Properties/bool[@name='Disabled']").text, "false")

    def test_modules_are_packaged_as_siblings_under_modules_folder(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temp:
            base = Path(temp)
            source = base / "StudioHarness.server.luau"
            source.write_text("require(script.Parent.Modules.Controller).start(plugin)\n", encoding="utf-8")
            modules = base / "modules"
            (modules / "Views").mkdir(parents=True)
            (modules / "Controller.luau").write_text('return {start = function(plugin) end}\n', encoding="utf-8")
            (modules / "Views" / "Theme.luau").write_text('return {text = "Știință < & >"}\n', encoding="utf-8")
            (base / "tests").mkdir()
            (base / "tests" / "NotPackaged.luau").write_text("error('test only')", encoding="utf-8")
            root = ET.parse(build(source, base / "result.rbxmx")).getroot()
            model = root.find("Item[@class='Model']")
            folders = model.findall("Item[@class='Folder']")
            self.assertEqual(len(folders), 1)
            self.assertEqual(folders[0].find("Properties/string[@name='Name']").text, "Modules")
            names = [node.find("Properties/string[@name='Name']").text for node in root.iter("Item")]
            self.assertIn("Controller", names)
            self.assertIn("Theme", names)
            self.assertNotIn("NotPackaged", names)
            refs = [node.attrib["referent"] for node in root.iter("Item")]
            self.assertEqual(len(refs), len(set(refs)))
            sources = [node.text for node in root.iter("ProtectedString")]
            self.assertIn('return {text = "Știință < & >"}\n', sources)

    def test_module_folder_name_collision_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temp:
            base = Path(temp)
            source = base / "main.luau"
            source.write_text("-- plugin\n", encoding="utf-8")
            (base / "modules" / "Views").mkdir(parents=True)
            (base / "modules" / "Views.luau").write_text("return {}", encoding="utf-8")
            (base / "modules" / "Views" / "Theme.luau").write_text("return {}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "același nume"):
                build(source, base / "invalid.rbxmx")
            self.assertFalse((base / "invalid.rbxmx").exists())

    def test_empty_module_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temp:
            base = Path(temp)
            source = base / "main.luau"
            source.write_text("-- plugin\n", encoding="utf-8")
            (base / "modules").mkdir()
            (base / "modules" / "Empty.luau").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                build(source, base / "invalid.rbxmx")

    def test_empty_source_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temp:
            source = Path(temp) / "empty.luau"
            source.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                build(source, Path(temp) / "invalid.rbxmx")
            self.assertFalse((Path(temp) / "invalid.rbxmx").exists())


if __name__ == "__main__":
    unittest.main()
