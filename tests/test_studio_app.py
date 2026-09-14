"""0.8 — aplicația din Studio fără repornire: modulele Luau (citire, revizie, extragere din pachet), folderul
`Roblox\\Plugins\\StudioHarness\\app`, `GET /v1/plugin/bundle` + `status.plugin_bundle`, update-ul cu `studio_app`/`loader_version`,
verificarea builder-ului și installer-ul PowerShell.

1.0 — `LocalToken`: builder-ul pune placeholder-ul în .rbxmx, installer-ul (PowerShell și updater) îl înlocuiește cu `local-token`
doar în fișierul instalat.

Fără rețea în afara loopback-ului și fără scriere în %LOCALAPPDATA% real: LOCALAPPDATA, STUDIO_HARNESS_STATE_DIR și
STUDIO_HARNESS_STUDIO_APP_DIR sunt temporare."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_studio_plugin  # noqa: E402
import publish_release  # noqa: E402
import studio_bridge  # noqa: E402
import updater  # noqa: E402
from studio_bridge import BridgeServer, PluginBundleCache  # noqa: E402
from test_studio_bridge import UI_TOKEN, make_bridge  # noqa: E402
from test_updater import LocalChannel, make_zip  # noqa: E402
from updater import UpdateError  # noqa: E402

MODULES = {"Main": "-- Studio Harness App 0.8.0\nreturn {start = function() end}\n", "Theme": 'return {accent = "verde"}\n'}
LOADER = "-- Studio Harness Loader 1.0.0. Loader de test.\nlocal app = require(script.Parent.Modules.Main).start(plugin)\n"


def write_modules(directory, modules=MODULES, crlf=False):
    directory.mkdir(parents=True, exist_ok=True)
    for name, source in modules.items():
        (directory / (name + ".luau")).write_bytes((source.replace("\n", "\r\n") if crlf else source).encode("utf-8"))
    return directory


def temp_dir():
    return tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))


class LuauModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_read_luau_modules_reads_flat_files_normalizes_line_endings_and_skips_invalid_names(self):
        modules = write_modules(self.base / "modules", crlf=True)
        (modules / "Main.server.luau").write_text("-- nu este modul\n", encoding="utf-8")
        (modules / "notes.txt").write_text("nu\n", encoding="utf-8")
        (modules / "Views").mkdir()
        (modules / "Views" / "Nested.luau").write_text("return 1\n", encoding="utf-8")
        (modules / "WithBom.luau").write_bytes("﻿return 2\n".encode("utf-8"))
        loaded = updater.read_luau_modules(modules)
        self.assertEqual(sorted(loaded), ["Main", "Theme", "WithBom"])
        self.assertEqual(loaded["Main"], MODULES["Main"])
        self.assertEqual(loaded["WithBom"], "return 2\n")
        self.assertNotIn("\r", "".join(loaded.values()))
        self.assertEqual(updater.read_luau_modules(self.base / "lipsa"), {})

    def test_oversized_or_non_utf8_modules_are_refused(self):
        modules = write_modules(self.base / "modules")
        (modules / "Huge.luau").write_bytes(b"-" * (updater.MAX_MODULE_BYTES + 1))
        with self.assertRaisesRegex(UpdateError, "Huge"):
            updater.read_luau_modules(modules)
        (modules / "Huge.luau").unlink()
        (modules / "Bad.luau").write_bytes(b"\xff\xfe-- nu e utf-8")
        with self.assertRaisesRegex(UpdateError, "UTF-8"):
            updater.read_luau_modules(modules)

    def test_bundle_revision_is_short_hex_stable_and_content_sensitive(self):
        revision = updater.bundle_revision(MODULES)
        self.assertRegex(revision, r"^[0-9a-f]{16}$")
        self.assertEqual(updater.bundle_revision({"Theme": MODULES["Theme"], "Main": MODULES["Main"]}), revision)
        self.assertNotEqual(updater.bundle_revision({**MODULES, "Main": MODULES["Main"] + "-- x\n"}), revision)
        self.assertNotEqual(updater.bundle_revision({"Main": MODULES["Main"]}), revision)
        # Aceeași revizie indiferent de terminatorii de linie de pe disc.
        lf = updater.read_luau_modules(write_modules(self.base / "lf"))
        crlf = updater.read_luau_modules(write_modules(self.base / "crlf", crlf=True))
        self.assertEqual(updater.bundle_revision(lf), updater.bundle_revision(crlf))
        self.assertEqual(updater.bundle_revision(lf), revision)

    def test_extract_luau_modules_reads_any_depth_and_refuses_bad_packages(self):
        data = make_zip({"Main.luau": MODULES["Main"].replace("\n", "\r\n").encode(), "sub/Theme.luau": MODULES["Theme"].encode(),
                         "README.md": b"nu"}, top="studio-harness-app-0.8.0")
        self.assertEqual(updater.extract_luau_modules(data), MODULES)
        with self.assertRaisesRegex(UpdateError, "nu conține module"):
            updater.extract_luau_modules(make_zip({"README.md": b"nu"}))
        with self.assertRaisesRegex(UpdateError, "de două ori"):
            updater.extract_luau_modules(make_zip({"Main.luau": b"a", "x/Main.luau": b"b"}))
        with self.assertRaisesRegex(UpdateError, "nume invalid"):
            updater.extract_luau_modules(make_zip({"Bad Name.luau": b"a"}))
        with self.assertRaisesRegex(UpdateError, "cale invalidă"):
            updater.extract_luau_modules(make_zip({"../Main.luau": b"a"}, raw=True))
        with self.assertRaisesRegex(UpdateError, "zip valid"):
            updater.extract_luau_modules(b"nu este zip")

    def test_loader_and_app_version_headers(self):
        self.assertEqual(updater.loader_version(LOADER), "1.0.0")
        self.assertIsNone(updater.loader_version("-- Studio Harness Hub 0.7.0. UI nativ\n"))
        self.assertIsNone(updater.app_version(LOADER))
        self.assertEqual(updater.app_version("-- Studio Harness Hub 0.7.0. UI nativ\n"), "0.7.0")
        self.assertEqual(updater.app_version(MODULES["Main"]), "0.8.0")
        self.assertEqual(updater.app_version("local x = 1\n-- Studio Harness App 1.2.3.4 pe a doua linie\n"), "1.2.3.4")
        self.assertIsNone(updater.loader_version(""))

    def test_studio_app_dir_prefers_the_override_then_the_windows_plugins_folder(self):
        self.assertEqual(updater.studio_app_dir({"STUDIO_HARNESS_STUDIO_APP_DIR": str(self.base / "app")}), self.base / "app")
        with patch.object(updater.os, "name", "posix"):
            # Pe alt sistem decât Windows doar variabila de mediu dă un folder app (pathlib nu poate construi PosixPath aici, deci doar None).
            self.assertIsNone(updater.studio_app_dir({"LOCALAPPDATA": str(self.base)}))
        self.assertEqual(updater.studio_app_dir({"STUDIO_HARNESS_STUDIO_APP_DIR": str(self.base / "o"), "LOCALAPPDATA": "x"}), self.base / "o")
        if os.name == "nt":
            self.assertEqual(updater.studio_app_dir({"LOCALAPPDATA": str(self.base)}), self.base / "Roblox" / "Plugins" / "StudioHarness" / "app")
        self.assertIsNone(updater.studio_app_dir({}))


class StudioAppInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.base = Path(self.temp.name)
        self.app = self.base / "plugins" / "StudioHarness" / "app"
        self.environ = {"STUDIO_HARNESS_STUDIO_APP_DIR": str(self.app)}
        self.backups = self.base / "state" / "plugin-backups"

    def tearDown(self):
        self.temp.cleanup()

    def installed(self):
        return updater.read_luau_modules(self.app)

    def test_write_creates_the_folder_skips_identical_content_and_backs_up_the_previous_version(self):
        result = updater.write_studio_app(MODULES, self.environ, self.backups)
        self.assertEqual(result, {"path": str(self.app), "revision": updater.bundle_revision(MODULES), "modules": 2, "changed": True, "backup": None})
        self.assertEqual(self.installed(), MODULES)
        self.assertEqual((self.app / "Main.luau").read_bytes(), MODULES["Main"].encode("utf-8"))
        self.assertFalse(self.backups.exists())
        self.assertEqual(sorted(path.name for path in self.app.parent.iterdir()), ["app"])
        # Identic: nimic de scris, niciun backup.
        again = updater.write_studio_app(MODULES, self.environ, self.backups)
        self.assertEqual((again["changed"], again["backup"]), (False, None))
        self.assertFalse(self.backups.exists())
        # Conținut nou: folderul vechi ajunge întreg în plugin-backups/app-<timestamp>, cel nou nu are fișiere în plus.
        newer = {"Main": MODULES["Main"] + "-- v2\n", "Extra": "return 3\n"}
        changed = updater.write_studio_app(newer, self.environ, self.backups)
        self.assertTrue(changed["changed"])
        self.assertEqual(self.installed(), newer)
        self.assertFalse((self.app / "Theme.luau").exists())
        backup = Path(changed["backup"])
        self.assertEqual(backup.parent, self.backups)
        self.assertRegex(backup.name, r"^app-\d{8}-\d{6}$")
        self.assertEqual(updater.read_luau_modules(backup), MODULES)
        self.assertEqual(sorted(path.name for path in self.app.parent.iterdir()), ["app"])

    def test_install_from_a_source_directory_and_no_ops(self):
        source = write_modules(self.base / "repo" / "modules", crlf=True)
        result = updater.install_studio_app(source, self.environ, self.backups)
        self.assertEqual((result["changed"], result["modules"]), (True, 2))
        self.assertEqual(self.installed(), MODULES)
        self.assertIsNone(updater.install_studio_app(self.base / "lipsa", self.environ, self.backups))
        self.assertIsNone(updater.write_studio_app({}, self.environ, self.backups))
        with patch.object(updater.os, "name", "posix"):
            self.assertIsNone(updater.write_studio_app(MODULES, {"LOCALAPPDATA": str(self.base / "local")}, self.backups))
        self.assertFalse((self.base / "local").exists())

    def test_a_file_in_the_way_is_replaced_and_without_backup_root_the_old_folder_is_removed(self):
        self.app.parent.mkdir(parents=True)
        self.app.write_text("nu este folder", encoding="utf-8")
        self.assertTrue(updater.write_studio_app(MODULES, self.environ)["changed"])
        self.assertEqual(self.installed(), MODULES)
        self.assertTrue(updater.write_studio_app({"Main": "return 1\n"}, self.environ)["changed"])
        self.assertEqual(self.installed(), {"Main": "return 1\n"})
        self.assertFalse(self.backups.exists())

    def test_installed_loader_version_is_recorded_in_the_state_directory(self):
        state = self.base / "state"
        self.assertIsNone(updater.installed_loader_version(state))
        updater.record_loader_version(state, "1.0.0")
        self.assertEqual((state / "installed-loader.txt").read_text(encoding="utf-8"), "1.0.0\n")
        self.assertEqual(updater.installed_loader_version(state), "1.0.0")
        self.assertEqual(sorted(path.name for path in state.iterdir()), ["installed-loader.txt"])
        (state / "installed-loader.txt").write_text("stricat\n", encoding="utf-8")
        self.assertIsNone(updater.installed_loader_version(state))
        with self.assertRaises(UpdateError):
            updater.record_loader_version(state, "x")


class ManifestExtensionTests(unittest.TestCase):
    def entry(self):
        return {"url": "https://example.com/x.zip", "sha256": "a" * 64, "size": 10}

    def test_studio_app_kind_loader_version_and_bundle_revision_are_accepted(self):
        manifest = {"version": "0.8.0", "loader_version": "1.0.0", "bundle_revision": "0123456789abcdef",
                    "files": {"plugin": self.entry(), "hub": self.entry(), "studio_app": self.entry()}}
        self.assertIs(updater.validate_manifest(manifest), manifest)
        self.assertIn("studio_app", updater.BUNDLE_KINDS)
        # Ambele chei noi sunt opționale (manifestele 0.6/0.7 rămân valide).
        legacy = {"version": "0.8.0", "files": {"plugin": self.entry()}}
        self.assertIs(updater.validate_manifest(legacy), legacy)

    def test_invalid_loader_version_or_bundle_revision_is_refused(self):
        for extra, message in (({"loader_version": "x"}, "loader_version"), ({"loader_version": 1}, "loader_version"),
                               ({"bundle_revision": "zz"}, "bundle_revision"), ({"bundle_revision": 5}, "bundle_revision"),
                               ({"bundle_revision": "abc"}, "bundle_revision")):
            with self.subTest(extra=extra), self.assertRaisesRegex(UpdateError, message):
                updater.validate_manifest({"version": "0.8.0", "files": {"plugin": self.entry()}, **extra})


class PluginBundleTests(unittest.TestCase):
    """Daemon-ul servește modulele loader-ului: din folderul app dacă există, altfel din studio-plugin/modules; cache pe mtime la 2 s."""

    def setUp(self):
        self.temp = temp_dir()
        self.base = Path(self.temp.name)
        self.root = self.base / "plugin"
        write_modules(self.root / "studio-plugin" / "modules")
        self.app = self.base / "app"
        self.bridge = make_bridge(self.temp.name)
        self.bridge.plugin_root = self.root
        self.environ = patch.dict(os.environ, {"STUDIO_HARNESS_STUDIO_APP_DIR": str(self.app), "LOCALAPPDATA": str(self.base / "local")})
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.bridge.close()
        self.temp.cleanup()

    def test_bundle_comes_from_the_repo_modules_until_the_app_folder_exists(self):
        bundle = self.bridge.plugin_bundle()
        expected = updater.bundle_revision(MODULES)
        self.assertEqual(bundle, {"ok": True, "version": studio_bridge.VERSION, "revision": expected, "entry": "Main", "source": "repo", "modules": MODULES})
        self.assertEqual(self.bridge.status()["plugin_bundle"], {"version": studio_bridge.VERSION, "revision": expected, "source": "repo", "modules": 2})
        self.assertTrue(self.bridge.status()["features"]["plugin_bundle"])
        # Folderul app (editat de developer sau scris de updater) are prioritate; folderele goale nu contează.
        self.app.mkdir()
        self.bridge.bundle_cache.invalidate()
        self.assertEqual(self.bridge.plugin_bundle()["source"], "repo")
        write_modules(self.app, {"Main": "return 'app'\n"})
        self.bridge.bundle_cache.invalidate()
        bundle = self.bridge.plugin_bundle()
        self.assertEqual((bundle["source"], bundle["modules"], bundle["revision"]), ("app", {"Main": "return 'app'\n"}, updater.bundle_revision({"Main": "return 'app'\n"})))
        summary = self.bridge.status()["plugin_bundle"]
        self.assertEqual((summary["source"], summary["modules"]), ("app", 1))
        # Răspunsul este o copie: modificarea lui nu atinge cache-ul.
        bundle["modules"]["Main"] = "altceva"
        self.assertEqual(self.bridge.plugin_bundle()["modules"], {"Main": "return 'app'\n"})

    def test_missing_modules_are_404_for_the_bundle_but_status_still_reports_a_revision(self):
        shutil.rmtree(self.root / "studio-plugin")
        self.bridge.bundle_cache.invalidate()
        with self.assertRaises(studio_bridge.BridgeError) as error:
            self.bridge.plugin_bundle()
        self.assertEqual(error.exception.status, 404)
        summary = self.bridge.status()["plugin_bundle"]
        self.assertEqual((summary["modules"], summary["source"]), (0, "repo"))
        self.assertRegex(summary["revision"], r"^[0-9a-f]{16}$")

    def test_cache_rechecks_mtimes_after_two_seconds(self):
        clock = [100.0]
        cache = PluginBundleCache("0.8.0", recheck=2.0, clock=lambda: clock[0])
        first = cache.get(self.root)
        self.assertEqual(first["revision"], updater.bundle_revision(MODULES))
        main = self.root / "studio-plugin" / "modules" / "Main.luau"
        main.write_text("return 'editat'\n", encoding="utf-8")
        stamp = time.time() + 30
        os.utime(main, (stamp, stamp))
        # Sub 2 s: răspunsul din cache, fără stat pe disc.
        clock[0] = 101.5
        self.assertIs(cache.get(self.root), first)
        clock[0] = 102.1
        second = cache.get(self.root)
        self.assertEqual(second["modules"]["Main"], "return 'editat'\n")
        self.assertNotEqual(second["revision"], first["revision"])
        # Fără schimbări pe disc, o reverificare păstrează același obiect.
        clock[0] = 110
        self.assertIs(cache.get(self.root), second)
        cache.invalidate()
        self.assertEqual(cache.get(self.root)["revision"], second["revision"])

    def test_bundle_route_over_http_needs_the_token_and_marks_the_plugin_connected(self):
        server = BridgeServer(0, self.bridge)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            base = self.bridge.base_url

            def get(path, token=UI_TOKEN):
                headers = {"X-Studio-Harness-Token": token} if token else {}
                try:
                    with urlopen(Request(base + path, headers=headers), timeout=3) as response:
                        return response.status, json.loads(response.read())
                except HTTPError as error:
                    return error.code, json.loads(error.read())

            self.assertEqual(get("/v1/plugin/bundle", token=None)[0], 401)
            self.assertFalse(get("/v1/status")[1]["plugin_connected"])
            status, body = get("/v1/plugin/bundle")
            self.assertEqual(status, 200)
            self.assertEqual((body["ok"], body["entry"], body["source"], sorted(body["modules"])), (True, "Main", "repo", ["Main", "Theme"]))
            self.assertEqual(body["revision"], get("/v1/status")[1]["plugin_bundle"]["revision"])
            self.assertTrue(get("/v1/status")[1]["plugin_connected"])
            self.assertNotIn(UI_TOKEN, json.dumps(body))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


class UpdateWithStudioAppTests(unittest.TestCase):
    """Bridge.update_apply (0.8): modulele ajung în folderul app, .rbxmx doar când loader_version diferă, mesaje explicite."""

    def setUp(self):
        self.temp = temp_dir()
        self.base = Path(self.temp.name)
        self.bridge = make_bridge(self.temp.name)
        self.root = self.base / "plugin"
        (self.root / ".claude-plugin").mkdir(parents=True)
        (self.root / ".claude-plugin" / "plugin.json").write_text('{"version": "0.8.0"}', encoding="utf-8")
        (self.root / "README.md").write_bytes(b"vechi\n")
        write_modules(self.root / "studio-plugin" / "modules", {"Main": "return 'vechi'\n"})
        self.bridge.plugin_root = self.root
        self.channel = LocalChannel().__enter__()
        self.local = self.base / "local"
        self.app = self.base / "app"
        self.state = self.base / "state"
        self.environ = patch.dict(os.environ, {"STUDIO_HARNESS_UPDATE_URL": self.channel.url("/manifest.json"), "STUDIO_HARNESS_AUTO_UPDATE": "1",
                                               "LOCALAPPDATA": str(self.local), "STUDIO_HARNESS_STUDIO_APP_DIR": str(self.app)})
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.channel.__exit__(None, None, None)
        self.bridge.close()
        self.temp.cleanup()

    @staticmethod
    def packaged(tag):
        """Un .rbxmx de pachet: conține placeholder-ul LocalToken, pe care instalarea îl înlocuiește cu tokenul UI al daemon-ului."""
        return b"<roblox>" + tag + b" " + updater.LOCAL_TOKEN_PLACEHOLDER.encode("utf-8") + b"</roblox>"

    @staticmethod
    def installed(tag):
        # make_bridge dă daemon-ului tokenul UI din test_studio_bridge; fișierul din Roblox\Plugins îl conține în locul placeholder-ului.
        return b"<roblox>" + tag + b" " + UI_TOKEN.encode("utf-8") + b"</roblox>"

    def publish(self, version, loader="1.0.0", app_modules=MODULES, rbxmx=None, with_app=True):
        rbxmx = self.packaged(b"v1") if rbxmx is None else rbxmx
        plugin_files = {"README.md": ("nou " + version + "\n").encode(), "dist/StudioHarness.rbxmx": rbxmx,
                        "studio-plugin/modules/Main.luau": b"return 'din pachetul plugin'\n"}
        files = {"plugin": self.channel.entry("/roblox-studio-harness-" + version + ".zip", make_zip(plugin_files, top="roblox-studio-harness-" + version))}
        if with_app:
            files["studio_app"] = self.channel.entry("/studio-harness-app-" + version + ".zip",
                                                     make_zip({name + ".luau": source.encode() for name, source in app_modules.items()}, top="studio-harness-app-" + version))
        manifest = self.channel.manifest(version, **files)
        manifest["loader_version"] = loader
        manifest["bundle_revision"] = updater.bundle_revision(app_modules)
        self.channel.serve("/manifest.json", json.dumps(manifest).encode("utf-8"))
        return manifest

    def rbxmx(self):
        return self.local / "Roblox" / "Plugins" / "StudioHarness.rbxmx"

    def test_modules_come_from_the_studio_app_bundle_and_the_loader_is_rewritten_only_when_its_version_changes(self):
        manifest = self.publish("0.9.0")
        self.assertTrue(self.bridge.update_apply(manifest))
        status = self.bridge.status()["update"]
        self.assertEqual((status["state"], status["restart_required"]), ("installed", True))
        self.assertEqual(self.channel.requests, ["/roblox-studio-harness-0.9.0.zip", "/studio-harness-app-0.9.0.zip"])
        self.assertEqual(updater.read_luau_modules(self.app), MODULES)
        self.assertEqual((self.root / "README.md").read_bytes(), b"nou 0.9.0\n")
        bundle = self.bridge.status()["plugin_bundle"]
        self.assertEqual((bundle["source"], bundle["revision"], bundle["modules"]), ("app", manifest["bundle_revision"], 2))
        if os.name == "nt":
            # Prima actualizare 0.8: niciun loader înregistrat, deci .rbxmx se instalează și Studio trebuie repornit o dată.
            self.assertEqual(self.rbxmx().read_bytes(), self.installed(b"v1"))
            self.assertEqual((self.root / "dist" / "StudioHarness.rbxmx").read_bytes(), self.packaged(b"v1"))
            self.assertEqual(updater.installed_loader_version(self.state), "1.0.0")
            self.assertIn("repornește Studio pentru noul loader", status["message"])
        else:
            self.assertFalse(self.rbxmx().exists())
            self.assertIsNone(updater.installed_loader_version(self.state))
            self.assertIn("interfața din Studio s-a actualizat singură", status["message"])
        # Același loader, module noi: doar folderul app se schimbă (cu backup), fără .rbxmx nou.
        newer = {"Main": "return 'v2'\n", "Theme": MODULES["Theme"]}
        self.assertTrue(self.bridge.update_apply(self.publish("0.9.1", app_modules=newer, rbxmx=self.packaged(b"v2"))))
        status = self.bridge.status()["update"]
        self.assertIn("Actualizare 0.9.1 instalată; interfața din Studio s-a actualizat singură.", status["message"])
        self.assertEqual(updater.read_luau_modules(self.app), newer)
        backups = sorted(path.name for path in (self.state / "plugin-backups").iterdir())
        self.assertEqual(len([name for name in backups if name.startswith("app-")]), 1, backups)
        self.assertEqual(self.bridge.status()["plugin_bundle"]["revision"], updater.bundle_revision(newer))
        if os.name == "nt":
            self.assertEqual(self.rbxmx().read_bytes(), self.installed(b"v1"))
            self.assertEqual(updater.installed_loader_version(self.state), "1.0.0")
            # Loader nou: .rbxmx rescris, versiunea înregistrată, mesajul cere repornirea Studio-ului.
            self.assertTrue(self.bridge.update_apply(self.publish("0.9.2", loader="1.1.0", rbxmx=self.packaged(b"v3"))))
            self.assertEqual(self.rbxmx().read_bytes(), self.installed(b"v3"))
            self.assertEqual(updater.installed_loader_version(self.state), "1.1.0")
            self.assertIn("Actualizare 0.9.2 instalată; repornește Studio pentru noul loader.", self.bridge.status()["update"]["message"])

    def test_without_a_studio_app_bundle_the_modules_from_the_plugin_bundle_are_installed(self):
        self.assertTrue(self.bridge.update_apply(self.publish("0.9.0", with_app=False)))
        self.assertEqual(self.channel.requests, ["/roblox-studio-harness-0.9.0.zip"])
        self.assertEqual(updater.read_luau_modules(self.app), {"Main": "return 'din pachetul plugin'\n"})
        self.assertEqual(self.bridge.status()["plugin_bundle"]["source"], "app")

    def test_a_bad_studio_app_bundle_stops_the_update_before_anything_is_written(self):
        manifest = self.publish("0.9.0")
        manifest["files"]["studio_app"]["sha256"] = "0" * 64
        self.assertFalse(self.bridge.update_apply(manifest))
        status = self.bridge.status()["update"]
        self.assertEqual(status["state"], "error")
        self.assertIn("SHA256", status["message"])
        self.assertEqual((self.root / "README.md").read_bytes(), b"vechi\n")
        self.assertFalse(self.app.exists())
        self.assertFalse(self.rbxmx().exists())
        self.assertIsNone(updater.installed_loader_version(self.state))
        # Un manifest fără loader_version folosește versiunea pachetului; un loader înregistrat identic nu rescrie .rbxmx.
        updater.record_loader_version(self.state, "0.9.0")
        manifest = self.publish("0.9.0")
        del manifest["loader_version"]
        self.channel.serve("/manifest.json", json.dumps(manifest).encode("utf-8"))
        self.assertTrue(self.bridge.update_apply(manifest))
        self.assertFalse(self.rbxmx().exists())
        self.assertIn("interfața din Studio s-a actualizat singură", self.bridge.status()["update"]["message"])
        self.assertEqual(updater.installed_loader_version(self.state), "0.9.0")


class BuildVerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = temp_dir()
        self.base = Path(self.temp.name)
        self.source = self.base / "StudioHarness.server.luau"
        self.source.write_text(LOADER, encoding="utf-8")
        write_modules(self.base / "modules")

    def tearDown(self):
        self.temp.cleanup()

    def test_build_with_entry_verifies_the_loader_header_and_the_main_module(self):
        destination = build_studio_plugin.build(self.source, self.base / "dist" / "StudioHarness.rbxmx", entry="Main", require_version=True)
        summary = build_studio_plugin.verify(destination)
        self.assertEqual(summary, {"loader_version": "1.0.0", "modules": ["Main", "Theme"], "entry": "Main", "local_token": "placeholder"})
        self.assertEqual(sorted(path.name for path in destination.parent.iterdir()), ["StudioHarness.rbxmx"])
        root = ET.parse(destination).getroot()
        self.assertEqual(root.find("Item[@class='Model']/Item[@class='Script']/Properties/ProtectedString[@name='Source']").text, LOADER)
        (self.base / "modules" / "Main.luau").unlink()
        with self.assertRaisesRegex(ValueError, "Main.luau lipsește"):
            build_studio_plugin.build(self.source, self.base / "dist2" / "StudioHarness.rbxmx", entry="Main", require_version=True)
        self.assertFalse((self.base / "dist2").exists())
        # Fără cerințe (testele de împachetare vechi) modulul de intrare nu este obligatoriu.
        build_studio_plugin.build(self.source, self.base / "dist3" / "StudioHarness.rbxmx")

    def test_loader_without_a_versioned_header_is_refused_when_required(self):
        self.source.write_text("-- Studio Harness fără versiune\nlocal p = plugin\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Antetul loader-ului"):
            build_studio_plugin.build(self.source, self.base / "dist" / "x.rbxmx", entry="Main", require_version=True)
        self.assertFalse((self.base / "dist").exists())
        destination = build_studio_plugin.build(self.source, self.base / "dist" / "x.rbxmx", entry="Main")
        self.assertIsNone(build_studio_plugin.verify(destination, "Main", require_version=False)["loader_version"])
        with self.assertRaisesRegex(ValueError, "versiune"):
            build_studio_plugin.verify(destination, "Main", require_version=True)
        self.assertEqual(build_studio_plugin.header_version("-- Studio Harness Hub 0.7.0. x"), "0.7.0")
        self.assertEqual(build_studio_plugin.header_version("-- Studio Harness App 0.8.0"), "0.8.0")

    def test_verify_rejects_packages_without_modules_or_the_entry(self):
        shutil.rmtree(self.base / "modules")
        destination = build_studio_plugin.build(self.source, self.base / "dist" / "x.rbxmx")
        with self.assertRaisesRegex(ValueError, "folderul Modules"):
            build_studio_plugin.verify(destination, "Main")
        write_modules(self.base / "modules", {"Theme": "return {}\n"})
        destination = build_studio_plugin.build(self.source, self.base / "dist" / "y.rbxmx")
        with self.assertRaisesRegex(ValueError, "modulul de intrare Main"):
            build_studio_plugin.verify(destination, "Main")
        self.assertEqual(build_studio_plugin.verify(destination, None)["modules"], ["Theme"])
        (self.base / "gol.rbxmx").write_text('<roblox version="4"></roblox>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "modelul"):
            build_studio_plugin.verify(self.base / "gol.rbxmx", None)

    def test_real_plugin_sources_build_a_verified_loader_with_main(self):
        source = ROOT / "studio-plugin" / "StudioHarness.server.luau"
        if not source.is_file() or not (ROOT / "studio-plugin" / "modules" / "Main.luau").is_file():
            self.skipTest("sursele loader-ului (faza 5) nu sunt încă în repo")
        destination = build_studio_plugin.build(source, self.base / "real" / "StudioHarness.rbxmx", entry="Main", require_version=True)
        summary = build_studio_plugin.verify(destination)
        self.assertIn("Main", summary["modules"])
        self.assertEqual(summary["loader_version"], publish_release.loader_version(ROOT) or updater.current_version(ROOT))
        self.assertEqual(summary["local_token"], "placeholder")

    def test_local_token_placeholder_is_a_string_value_under_the_loader_and_verify_requires_it(self):
        # 1.0: loader-ul citește script:FindFirstChild("LocalToken"); dist/ conține doar placeholder-ul, instalarea îl înlocuiește.
        destination = build_studio_plugin.build(self.source, self.base / "dist" / "StudioHarness.rbxmx", entry="Main", require_version=True)
        root = ET.parse(destination).getroot()
        loader = root.find("Item[@class='Model']/Item[@class='Script']")
        values = loader.findall("Item[@class='StringValue']")
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].findtext("Properties/string[@name='Name']"), "LocalToken")
        self.assertEqual(values[0].findtext("Properties/string[@name='Value']"), updater.LOCAL_TOKEN_PLACEHOLDER)
        self.assertEqual(build_studio_plugin.local_token_value(loader), updater.LOCAL_TOKEN_PLACEHOLDER)
        # Referenții rămân unici și placeholder-ul nu apare în sursele Luau (doar în StringValue).
        referents = [node.attrib["referent"] for node in root.iter("Item")]
        self.assertEqual(len(referents), len(set(referents)))
        self.assertNotIn(updater.LOCAL_TOKEN_PLACEHOLDER, "".join(node.text or "" for node in root.iter("ProtectedString")))
        # Fișierul instalat (token injectat) trece verificarea și este raportat ca `injected`; XML-ul rămâne valid.
        installed = self.base / "installed.rbxmx"
        installed.write_bytes(updater.inject_local_token(destination.read_bytes(), "cod-local-de-test_0123456789"))
        self.assertEqual(build_studio_plugin.verify(installed)["local_token"], "injected")
        self.assertEqual(build_studio_plugin.local_token_value(ET.parse(installed).getroot().find("Item[@class='Model']/Item[@class='Script']")),
                         "cod-local-de-test_0123456789")
        # Un pachet 0.8 (fără StringValue) sau cu valoarea goală este refuzat de verify.
        for value in values:
            loader.remove(value)
        old = self.base / "old.rbxmx"
        old.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        with self.assertRaisesRegex(ValueError, "LocalToken"):
            build_studio_plugin.verify(old)
        self.assertIsNone(build_studio_plugin.local_token_value(ET.parse(old).getroot().find("Item[@class='Model']/Item[@class='Script']")))
        empty = self.base / "empty.rbxmx"
        empty.write_bytes(destination.read_bytes().replace(updater.LOCAL_TOKEN_PLACEHOLDER.encode("utf-8"), b""))
        with self.assertRaisesRegex(ValueError, "LocalToken"):
            build_studio_plugin.verify(empty)


class InstallerScriptTests(unittest.TestCase):
    """scripts/install-studio-plugin.ps1: .rbxmx cu `local-token` injectat (1.0) + folderul app cu modulele, backup-uri în
    <stare>\\plugin-backups (STUDIO_HARNESS_STATE_DIR sau %LOCALAPPDATA%\\StudioHarness)."""

    SCRIPT = ROOT / "scripts" / "install-studio-plugin.ps1"
    PACKAGED = ('<roblox version="4"><Item class="Script"><Item class="StringValue"><Properties><string name="Name">LocalToken</string>'
                '<string name="Value">' + updater.LOCAL_TOKEN_PLACEHOLDER + '</string></Properties></Item></Item></roblox>\n').encode("utf-8")

    def powershell(self):
        return shutil.which("powershell") or shutil.which("pwsh")

    def test_installer_text_covers_the_app_folder_the_token_and_stays_utf8_bom(self):
        raw = self.SCRIPT.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "install-studio-plugin.ps1 trebuie salvat ca UTF-8 cu BOM")
        self.assertNotIn(b"\r", raw, "install-studio-plugin.ps1 trebuie salvat cu linii LF")
        script = raw.decode("utf-8-sig")
        for needle in ("Join-Path $root 'studio-plugin\\modules'", "Join-Path $pluginDirectory 'StudioHarness\\app'", "'plugin-backups'",
                       "('app-' + $stamp)", "Main.luau", "-Filter '*.luau'", "AppModules", "AppDirectory", "AppBackup",
                       "$env:STUDIO_HARNESS_STATE_DIR", "Join-Path $env:LOCALAPPDATA 'StudioHarness'", "Join-Path $stateDirectory 'local-token'",
                       "'" + updater.LOCAL_TOKEN_PLACEHOLDER + "'", "'^[A-Za-z0-9_\\-]{16,512}$'", "RandomNumberGenerator", "TrimEnd('=').Replace('+', '-').Replace('/', '_')",
                       "$packaged.Replace($tokenPlaceholder, $token)", "[IO.File]::WriteAllBytes($target, $installedBytes)",
                       "LocalTokenFile", "LocalTokenCreated", "LocalTokenInjected"):
            self.assertIn(needle, script)
        # Pachetul din dist/ nu se copiază ca atare (ar rămâne cu placeholder) și tokenul nu ajunge în rezumat/consolă.
        self.assertNotIn("Copy-Item -LiteralPath $source", script)
        self.assertNotIn("Write-Host $token", script)
        self.assertNotIn("LocalToken = $token", script)
        launcher = (ROOT / "Install-Studio-Plugin.cmd").read_text(encoding="utf-8")
        self.assertIn("scripts\\build_studio_plugin.py", launcher)
        self.assertIn("scripts\\install-studio-plugin.ps1", launcher)
        self.assertTrue(launcher.isascii())

    def test_installer_is_valid_powershell(self):
        powershell = self.powershell()
        if not powershell:
            self.skipTest("powershell indisponibil")
        command = "[scriptblock]::Create((Get-Content -Raw -LiteralPath '" + str(self.SCRIPT) + "')) | Out-Null"
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.strip(), "")

    def run_installer(self, powershell, root, env):
        return subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts" / "install-studio-plugin.ps1")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=120, env=env)

    def make_repo(self, base, packaged=None):
        root = base / "repo"
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(self.SCRIPT, root / "scripts" / "install-studio-plugin.ps1")
        (root / "dist").mkdir()
        (root / "dist" / "StudioHarness.rbxmx").write_bytes(self.PACKAGED if packaged is None else packaged)
        write_modules(root / "studio-plugin" / "modules")
        return root

    def test_installer_injects_the_local_token_and_copies_the_modules_into_a_temporary_localappdata(self):
        powershell = self.powershell()
        if not powershell or os.name != "nt":
            self.skipTest("installer-ul PowerShell rulează doar pe Windows")
        with temp_dir() as temp:
            base = Path(temp)
            root = self.make_repo(base)
            local = base / "local"
            (local / "Roblox").mkdir(parents=True)
            env = dict(os.environ, LOCALAPPDATA=str(local))
            env.pop("STUDIO_HARNESS_STATE_DIR", None)

            def run():
                result = self.run_installer(powershell, root, env)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout), result.stdout

            first, stdout = run()
            plugins = local / "Roblox" / "Plugins"
            token_file = local / "StudioHarness" / "local-token"
            self.assertEqual((first["Installed"], first["HashVerified"], first["Backup"], first["AppBackup"], first["AppModules"]), (True, True, None, None, 2))
            self.assertEqual((first["LocalTokenCreated"], first["LocalTokenInjected"], Path(first["LocalTokenFile"])), (True, True, token_file))
            # Tokenul este creat în directorul de stare, în formatul lui local_state (43 de caractere URL-safe), fără BOM și fără linie nouă.
            token = token_file.read_bytes().decode("utf-8")
            self.assertRegex(token, r"^[A-Za-z0-9_\-]{43}$")
            self.assertEqual(updater.inject_local_token(self.PACKAGED, token), (plugins / "StudioHarness.rbxmx").read_bytes())
            self.assertNotIn(updater.LOCAL_TOKEN_PLACEHOLDER.encode("utf-8"), (plugins / "StudioHarness.rbxmx").read_bytes())
            self.assertNotIn(token, stdout)
            self.assertEqual((root / "dist" / "StudioHarness.rbxmx").read_bytes(), self.PACKAGED)
            self.assertEqual(updater.read_luau_modules(plugins / "StudioHarness" / "app"), MODULES)
            self.assertEqual(Path(first["AppDirectory"]), plugins / "StudioHarness" / "app")
            # A doua instalare: același token (nu se regenerează); .rbxmx și folderul app anterior ajung în plugin-backups.
            (root / "studio-plugin" / "modules" / "Theme.luau").unlink()
            (root / "studio-plugin" / "modules" / "Main.luau").write_text("return 'v2'\n", encoding="utf-8")
            second, _ = run()
            self.assertEqual((second["AppModules"], second["LocalTokenCreated"], second["LocalTokenInjected"]), (1, False, True))
            self.assertEqual(token_file.read_bytes().decode("utf-8"), token)
            self.assertEqual(updater.inject_local_token(self.PACKAGED, token), (plugins / "StudioHarness.rbxmx").read_bytes())
            self.assertEqual(updater.read_luau_modules(plugins / "StudioHarness" / "app"), {"Main": "return 'v2'\n"})
            backups = local / "StudioHarness" / "plugin-backups"
            self.assertEqual(Path(second["Backup"]).parent, backups)
            self.assertEqual(Path(second["AppBackup"]).parent, backups)
            self.assertRegex(Path(second["AppBackup"]).name, r"^app-\d{8}-\d{6}-\d{4}$")
            self.assertEqual(updater.read_luau_modules(Path(second["AppBackup"])), MODULES)
            # Fără Main.luau installer-ul refuză (alte module există, deci mesajul numește intrarea lipsă).
            (root / "studio-plugin" / "modules" / "Main.luau").unlink()
            (root / "studio-plugin" / "modules" / "Theme.luau").write_text("return {}\n", encoding="utf-8")
            result = self.run_installer(powershell, root, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Main.luau", result.stderr)

    def test_installer_reuses_a_valid_token_from_the_state_directory_and_replaces_an_invalid_one(self):
        powershell = self.powershell()
        if not powershell or os.name != "nt":
            self.skipTest("installer-ul PowerShell rulează doar pe Windows")
        with temp_dir() as temp:
            base = Path(temp)
            root = self.make_repo(base)
            local = base / "local"
            (local / "Roblox").mkdir(parents=True)
            state = base / "stare"
            state.mkdir()
            (state / "local-token").write_text("cod-local-existent_0123456789\n", encoding="utf-8")
            env = dict(os.environ, LOCALAPPDATA=str(local), STUDIO_HARNESS_STATE_DIR=str(state))
            result = self.run_installer(powershell, root, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual((summary["LocalTokenCreated"], summary["LocalTokenInjected"], Path(summary["LocalTokenFile"])), (False, True, state / "local-token"))
            installed = local / "Roblox" / "Plugins" / "StudioHarness.rbxmx"
            self.assertEqual(installed.read_bytes(), updater.inject_local_token(self.PACKAGED, "cod-local-existent_0123456789"))
            self.assertEqual((state / "local-token").read_text(encoding="utf-8"), "cod-local-existent_0123456789\n")
            self.assertNotIn("cod-local-existent", result.stdout)
            self.assertFalse((local / "StudioHarness").exists())
            # Un fișier invalid (prea scurt) este înlocuit cu un token nou, iar backup-ul urmează directorul de stare.
            (state / "local-token").write_text("scurt\n", encoding="utf-8")
            result = self.run_installer(powershell, root, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            fresh = (state / "local-token").read_bytes().decode("utf-8")
            self.assertRegex(fresh, r"^[A-Za-z0-9_\-]{43}$")
            self.assertEqual((summary["LocalTokenCreated"], summary["LocalTokenInjected"]), (True, True))
            self.assertEqual(installed.read_bytes(), updater.inject_local_token(self.PACKAGED, fresh))
            self.assertEqual(Path(summary["Backup"]).parent, state / "plugin-backups")
            self.assertEqual(Path(summary["Backup"]).read_bytes(), updater.inject_local_token(self.PACKAGED, "cod-local-existent_0123456789"))

    def test_installer_keeps_a_package_without_placeholder_byte_for_byte(self):
        powershell = self.powershell()
        if not powershell or os.name != "nt":
            self.skipTest("installer-ul PowerShell rulează doar pe Windows")
        with temp_dir() as temp:
            base = Path(temp)
            root = self.make_repo(base, packaged=b"<roblox>loader vechi</roblox>\r\n")
            local = base / "local"
            (local / "Roblox").mkdir(parents=True)
            env = dict(os.environ, LOCALAPPDATA=str(local))
            env.pop("STUDIO_HARNESS_STATE_DIR", None)
            result = self.run_installer(powershell, root, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual((summary["HashVerified"], summary["LocalTokenInjected"]), (True, False))
            self.assertIn("cod", summary["NextStep"])
            self.assertEqual((local / "Roblox" / "Plugins" / "StudioHarness.rbxmx").read_bytes(), b"<roblox>loader vechi</roblox>\r\n")
            # Tokenul este creat oricum (daemon-ul și hook-urile îl folosesc), dar nu ajunge în fișier.
            self.assertRegex((local / "StudioHarness" / "local-token").read_bytes().decode("utf-8"), r"^[A-Za-z0-9_\-]{43}$")


if __name__ == "__main__":
    unittest.main()
