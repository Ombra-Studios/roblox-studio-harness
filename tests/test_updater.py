"""Auto-update (0.6): versiuni, canal, manifest verificat, descărcare cu SHA256, aplicare cu backup, pluginul Studio.

Fără rețea în afara loopback-ului: canalul este un http.server pornit de test pe port efemer (permis de `_allowed_url`).
Nimic nu scrie în %LOCALAPPDATA% real sau în repo: fiecare test are propriul director temporar."""

import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import updater
from updater import UpdateError

PLACEHOLDER_URL = "https://huggingface.co/spaces/OWNER/studio-harness/resolve/main/releases/manifest.json"
HTTPS_URL = "https://huggingface.co/spaces/ana/studio-harness/resolve/main/releases/manifest.json"
PUBLIC_URL = "https://lostcube.pro/releases/manifest.json"


class LocalChannel:
    """Canal de actualizare pe loopback, port efemer: servește rute fixe (manifest, zip) și înregistrează cererile."""

    def __init__(self):
        outer = self
        self.routes = {}
        self.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                status, data = outer.routes.get(self.path, (404, b'{"ok": false}'))
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def url(self, path):
        return self.base + path

    def serve(self, path, data, status=200):
        self.routes[path] = (status, data)
        return self.url(path)

    def entry(self, path, data):
        """Intrare de manifest pentru `data` servită la `path`, cu suma și mărimea reale."""
        return {"url": self.serve(path, data), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def manifest(self, version, path="/manifest.json", **files):
        """Publică un manifest cu pachetele date și întoarce dict-ul exact așa cum îl va citi updater-ul."""
        manifest = {"version": version, "published": "2026-09-14T00:00:00", "notes": "Studio Harness " + version, "files": files}
        self.serve(path, json.dumps(manifest).encode("utf-8"))
        return manifest

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def make_zip(files, top="pachet-1.0.0", raw=False):
    """Zip în memorie cu folder de top, ca pachetele de release; cu `raw` numele sunt scrise exact (pentru căi invalide)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        if not raw:
            bundle.writestr(top + "/", b"")
        for name, data in files.items():
            bundle.writestr(name if raw else top + "/" + name, data)
    return buffer.getvalue()


def refused(*_, **__):
    raise URLError(ConnectionRefusedError("nimic nu ascultă"))


class VersionTests(unittest.TestCase):
    def test_parse_version_pads_to_four_numbers(self):
        self.assertEqual(updater.parse_version("0.6.0"), (0, 6, 0, 0))
        self.assertEqual(updater.parse_version("1.2"), (1, 2, 0, 0))
        self.assertEqual(updater.parse_version("1.2.3.4"), (1, 2, 3, 4))
        self.assertEqual(updater.parse_version(" 0.6.1 \n"), (0, 6, 1, 0))
        self.assertEqual(updater.parse_version("0.6"), updater.parse_version("0.6.0.0"))

    def test_parse_version_rejects_anything_but_two_to_four_numbers(self):
        for value in (None, 6, 0.6, "", "1", "1.2.3.4.5", "a.b", "1.-2", "1..2", "0.6.0-beta", "v0.6.0", ["0", "6"], {"v": 1}):
            with self.subTest(value=value), self.assertRaisesRegex(UpdateError, "Versiune invalidă"):
                updater.parse_version(value)

    def test_versions_compare_numerically_not_lexically(self):
        self.assertGreater(updater.parse_version("0.10.0"), updater.parse_version("0.9.9"))
        self.assertGreater(updater.parse_version("1.0.0"), updater.parse_version("0.99.99"))
        self.assertGreater(updater.parse_version("0.6.0.1"), updater.parse_version("0.6.0"))
        self.assertLess(updater.parse_version("0.5.3"), updater.parse_version("0.6"))
        self.assertEqual(updater.parse_version("0.6.0"), updater.parse_version("0.6"))


class CurrentVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, content):
        (self.root / ".claude-plugin").mkdir(exist_ok=True)
        (self.root / ".claude-plugin" / "plugin.json").write_text(content, encoding="utf-8")

    def test_missing_or_invalid_plugin_json_means_0_0_0(self):
        self.assertEqual(updater.current_version(self.root), "0.0.0")
        for content in ("{", "{}", '{"version": "x"}', '{"version": 6}', '{"version": "1"}', '{"version": null}'):
            with self.subTest(content=content):
                self.write(content)
                self.assertEqual(updater.current_version(self.root), "0.0.0")

    def test_valid_plugin_json_gives_its_version(self):
        self.write('{"name": "roblox-studio-harness", "version": "0.6.0"}')
        self.assertEqual(updater.current_version(self.root), "0.6.0")

    def test_repo_versions_are_aligned(self):
        # publish_release refuză publicarea dacă daemon-ul, hub-ul, plugin.json și antetul `App` din Main.luau nu au aceeași versiune.
        # Versiunile se citesc din text (ca în publish_release.verify_versions), nu prin import: testul nu depinde de restul daemon-ului.
        version = updater.current_version(ROOT)
        self.assertNotEqual(version, "0.0.0")
        for script in ("scripts/studio_bridge.py", "scripts/team_hub.py"):
            with self.subTest(script=script):
                match = re.search(r'^VERSION = "([^"]+)"', (ROOT / script).read_text(encoding="utf-8"), re.MULTILINE)
                self.assertIsNotNone(match, script)
                self.assertEqual(match.group(1), version)
        self.assertEqual(updater.app_version((ROOT / "studio-plugin" / "modules" / "Main.luau").read_text(encoding="utf-8-sig")), version)
        loader = updater.loader_version((ROOT / "studio-plugin" / "StudioHarness.server.luau").read_text(encoding="utf-8-sig"))
        self.assertIsNotNone(loader)
        updater.parse_version(loader)
        shipped = json.loads((ROOT / "update-channel.json").read_text(encoding="utf-8"))
        self.assertIsInstance(shipped.get("manifest_url"), str)
        self.assertIsInstance(shipped.get("auto"), bool)
        # Developerii se actualizează de la hub-ul găzduit (lostcube.pro); upstream-ul (GitHub raw sau Hugging Face) este HTTPS.
        self.assertEqual(shipped["manifest_url"], PUBLIC_URL)
        self.assertTrue(shipped["upstream_manifest_url"].startswith("https://"), shipped)
        self.assertEqual(updater.channel(ROOT, {})["manifest_url"], PUBLIC_URL)


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, settings):
        content = settings if isinstance(settings, str) else json.dumps(settings)
        (self.root / "update-channel.json").write_text(content, encoding="utf-8")

    def test_placeholder_owner_means_unconfigured(self):
        self.write({"manifest_url": PLACEHOLDER_URL, "auto": True})
        self.assertEqual(updater.channel(self.root, {}), {"manifest_url": None, "upstream_manifest_url": None, "auto": True})

    def test_missing_or_broken_file_falls_back_to_defaults(self):
        self.assertEqual(updater.channel(self.root, {}), {"manifest_url": None, "upstream_manifest_url": None, "auto": True})
        for content in ("{", "[1]", "null", '"text"'):
            with self.subTest(content=content):
                self.write(content)
                self.assertEqual(updater.channel(self.root, {}), {"manifest_url": None, "upstream_manifest_url": None, "auto": True})
        # Doar `true` literal înseamnă auto; URL-ul care nu este text este ignorat.
        self.write({"manifest_url": 5, "auto": "da"})
        self.assertEqual(updater.channel(self.root, {}), {"manifest_url": None, "upstream_manifest_url": None, "auto": False})

    def test_configured_https_channel_is_accepted(self):
        self.write({"manifest_url": HTTPS_URL, "auto": False})
        self.assertEqual(updater.channel(self.root, {}), {"manifest_url": HTTPS_URL, "upstream_manifest_url": None, "auto": False})
        self.write({"manifest_url": HTTPS_URL})
        self.assertEqual(updater.channel(self.root, {}), {"manifest_url": HTTPS_URL, "upstream_manifest_url": None, "auto": True})

    def test_environment_override_wins_and_only_https_or_loopback_are_allowed(self):
        self.write({"manifest_url": PLACEHOLDER_URL, "auto": True})
        for url in ("https://example.com/m.json", "http://127.0.0.1:34999/m.json", "http://localhost:34999/m.json"):
            with self.subTest(url=url):
                self.assertEqual(updater.channel(self.root, {"STUDIO_HARNESS_UPDATE_URL": url})["manifest_url"], url)
        for url in ("http://example.com/m.json", "http://192.168.1.10/m.json", "http://127.0.0.1.evil.com/m.json",
                    "ftp://example.com/m.json", "file:///tmp/m.json", "https://huggingface.co/OWNER/m.json"):
            with self.subTest(url=url):
                self.assertIsNone(updater.channel(self.root, {"STUDIO_HARNESS_UPDATE_URL": url})["manifest_url"])
        # Variabila goală nu suprascrie fișierul.
        self.write({"manifest_url": HTTPS_URL, "auto": True})
        self.assertEqual(updater.channel(self.root, {"STUDIO_HARNESS_UPDATE_URL": ""})["manifest_url"], HTTPS_URL)

    def test_auto_update_can_be_disabled_from_the_environment(self):
        self.write({"manifest_url": HTTPS_URL, "auto": True})
        for value in ("0", "false", "no", "off", "OFF", "False"):
            with self.subTest(value=value):
                self.assertEqual(updater.channel(self.root, {"STUDIO_HARNESS_AUTO_UPDATE": value}), {"manifest_url": HTTPS_URL, "upstream_manifest_url": None, "auto": False})
        for value in ("1", "true", "", "yes"):
            with self.subTest(value=value):
                self.assertTrue(updater.channel(self.root, {"STUDIO_HARNESS_AUTO_UPDATE": value})["auto"])
        # Variabila nu poate reactiva ce fișierul a dezactivat.
        self.write({"manifest_url": HTTPS_URL, "auto": False})
        self.assertFalse(updater.channel(self.root, {"STUDIO_HARNESS_AUTO_UPDATE": "1"})["auto"])

    def test_upstream_manifest_url_is_validated_like_the_public_one_and_ignores_the_override(self):
        # 0.8: hub-ul găzduit citește de la upstream (Hugging Face) și oglindește spre canalul public (lostcube.pro).
        self.write({"manifest_url": PUBLIC_URL, "upstream_manifest_url": HTTPS_URL, "auto": True})
        self.assertEqual(updater.channel(self.root, {}), {"manifest_url": PUBLIC_URL, "upstream_manifest_url": HTTPS_URL, "auto": True})
        for upstream in (PLACEHOLDER_URL, "http://example.com/m.json", "http://127.0.0.1.evil.com/m.json", "ftp://x/m.json", 5, None, "", ["x"]):
            with self.subTest(upstream=upstream):
                self.write({"manifest_url": PUBLIC_URL, "upstream_manifest_url": upstream})
                self.assertEqual(updater.channel(self.root, {}), {"manifest_url": PUBLIC_URL, "upstream_manifest_url": None, "auto": True})
        for upstream in ("http://127.0.0.1:34999/m.json", "http://localhost:34999/m.json"):
            with self.subTest(upstream=upstream):
                self.write({"manifest_url": PUBLIC_URL, "upstream_manifest_url": upstream})
                self.assertEqual(updater.channel(self.root, {})["upstream_manifest_url"], upstream)
        # Variabila de mediu suprascrie doar canalul public; upstream-ul rămâne cel din fișier.
        self.write({"manifest_url": PUBLIC_URL, "upstream_manifest_url": HTTPS_URL})
        self.assertEqual(updater.channel(self.root, {"STUDIO_HARNESS_UPDATE_URL": "https://example.com/m.json"}),
                         {"manifest_url": "https://example.com/m.json", "upstream_manifest_url": HTTPS_URL, "auto": True})
        # Cele două URL-uri se validează independent: unul invalid nu îl strică pe celălalt.
        self.write({"manifest_url": PLACEHOLDER_URL, "upstream_manifest_url": HTTPS_URL})
        self.assertEqual(updater.channel(self.root, {}), {"manifest_url": None, "upstream_manifest_url": HTTPS_URL, "auto": True})


class ManifestTests(unittest.TestCase):
    def entry(self, **overrides):
        entry = {"url": "https://example.com/plugin.zip", "sha256": "a" * 64, "size": 10}
        for key, value in overrides.items():
            if value is None:
                entry.pop(key)
            else:
                entry[key] = value
        return entry

    def test_valid_manifest_is_returned_unchanged(self):
        manifest = {"version": "0.7.0", "files": {"plugin": self.entry(), "hub": self.entry(url="http://127.0.0.1:1/h.zip", sha256="0" * 64, size=updater.MAX_BUNDLE)}}
        self.assertIs(updater.validate_manifest(manifest), manifest)
        # Un singur pachet este suficient (hub-ul găzduit nu are nevoie de `plugin`).
        hub_only = {"version": "1.0", "files": {"hub": self.entry()}}
        self.assertIs(updater.validate_manifest(hub_only), hub_only)

    def test_structure_rejections(self):
        cases = [
            (None, "obiect JSON"), ([], "obiect JSON"), ("{}", "obiect JSON"), (7, "obiect JSON"),
            ({"files": {"plugin": self.entry()}}, "Versiune invalidă"),
            ({"version": "x", "files": {"plugin": self.entry()}}, "Versiune invalidă"),
            ({"version": 7, "files": {"plugin": self.entry()}}, "Versiune invalidă"),
            ({"version": "0.7.0"}, "nu conține fișiere"),
            ({"version": "0.7.0", "files": {}}, "nu conține fișiere"),
            ({"version": "0.7.0", "files": []}, "nu conține fișiere"),
            ({"version": "0.7.0", "files": {"extra": self.entry()}}, "Tip de pachet necunoscut în manifest: extra"),
            ({"version": "0.7.0", "files": {"plugin": self.entry(), "Plugin": self.entry()}}, "necunoscut"),
        ]
        for manifest, message in cases:
            with self.subTest(manifest=manifest), self.assertRaisesRegex(UpdateError, message):
                updater.validate_manifest(manifest)

    def test_entry_rejections_name_the_bundle(self):
        entries = [
            "text", None, [], self.entry(url=None), self.entry(url=5), self.entry(url="http://example.com/p.zip"), self.entry(url="ftp://x/p.zip"),
            self.entry(sha256=None), self.entry(sha256="a" * 63), self.entry(sha256="a" * 65), self.entry(sha256="A" * 64), self.entry(sha256="g" * 64),
            self.entry(sha256=123), self.entry(size=None), self.entry(size="10"), self.entry(size=0), self.entry(size=-1), self.entry(size=True),
            self.entry(size=1.5), self.entry(size=updater.MAX_BUNDLE + 1),
        ]
        for entry in entries:
            for kind in ("plugin", "hub"):
                with self.subTest(kind=kind, entry=entry), self.assertRaisesRegex(UpdateError, "Intrarea " + kind + " din manifest este invalidă"):
                    updater.validate_manifest({"version": "0.7.0", "files": {kind: entry}})


class FetchTests(unittest.TestCase):
    def test_fetch_manifest_reads_and_validates_the_local_channel(self):
        with LocalChannel() as channel:
            manifest = channel.manifest("0.7.0", plugin={"url": "https://example.com/p.zip", "sha256": "a" * 64, "size": 1})
            self.assertEqual(updater.fetch_manifest(channel.url("/manifest.json"), timeout=3), manifest)
            self.assertEqual(channel.requests, ["/manifest.json"])

    def test_fetch_manifest_refuses_bad_json_large_bodies_and_http_errors(self):
        with LocalChannel() as channel:
            oversized = b'{"version": "0.7.0", "notes": "' + b"a" * updater.MAX_MANIFEST + b'", "files": {}}'
            cases = [
                (channel.serve("/broken.json", b'{"version": '), "nu este JSON valid"),
                (channel.serve("/binary.json", b"\xff\xfe\x00\x01"), "nu este JSON valid"),
                (channel.serve("/large.json", oversized), "depășește limita"),
                (channel.serve("/missing.json", b"", status=404), "HTTP 404"),
                (channel.serve("/error.json", b"", status=500), "HTTP 500"),
                (channel.serve("/list.json", b"[]"), "obiect JSON"),
                (channel.serve("/nofiles.json", b'{"version": "0.7.0"}'), "nu conține fișiere"),
                (channel.serve("/unknown.json", json.dumps({"version": "0.7.0", "files": {"extra": {}}}).encode()), "necunoscut"),
                (channel.serve("/external.json", json.dumps({"version": "0.7.0", "files": {"plugin": {"url": "http://example.com/p.zip", "sha256": "a" * 64, "size": 1}}}).encode()), "invalidă"),
            ]
            for url, message in cases:
                with self.subTest(url=url), self.assertRaisesRegex(UpdateError, message):
                    updater.fetch_manifest(url, timeout=3)

    def test_external_http_is_refused_before_any_request_and_a_dead_channel_is_an_update_error(self):
        with patch.object(updater, "urlopen", side_effect=AssertionError("nu trebuie să ajungă în rețea")):
            for url in ("http://example.com/manifest.json", "ftp://example.com/manifest.json", "file:///manifest.json"):
                with self.subTest(url=url), self.assertRaisesRegex(UpdateError, "HTTPS"):
                    updater.fetch_manifest(url, timeout=3)
        with patch.object(updater, "urlopen", refused):
            with self.assertRaisesRegex(UpdateError, "nu răspunde: URLError"):
                updater.fetch_manifest("http://127.0.0.1:1/manifest.json", timeout=3)
        with patch.object(updater, "urlopen", side_effect=TimeoutError("timp expirat")):
            with self.assertRaisesRegex(UpdateError, "nu răspunde: TimeoutError"):
                updater.fetch_manifest("https://example.com/manifest.json", timeout=3)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.root = Path(self.temp.name)
        (self.root / ".claude-plugin").mkdir()
        (self.root / ".claude-plugin" / "plugin.json").write_text('{"version": "0.6.0"}', encoding="utf-8")
        (self.root / "update-channel.json").write_text(json.dumps({"manifest_url": PLACEHOLDER_URL, "auto": True}), encoding="utf-8")
        self.channel = LocalChannel().__enter__()
        self.plugin = self.channel.entry("/plugin.zip", b"nu se descarca la check")

    def tearDown(self):
        self.channel.__exit__(None, None, None)
        self.temp.cleanup()

    def environ(self, path="/manifest.json", **extra):
        return {"STUDIO_HARNESS_UPDATE_URL": self.channel.url(path), **extra}

    def test_newer_manifest_is_reported_without_downloading(self):
        manifest = self.channel.manifest("0.7.0", plugin=self.plugin)
        result = updater.check(self.root, timeout=3, environ=self.environ())
        self.assertEqual(result, {"current": "0.6.0", "available": "0.7.0", "newer": True, "auto": True,
                                  "channel": self.channel.url("/manifest.json"), "manifest": manifest, "error": None})
        self.assertEqual(self.channel.requests, ["/manifest.json"])

    def test_same_or_older_manifest_is_not_newer(self):
        for version in ("0.6.0", "0.6", "0.6.0.0", "0.5.9", "0.0.1"):
            with self.subTest(version=version):
                self.channel.manifest(version, plugin=self.plugin)
                result = updater.check(self.root, timeout=3, environ=self.environ())
                self.assertEqual((result["current"], result["available"], result["newer"], result["error"]), ("0.6.0", version, False, None))
        self.channel.manifest("0.6.1", plugin=self.plugin)
        self.assertTrue(updater.check(self.root, timeout=3, environ=self.environ())["newer"])

    def test_channel_errors_are_reported_not_raised(self):
        self.channel.serve("/broken.json", b"{")
        for path, message in (("/missing.json", "HTTP 404"), ("/broken.json", "JSON valid")):
            with self.subTest(path=path):
                result = updater.check(self.root, timeout=3, environ=self.environ(path))
                self.assertEqual((result["current"], result["available"], result["newer"], result["manifest"]), ("0.6.0", None, False, None))
                self.assertIn(message, result["error"])

    def test_unconfigured_channel_makes_no_request(self):
        for environ in ({}, {"STUDIO_HARNESS_UPDATE_URL": "http://example.com/manifest.json"}):
            with self.subTest(environ=environ):
                result = updater.check(self.root, timeout=3, environ=environ)
                self.assertEqual((result["current"], result["newer"], result["channel"], result["manifest"]), ("0.6.0", False, None, None))
                self.assertIn("neconfigurat", result["error"])
        self.assertEqual(self.channel.requests, [])

    def test_explicit_current_version_overrides_plugin_json(self):
        # Hub-ul găzduit nu are plugin.json lângă el: transmite VERSION din team_hub.py.
        (self.root / ".claude-plugin" / "plugin.json").unlink()
        self.assertEqual(updater.current_version(self.root), "0.0.0")
        self.channel.manifest("0.6.0", hub=self.plugin)
        result = updater.check(self.root, timeout=3, environ=self.environ(), current="0.6.0")
        self.assertEqual((result["current"], result["available"], result["newer"]), ("0.6.0", "0.6.0", False))
        self.assertTrue(updater.check(self.root, timeout=3, environ=self.environ(), current="0.5.9")["newer"])
        fallback = updater.check(self.root, timeout=3, environ=self.environ())
        self.assertEqual((fallback["current"], fallback["newer"]), ("0.0.0", True))

    def test_auto_flag_follows_the_environment(self):
        self.channel.manifest("0.7.0", plugin=self.plugin)
        result = updater.check(self.root, timeout=3, environ=self.environ(STUDIO_HARNESS_AUTO_UPDATE="0"))
        self.assertEqual((result["newer"], result["auto"]), (True, False))

    def test_explicit_manifest_url_overrides_the_channel_and_is_validated_the_same_way(self):
        # 0.8: hub-ul găzduit verifică upstream-ul (Hugging Face), nu canalul public pe care îl servește el însuși.
        manifest = self.channel.manifest("0.7.0", plugin=self.plugin)
        result = updater.check(self.root, timeout=3, environ={}, manifest_url=self.channel.url("/manifest.json"))
        self.assertEqual(result, {"current": "0.6.0", "available": "0.7.0", "newer": True, "auto": True,
                                  "channel": self.channel.url("/manifest.json"), "manifest": manifest, "error": None})
        # Bate și variabila de mediu (care arată spre o rută inexistentă); `auto` vine tot din canal.
        result = updater.check(self.root, timeout=3, environ=self.environ("/absent.json", STUDIO_HARNESS_AUTO_UPDATE="0"),
                               manifest_url=self.channel.url("/manifest.json"))
        self.assertEqual((result["newer"], result["auto"], result["channel"], result["error"]), (True, False, self.channel.url("/manifest.json"), None))
        self.assertEqual(self.channel.requests, ["/manifest.json", "/manifest.json"])
        for url in (PLACEHOLDER_URL, "http://example.com/manifest.json", "http://127.0.0.1.evil.com/manifest.json", "ftp://x/manifest.json", "file:///manifest.json"):
            with self.subTest(url=url):
                result = updater.check(self.root, timeout=3, environ=self.environ(), manifest_url=url)
                self.assertEqual((result["current"], result["newer"], result["channel"], result["manifest"]), ("0.6.0", False, None, None))
                self.assertIn("neconfigurat", result["error"])
        # Gol sau None înseamnă fără override: rămâne canalul din mediu/fișier.
        for url in ("", None):
            with self.subTest(url=url):
                self.assertTrue(updater.check(self.root, timeout=3, environ=self.environ(), manifest_url=url)["newer"])
        self.assertEqual(self.channel.requests, ["/manifest.json"] * 4)


class DownloadTests(unittest.TestCase):
    def test_download_verifies_size_and_sha256(self):
        with LocalChannel() as channel:
            data = "conținut de pachet ".encode("utf-8") * 200
            entry = channel.entry("/p.zip", data)
            self.assertEqual(updater.download(entry, timeout=3), data)
            with self.assertRaisesRegex(UpdateError, "SHA256"):
                updater.download({**entry, "sha256": "0" * 64}, timeout=3)
            with self.assertRaisesRegex(UpdateError, "SHA256"):
                updater.download({**entry, "size": len(data) + 1}, timeout=3)
            with self.assertRaisesRegex(UpdateError, "depășește limita"):
                updater.download({**entry, "size": len(data) - 1}, timeout=3)
            with self.assertRaisesRegex(UpdateError, "HTTP 404"):
                updater.download({**entry, "url": channel.url("/absent.zip")}, timeout=3)
            self.assertEqual(channel.requests.count("/p.zip"), 4)

    def test_download_refuses_external_http(self):
        with patch.object(updater, "urlopen", side_effect=AssertionError("nu trebuie să ajungă în rețea")):
            with self.assertRaisesRegex(UpdateError, "HTTPS"):
                updater.download({"url": "http://example.com/p.zip", "sha256": "a" * 64, "size": 1}, timeout=3)

    def test_bundle_sha256_matches_hashlib(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temp:
            path = Path(temp) / "p.zip"
            path.write_bytes(b"x" * (3 * 1024 * 1024 + 7))
            self.assertEqual(updater.bundle_sha256(path), hashlib.sha256(path.read_bytes()).hexdigest())


class MirrorTests(unittest.TestCase):
    """0.8: hub-ul găzduit oglindește pachetele din upstream (Hugging Face) în `releases/`, servit apoi la https://lostcube.pro/releases/."""

    PUBLIC_BASE = "https://lostcube.pro/releases"
    PLUGIN_NAME = "roblox-studio-harness-9.9.9.zip"
    HUB_NAME = "studio-harness-hub-9.9.9-ubuntu.zip"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.releases = Path(self.temp.name) / "app" / "releases"
        self.channel = LocalChannel().__enter__()
        self.plugin_data = make_zip({"README.md": b"plugin\n"}, top="roblox-studio-harness-9.9.9")
        self.hub_data = make_zip({"scripts/team_hub.py": b"# hub\n"}, top="studio-harness-hub-9.9.9")
        self.manifest = self.channel.manifest("9.9.9", plugin=self.channel.entry("/" + self.PLUGIN_NAME, self.plugin_data),
                                              hub=self.channel.entry("/" + self.HUB_NAME, self.hub_data))

    def tearDown(self):
        self.channel.__exit__(None, None, None)
        self.temp.cleanup()

    def mirror(self, manifest=None, base=PUBLIC_BASE):
        return updater.mirror(manifest or self.manifest, self.releases, base, timeout=3)

    def with_plugin(self, **overrides):
        return {**self.manifest, "files": {"plugin": {**self.manifest["files"]["plugin"], **overrides}}}

    def listing(self):
        return sorted(path.name for path in self.releases.iterdir()) if self.releases.exists() else None

    def sha_line(self, kind, name):
        return (self.manifest["files"][kind]["sha256"] + "  " + name + "\n").encode("utf-8")

    def test_mirror_downloads_verifies_and_rewrites_the_manifest_to_the_public_base(self):
        original = json.loads(json.dumps(self.manifest))
        mirrored = self.mirror(base=self.PUBLIC_BASE + "/")
        self.assertEqual(self.channel.requests, ["/" + self.PLUGIN_NAME, "/" + self.HUB_NAME])
        self.assertEqual(self.listing(), ["manifest.json", self.PLUGIN_NAME, self.PLUGIN_NAME + ".sha256", self.HUB_NAME, self.HUB_NAME + ".sha256"])
        self.assertEqual((self.releases / self.PLUGIN_NAME).read_bytes(), self.plugin_data)
        self.assertEqual((self.releases / self.HUB_NAME).read_bytes(), self.hub_data)
        for kind, name in (("plugin", self.PLUGIN_NAME), ("hub", self.HUB_NAME)):
            with self.subTest(kind=kind):
                self.assertEqual((self.releases / (name + ".sha256")).read_bytes(), self.sha_line(kind, name))
                self.assertEqual(mirrored["files"][kind], {**self.manifest["files"][kind], "url": self.PUBLIC_BASE + "/" + name})
        self.assertEqual({key: mirrored[key] for key in ("version", "published", "notes")}, {key: self.manifest[key] for key in ("version", "published", "notes")})
        written = (self.releases / "manifest.json").read_bytes()
        self.assertEqual(json.loads(written.decode("utf-8")), mirrored)
        self.assertNotIn(b"\r", written)
        self.assertIs(updater.validate_manifest(mirrored), mirrored)
        # Manifestul primit rămâne neatins: hub-ul descarcă apoi propriul pachet din upstream cu el.
        self.assertEqual(self.manifest, original)

    def test_mirror_keeps_correct_files_and_redownloads_only_the_wrong_ones(self):
        self.mirror()
        (self.releases / "manifest.json").write_bytes(b"{}")
        self.channel.requests.clear()
        self.assertEqual(self.mirror(), json.loads((self.releases / "manifest.json").read_text(encoding="utf-8")))
        self.assertEqual(self.channel.requests, [])
        # Aceeași mărime, conținut diferit: suma nu mai corespunde, se redescarcă doar acel pachet; .sha256 se rescrie mereu.
        (self.releases / self.HUB_NAME).write_bytes(b"\0" * len(self.hub_data))
        (self.releases / (self.PLUGIN_NAME + ".sha256")).write_bytes(b"gresit\n")
        self.mirror()
        self.assertEqual(self.channel.requests, ["/" + self.HUB_NAME])
        self.assertEqual((self.releases / self.HUB_NAME).read_bytes(), self.hub_data)
        self.assertEqual((self.releases / (self.PLUGIN_NAME + ".sha256")).read_bytes(), self.sha_line("plugin", self.PLUGIN_NAME))
        # Mărime diferită (fișier trunchiat): la fel.
        (self.releases / self.PLUGIN_NAME).write_bytes(self.plugin_data[:-1])
        self.channel.requests.clear()
        self.mirror()
        self.assertEqual(self.channel.requests, ["/" + self.PLUGIN_NAME])
        self.assertEqual((self.releases / self.PLUGIN_NAME).read_bytes(), self.plugin_data)
        self.assertEqual(len(self.listing()), 5)

    def test_mirror_refuses_a_non_https_public_base_before_touching_disk_or_network(self):
        for base in ("http://example.com/releases", "http://192.168.1.10/releases", "http://127.0.0.1.evil.com/releases",
                     "ftp://lostcube.pro/releases", "lostcube.pro/releases", "", "https:/lostcube.pro"):
            with self.subTest(base=base), self.assertRaisesRegex(UpdateError, "HTTPS"):
                self.mirror(base=base)
        self.assertEqual(self.channel.requests, [])
        self.assertIsNone(self.listing())
        # Loopback este permis (teste și hub local).
        self.mirror(base=self.channel.url("/releases"))
        self.assertEqual(json.loads((self.releases / "manifest.json").read_text(encoding="utf-8"))["files"]["hub"]["url"],
                         self.channel.url("/releases/" + self.HUB_NAME))

    def test_mirror_refuses_invalid_manifests_names_and_checksums_without_leaving_files(self):
        with self.assertRaisesRegex(UpdateError, "necunoscut"):
            self.mirror({"version": "9.9.9", "files": {"extra": self.manifest["files"]["hub"]}})
        with self.assertRaisesRegex(UpdateError, "Intrarea plugin"):
            self.mirror(self.with_plugin(url="http://example.com/" + self.PLUGIN_NAME))
        self.assertIsNone(self.listing())
        # Numele vine din URL: doar litere, cifre, . _ -, cel mult 120 de caractere; nu `.`/`..`, nu query, nu spații.
        for name in ("", "p.zip?x=1", "a b.zip", "p%20.zip", ".", "..", "x" * 121):
            with self.subTest(name=name), self.assertRaisesRegex(UpdateError, "Nume de fișier invalid"):
                self.mirror(self.with_plugin(url=self.channel.url("/") + name))
        self.assertEqual(self.channel.requests, [])
        # Doar ultimul segment contează: un URL cu subdirectoare se oglindește sub numele fișierului.
        self.assertEqual(self.mirror(self.with_plugin(url=self.channel.serve("/sub/dir/" + self.PLUGIN_NAME, self.plugin_data)))["files"]["plugin"]["url"],
                         self.PUBLIC_BASE + "/" + self.PLUGIN_NAME)
        self.assertEqual(self.channel.requests, ["/sub/dir/" + self.PLUGIN_NAME])
        (self.releases / self.PLUGIN_NAME).unlink()
        (self.releases / "manifest.json").unlink()
        (self.releases / (self.PLUGIN_NAME + ".sha256")).unlink()
        self.channel.requests.clear()
        for field, value in (("sha256", "0" * 64), ("size", len(self.plugin_data) + 1)):
            with self.subTest(field=field), self.assertRaisesRegex(UpdateError, "SHA256"):
                self.mirror(self.with_plugin(**{field: value}))
        self.assertEqual(self.channel.requests, ["/" + self.PLUGIN_NAME] * 2)
        # Nimic scris în releases/ (nici .part) și nimic lângă el.
        self.assertEqual(self.listing(), [])
        self.assertEqual(sorted(path.name for path in self.releases.parent.iterdir()), ["releases"])


FILES = {"README.md": b"nou\n", "scripts/a.py": b"print('nou')\n", "deploy/install.sh": b"#!/bin/sh\necho nou\n",
         "dist/StudioHarness.rbxmx": b"<roblox>nou</roblox>", "nested/deep/file.txt": b"x"}


class ApplyBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.target = Path(self.temp.name) / "plugin"
        self.backups = Path(self.temp.name) / "state" / "backups"
        (self.target / "scripts").mkdir(parents=True)
        (self.target / "README.md").write_bytes(b"vechi\n")
        (self.target / "scripts" / "a.py").write_bytes(b"print('vechi')\n")
        (self.target / "keep.txt").write_text("rămâne\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def staging_leftovers(self):
        return [path.name for path in self.target.parent.iterdir() if ".update-" in path.name]

    def assert_untouched(self):
        self.assertEqual((self.target / "README.md").read_bytes(), b"vechi\n")
        self.assertEqual((self.target / "scripts" / "a.py").read_bytes(), b"print('vechi')\n")
        self.assertFalse((self.target / "deploy").exists())
        self.assertFalse(self.backups.exists())
        self.assertEqual(self.staging_leftovers(), [])

    def test_files_are_written_and_replaced_ones_are_backed_up(self):
        result = updater.apply_bundle(make_zip(FILES), self.target, self.backups)
        self.assertEqual(sorted(result["written"]), sorted(FILES))
        for relative, data in FILES.items():
            self.assertEqual((self.target / relative).read_bytes(), data, relative)
        self.assertEqual((self.target / "keep.txt").read_text(encoding="utf-8"), "rămâne\n")
        backup = Path(result["backup"])
        self.assertEqual(backup.parent, self.backups)
        self.assertEqual((backup / "README.md").read_bytes(), b"vechi\n")
        self.assertEqual((backup / "scripts" / "a.py").read_bytes(), b"print('vechi')\n")
        # Doar fișierele înlocuite ajung în backup; fișierele noi și cele neatinse nu.
        self.assertEqual(sorted(path.relative_to(backup).as_posix() for path in backup.rglob("*") if path.is_file()), ["README.md", "scripts/a.py"])
        self.assertEqual(self.staging_leftovers(), [])
        if os.name != "nt":
            self.assertEqual((self.target / "deploy" / "install.sh").stat().st_mode & 0o777, 0o755)

    def test_protect_keeps_local_files_and_folders_untouched(self):
        result = updater.apply_bundle(make_zip(FILES), self.target, self.backups, protect=("scripts/a.py", "dist/"))
        self.assertEqual(sorted(result["written"]), ["README.md", "deploy/install.sh", "nested/deep/file.txt"])
        self.assertEqual((self.target / "scripts" / "a.py").read_bytes(), b"print('vechi')\n")
        self.assertFalse((self.target / "dist").exists())
        self.assertEqual((self.target / "README.md").read_bytes(), b"nou\n")
        backup = Path(result["backup"])
        self.assertEqual([path.relative_to(backup).as_posix() for path in backup.rglob("*") if path.is_file()], ["README.md"])

    def test_flatten_moves_a_prefix_to_the_root_like_the_hosted_hub(self):
        app = Path(self.temp.name) / "app"
        app.mkdir()
        (app / "a.py").write_bytes(b"print('vechi')\n")
        result = updater.apply_bundle(make_zip(FILES), app, self.backups, flatten="scripts/")
        self.assertEqual(sorted(result["written"]), ["README.md", "a.py", "deploy/install.sh", "dist/StudioHarness.rbxmx", "nested/deep/file.txt"])
        self.assertEqual((app / "a.py").read_bytes(), b"print('nou')\n")
        self.assertFalse((app / "scripts").exists())
        self.assertEqual((app / "deploy" / "install.sh").read_bytes(), FILES["deploy/install.sh"])
        self.assertEqual((Path(result["backup"]) / "a.py").read_bytes(), b"print('vechi')\n")

    def test_paths_that_escape_the_target_are_refused_before_anything_is_written(self):
        # Folderul de top este eliminat, deci verificarea acoperă toate segmentele: `pachet/C:/evil.txt` ar deveni absolut pe Windows.
        for name in ("pachet/../evil.txt", "../evil.txt", "/etc/evil.txt", "C:/evil.txt", "pachet/sub/../../evil.txt", "pachet\\..\\evil.txt",
                     "pachet/C:/evil.txt", "pachet/C:evil.txt"):
            with self.subTest(name=name):
                data = make_zip({"pachet/ok.txt": b"ok", name: "rău".encode("utf-8")}, raw=True)
                with self.assertRaisesRegex(UpdateError, "cale invalidă"):
                    updater.apply_bundle(data, self.target, self.backups)
                self.assertFalse((self.target / "ok.txt").exists())
                self.assertFalse((self.target.parent / "evil.txt").exists())
        self.assert_untouched()

    def test_entries_outside_the_top_folder_are_ignored_not_flattened_next_to_it(self):
        """`_members` elimină folderul de top; un fișier lăsat la rădăcina arhivei ar ajunge altfel lângă fișierele aplatizate."""
        data = make_zip({"pachet-1.0.0/scripts/a.py": b"print('nou')\n", "rau.py": b"print('intrus')\n"}, raw=True)
        result = updater.apply_bundle(data, self.target, self.backups)
        self.assertEqual(result["written"], ["scripts/a.py"])
        self.assertFalse((self.target / "rau.py").exists())
        self.assertEqual((self.target / "scripts" / "a.py").read_bytes(), b"print('nou')\n")
        # Un pachet format doar din fișiere de la rădăcină nu mai are ce aplica.
        with self.assertRaisesRegex(UpdateError, "gol"):
            updater.apply_bundle(make_zip({"doar-radacina.py": b"x"}, raw=True), self.target, self.backups)
        self.assertFalse((self.target / "doar-radacina.py").exists())

    def test_git_checkout_invalid_zip_and_empty_bundle_are_refused(self):
        (self.target / ".git").mkdir()
        self.assertTrue(updater.is_git_checkout(self.target))
        with self.assertRaisesRegex(UpdateError, "checkout git"):
            updater.apply_bundle(make_zip(FILES), self.target, self.backups)
        (self.target / ".git").rmdir()
        # Un worktree are `.git` fișier, nu director: tot checkout este.
        (self.target / ".git").write_text("gitdir: ../repo/.git/worktrees/x\n", encoding="utf-8")
        with self.assertRaisesRegex(UpdateError, "checkout git"):
            updater.apply_bundle(make_zip(FILES), self.target, self.backups)
        (self.target / ".git").unlink()
        self.assertFalse(updater.is_git_checkout(self.target))
        with self.assertRaisesRegex(UpdateError, "zip valid"):
            updater.apply_bundle(b"nu este un zip", self.target, self.backups)
        for data in (make_zip({}), make_zip({"pachet/": b"", "pachet/sub/": b""}, raw=True)):
            with self.assertRaisesRegex(UpdateError, "gol"):
                updater.apply_bundle(data, self.target, self.backups)
        self.assert_untouched()


class StudioPluginInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.local = Path(self.temp.name) / "local"
        self.environ = {"LOCALAPPDATA": str(self.local)}
        self.source = Path(self.temp.name) / "dist" / "StudioHarness.rbxmx"
        self.backups = Path(self.temp.name) / "state" / "plugin-backups"
        self.plugins = self.local / "Roblox" / "Plugins"

    def tearDown(self):
        self.temp.cleanup()

    def test_plugins_dir_needs_windows_and_localappdata(self):
        self.assertIsNone(updater.studio_plugins_dir({}))
        self.assertIsNone(updater.studio_plugins_dir({"LOCALAPPDATA": ""}))
        with patch.object(updater.os, "name", "posix"):
            self.assertIsNone(updater.studio_plugins_dir(self.environ))
            self.source.parent.mkdir()
            self.source.write_bytes(b"<roblox/>")
            self.assertIsNone(updater.install_studio_plugin(self.source, self.backups, self.environ))
        self.assertFalse(self.local.exists())
        if os.name == "nt":
            self.assertEqual(updater.studio_plugins_dir(self.environ), self.plugins)

    def test_missing_source_is_a_no_op(self):
        if os.name != "nt":
            self.skipTest("folderul de pluginuri Studio există doar pe Windows")
        self.assertIsNone(updater.install_studio_plugin(self.source, self.backups, self.environ))
        self.assertFalse(self.local.exists())
        self.assertFalse(self.backups.exists())

    def test_install_backs_up_a_different_plugin_and_skips_an_identical_one(self):
        if os.name != "nt":
            self.skipTest("folderul de pluginuri Studio există doar pe Windows")
        self.source.parent.mkdir()
        self.source.write_bytes(b"<roblox>v1</roblox>")
        target = updater.install_studio_plugin(self.source, self.backups, self.environ)
        self.assertEqual(target, self.plugins / "StudioHarness.rbxmx")
        self.assertEqual(target.read_bytes(), b"<roblox>v1</roblox>")
        self.assertFalse(self.backups.exists())
        # Identic: nimic de copiat, niciun backup.
        self.assertEqual(updater.install_studio_plugin(self.source, self.backups, self.environ), target)
        self.assertFalse(self.backups.exists())
        self.source.write_bytes(b"<roblox>v2</roblox>")
        self.assertEqual(updater.install_studio_plugin(self.source, self.backups, self.environ), target)
        self.assertEqual(target.read_bytes(), b"<roblox>v2</roblox>")
        saved = list(self.backups.iterdir())
        self.assertEqual(len(saved), 1)
        self.assertRegex(saved[0].name, r"^StudioHarness-\d{8}-\d{6}\.rbxmx$")
        self.assertEqual(saved[0].read_bytes(), b"<roblox>v1</roblox>")
        self.assertEqual(sorted(path.name for path in self.plugins.iterdir()), ["StudioHarness.rbxmx"])


# Ca pachetul real: sursa loader-ului conține același șir ca literal (constanta cu care își respinge propriul cod
# neinstalat), pe lângă slotul `LocalToken`. Doar slotul devine tokenul.
PACKAGED_RBXMX = ('<roblox version="4"><Item class="Script"><Properties>'
                  '<ProtectedString name="Source">local TOKEN_PLACEHOLDER = "' + updater.LOCAL_TOKEN_PLACEHOLDER + '" '
                  'return token ~= TOKEN_PLACEHOLDER</ProtectedString></Properties>'
                  '<Item class="StringValue"><Properties><string name="Name">LocalToken</string>'
                  '<string name="Value">' + updater.LOCAL_TOKEN_PLACEHOLDER + '</string></Properties></Item></Item></roblox>').encode("utf-8")
UI_TOKEN = "cod-local-de-test_0123456789"


class LocalTokenInjectionTests(unittest.TestCase):
    """1.0: placeholder-ul `LocalToken` din pachet devine tokenul UI doar în fișierul instalat (funcție pură, pe orice sistem)."""

    def test_placeholder_is_replaced_only_in_the_local_token_slot(self):
        placeholder = updater.LOCAL_TOKEN_PLACEHOLDER.encode("utf-8")
        injected = updater.inject_local_token(PACKAGED_RBXMX, UI_TOKEN)
        # Tokenul intră o singură dată, în valoarea LocalToken.
        self.assertEqual(injected.count(UI_TOKEN.encode("utf-8")), 1)
        self.assertIn(b"<string name=\"Name\">LocalToken</string><string name=\"Value\">" + UI_TOKEN.encode("utf-8") + b"</string>", injected)
        # Constanta din sursa loader-ului rămâne literalul: altfel pluginul instalat și-ar respinge propriul cod
        # (token == TOKEN_PLACEHOLDER) și nu s-ar mai conecta niciodată la daemon.
        self.assertIn(b'local TOKEN_PLACEHOLDER = "' + placeholder + b'"', injected)
        self.assertEqual(injected.count(placeholder), PACKAGED_RBXMX.count(placeholder) - 1)
        self.assertEqual(len(injected), len(PACKAGED_RBXMX) - len(placeholder) + len(UI_TOKEN.encode("utf-8")))
        # Două sloturi LocalToken: pachet stricat, nu injectăm nimic.
        with self.assertRaisesRegex(UpdateError, "mai multe valori LocalToken"):
            updater.inject_local_token(PACKAGED_RBXMX + PACKAGED_RBXMX, UI_TOKEN)
        # Fără token: pachetul rămâne cu placeholder (câmpul manual din Avansat rămâne fallback-ul).
        self.assertEqual(updater.inject_local_token(PACKAGED_RBXMX, None), PACKAGED_RBXMX)
        # Un .rbxmx fără placeholder (loader vechi) nu se schimbă nici cu token.
        self.assertEqual(updater.inject_local_token(b"<roblox>vechi</roblox>", UI_TOKEN), b"<roblox>vechi</roblox>")
        # Formatul tokenului este cel din local_state (URL-safe, 16–512): nimic care ar putea strica XML-ul.
        self.assertRegex(updater.LOCAL_TOKEN_PLACEHOLDER, r"^[A-Z_]+$")
        for bad in ("", "scurt", "cu spatiu 0123456789", "a<b>0123456789abcdef", "ghilimea\"0123456789abcdef", "x" * 513, 12345):
            with self.subTest(bad=bad), self.assertRaisesRegex(UpdateError, "Codul local"):
                updater.inject_local_token(PACKAGED_RBXMX, bad)


class StudioPluginTokenInstallTests(unittest.TestCase):
    """install_studio_plugin(..., local_token=…): scrie pachetul cu tokenul injectat, fără să atingă dist/, și compară după substituire."""

    def setUp(self):
        if os.name != "nt":
            self.skipTest("folderul de pluginuri Studio există doar pe Windows")
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.local = Path(self.temp.name) / "local"
        self.environ = {"LOCALAPPDATA": str(self.local)}
        self.source = Path(self.temp.name) / "dist" / "StudioHarness.rbxmx"
        self.source.parent.mkdir()
        self.source.write_bytes(PACKAGED_RBXMX)
        self.backups = Path(self.temp.name) / "state" / "plugin-backups"
        self.target = self.local / "Roblox" / "Plugins" / "StudioHarness.rbxmx"

    def tearDown(self):
        self.temp.cleanup()

    def install(self, token):
        return updater.install_studio_plugin(self.source, self.backups, self.environ, local_token=token)

    def backups_listing(self):
        return sorted(path.name for path in self.backups.iterdir()) if self.backups.exists() else []

    def test_token_lands_in_the_installed_file_and_the_package_stays_a_placeholder(self):
        self.assertEqual(self.install(UI_TOKEN), self.target)
        installed = self.target.read_bytes()
        self.assertEqual(installed, updater.inject_local_token(PACKAGED_RBXMX, UI_TOKEN))
        # Doar slotul devine tokenul; constanta din sursa loader-ului rămâne literalul.
        self.assertEqual(installed.count(UI_TOKEN.encode("utf-8")), 1)
        self.assertIn(b'local TOKEN_PLACEHOLDER = "' + updater.LOCAL_TOKEN_PLACEHOLDER.encode("utf-8") + b'"', installed)
        self.assertEqual(self.source.read_bytes(), PACKAGED_RBXMX)
        self.assertEqual(self.backups_listing(), [])
        self.assertEqual(sorted(path.name for path in self.target.parent.iterdir()), ["StudioHarness.rbxmx"])
        # Același pachet, același token: „identic” după substituire, deci fără rescriere și fără backup.
        self.assertEqual(self.install(UI_TOKEN), self.target)
        self.assertEqual(self.backups_listing(), [])
        # Alt token (fișierul local-token regenerat): fișierul se rescrie, cel vechi ajunge în backup.
        other = "alt-cod-local_9876543210abcdef"
        self.assertEqual(self.install(other), self.target)
        self.assertEqual(self.target.read_bytes(), updater.inject_local_token(PACKAGED_RBXMX, other))
        self.assertEqual(len(self.backups_listing()), 1)
        self.assertEqual((self.backups / self.backups_listing()[0]).read_bytes(), installed)

    def test_without_a_token_the_placeholder_is_kept_and_counts_as_a_different_file(self):
        self.assertEqual(self.install(None), self.target)
        self.assertEqual(self.target.read_bytes(), PACKAGED_RBXMX)
        self.assertEqual(self.backups_listing(), [])
        self.assertEqual(self.install(UI_TOKEN), self.target)
        self.assertEqual(self.target.read_bytes().count(UI_TOKEN.encode("utf-8")), 1)
        self.assertEqual(len(self.backups_listing()), 1)

    def test_an_invalid_token_stops_the_install_before_anything_is_written(self):
        for bad in ("scurt", "cu spatiu 0123456789", "a<b>0123456789abcdef"):
            with self.subTest(bad=bad), self.assertRaisesRegex(UpdateError, "Codul local"):
                self.install(bad)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.backups.exists())
        # Cu un fișier deja instalat: rămâne neatins, fără backup, fără .tmp.
        self.install(UI_TOKEN)
        with self.assertRaisesRegex(UpdateError, "Codul local"):
            self.install("scurt")
        self.assertEqual(self.target.read_bytes(), updater.inject_local_token(PACKAGED_RBXMX, UI_TOKEN))
        self.assertEqual(self.backups_listing(), [])
        self.assertEqual(sorted(path.name for path in self.target.parent.iterdir()), ["StudioHarness.rbxmx"])


if __name__ == "__main__":
    unittest.main()
