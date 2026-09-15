"""Publicare: scanarea de secrete (1.0: și device-token/hub-admin-token), pachetele plugin+hub cu manifest verificabil (fără
Setup-Team/Start-Team-Hub, cu Start-Hub.cmd și Publish.cmd), canalul HF și `main --dry-run`.

Fără huggingface_hub și fără rețea: `upload` este înlocuit în teste, iar `main` rulează pe un repo minimal dintr-un director
temporar (ROOT din publish_release este redirecționat acolo), ca repo-ul real să nu fie modificat."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import publish_release
import updater
from test_updater import LocalChannel

# Construite la rulare, ca fișierul de test să nu conțină el însuși ceva ce seamănă cu un token.
HF_TOKEN = "hf_" + "A" * 30
ANTHROPIC_KEY = "sk-ant-" + "b" * 30
REAL_TEAM_TOKEN = "valoare-reala-0123456789"
DEVICE_TOKEN = "0123456789abcdef" * 4
# Antetele PEM sunt lipite la rulare din același motiv: altfel acest fișier ar fi raportat de `scan_secrets`, care citește `tests/`.
PEM_RSA = "-----BEGIN RSA PRIVATE" + " KEY-----\n" + "A" * 40 + "\n-----END RSA PRIVATE" + " KEY-----\n"
PEM_PLAIN = "-----BEGIN PRIVATE" + " KEY-----\n" + "B" * 40 + "\n-----END PRIVATE" + " KEY-----\n"
BASE_URL = "https://huggingface.co/spaces/ana/studio-harness/resolve/main/releases/"
PUBLIC_CHANNEL = publish_release.PUBLIC_CHANNEL


def build_plugin(root, local_token=None):
    """Construiește un `dist/StudioHarness.rbxmx` real (loader + Main); cu `local_token` îl rescrie ca un pachet deja instalat."""
    import build_studio_plugin
    write(root, "studio-plugin/StudioHarness.server.luau", "-- Studio Harness Loader 1.1.0\nlocal token = plugin\nreturn token\n")
    write(root, "studio-plugin/modules/Main.luau", "-- Studio Harness App 0.7.0\nreturn {}\n")
    package = root / "dist" / "StudioHarness.rbxmx"
    package.parent.mkdir(parents=True, exist_ok=True)
    build_studio_plugin.build(root / "studio-plugin" / "StudioHarness.server.luau", package, entry="Main", require_version=True)
    if local_token is not None:
        package.write_bytes(package.read_bytes().replace(updater.LOCAL_TOKEN_PLACEHOLDER.encode(), local_token.encode()))
    return package


def write(root, relative, content):
    """Scrie octeții exact (fără conversia de linii nouă a modului text pe Windows)."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


class ScanSecretsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_documentation_placeholders_and_clean_code_are_not_findings(self):
        write(self.root, "README.md", '# Doc\n{"team_token": "..."}\n{"team_token": "<token>"}\n{"password": "scurt"}\n{"device_token": "<64 hex>"}\n')
        write(self.root, "scripts/publish.py", 'token = os.environ.get("HF_TOKEN")\nkey = "sk-ant-"  # doar prefixul\n')
        write(self.root, "update-channel.json", json.dumps({"manifest_url": "https://huggingface.co/spaces/OWNER/x/resolve/main/releases/manifest.json", "auto": True}))
        write(self.root, "deploy/hub.service", "Environment=STUDIO_HARNESS_ADMIN_TOKEN=\n")
        # Placeholder-ul LocalToken din pachet și numele fișierelor de token din documente nu sunt secrete.
        write(self.root, "STUDIO_HUB_CONTRACT.md", "`local-token`, `device-token`, `hub-admin-token` stau în %LOCALAPPDATA%\\StudioHarness\\.\n")
        write(self.root, "scripts/build.py", 'LOCAL_TOKEN_PLACEHOLDER = "' + updater.LOCAL_TOKEN_PLACEHOLDER + '"\n')
        self.assertEqual(publish_release.scan_secrets(self.root), [])

    def test_secret_files_and_tokens_are_reported_without_leaking_them(self):
        write(self.root, ".env", "HF_TOKEN=" + HF_TOKEN + "\n")
        write(self.root, ".env.local", "X=1\n")
        write(self.root, "state/team.json", json.dumps({"developer": "ana", "team_token": "x" * 20}))
        write(self.root, "local-token", "cod-local-0123456789\n")
        write(self.root, "state/device-token", DEVICE_TOKEN + "\n")
        write(self.root, "state/hub-admin-token", "cod-admin-0123456789\n")
        write(self.root, "state/team-token", "cod-echipa-0123456789\n")
        write(self.root, "daemon.log", "jurnal\n")
        write(self.root, "notes.md", "token: " + HF_TOKEN + "\n")
        write(self.root, "scripts/keys.py", 'KEY = "' + ANTHROPIC_KEY + '"\n')
        # Fixturile JSON se construiesc cu `json.dumps`, nu prin lipirea textului: altfel chiar acest fișier ar conține `"cheie": "<valoare>"` și s-ar raporta singur.
        write(self.root, "state/config.json", json.dumps({"hub_url": "https://hub", "team_token": REAL_TEAM_TOKEN}) + "\n")
        write(self.root, "state/device.json", json.dumps({"device_token": DEVICE_TOKEN}) + "\n")
        write(self.root, "state/admin.json", json.dumps({"admin_token": REAL_TEAM_TOKEN}) + "\n")
        write(self.root, "state/hub.json", json.dumps({"hub_admin_token": REAL_TEAM_TOKEN}) + "\n")
        write(self.root, "scripts/aws.ps1", "$key = 'AKIA" + "Q" * 16 + "'\n")
        findings = publish_release.scan_secrets(self.root)
        by_path = {}
        for finding in findings:
            relative, label = finding.split(": ", 1)
            by_path[relative] = label
        for relative in (".env", ".env.local", "state/team.json", "local-token", "state/device-token", "state/hub-admin-token", "state/team-token", "daemon.log"):
            self.assertEqual(by_path.get(relative), "fișier care nu se publică", relative)
        self.assertEqual(by_path.get("notes.md"), "token Hugging Face (hf_AAAAA…)")
        self.assertEqual(by_path.get("scripts/keys.py"), "cheie Anthropic (sk-ant-b…)")
        self.assertEqual(by_path.get("state/config.json"), 'secret în JSON ("team_to…)')
        self.assertEqual(by_path.get("state/device.json"), 'secret în JSON ("device_…)')
        self.assertEqual(by_path.get("state/admin.json"), 'secret în JSON ("admin_t…)')
        self.assertEqual(by_path.get("state/hub.json"), 'secret în JSON ("hub_adm…)')
        self.assertEqual(by_path.get("scripts/aws.ps1"), "cheie AWS (AKIAQQQQ…)")
        self.assertEqual(len(findings), len(by_path))
        joined = "\n".join(findings)
        for secret in (HF_TOKEN, ANTHROPIC_KEY, REAL_TEAM_TOKEN, DEVICE_TOKEN, "AKIA" + "Q" * 16, "cod-admin", "cod-echipa", "cod-local"):
            self.assertNotIn(secret, joined)
        for name in ("device-token", "hub-admin-token", "team-token", "local-token", "team.json"):
            self.assertIn(name, publish_release.SECRET_NAMES)
            self.assertIn(name, publish_release.EXCLUDE_UPLOAD)

    def test_work_directories_and_binaries_are_not_scanned(self):
        for relative in ("release/leak.md", "release/x/leak.json", ".runtime/leak.json",
                         "scripts/__pycache__/leak.py", ".git/leak.txt", "node_modules/leak.js"):
            write(self.root, relative, "token: " + HF_TOKEN + "\n")
        write(self.root, "bundle.zip", HF_TOKEN.encode("utf-8"))
        write(self.root, "release/.env", "HF_TOKEN=" + HF_TOKEN + "\n")
        self.assertEqual(publish_release.scan_secrets(self.root), [])

    def test_tests_and_design_are_scanned_because_git_publishes_them(self):
        """Nu intră în pachete, dar `git add -A` le trimite pe GitHub: scanarea este singura barieră dinaintea publicării."""
        write(self.root, "tests/test_leak.py", "token = '" + HF_TOKEN + "'\n")
        write(self.root, "design/notite.md", "cheie: " + ANTHROPIC_KEY + "\n")
        findings = dict(finding.split(": ", 1) for finding in publish_release.scan_secrets(self.root))
        self.assertEqual(findings, {"design/notite.md": "cheie Anthropic (sk-ant-b…)", "tests/test_leak.py": "token Hugging Face (hf_AAAAA…)"})
        self.assertNotIn("tests", publish_release.SCAN_EXCLUDE_DIRS)
        self.assertNotIn("design", publish_release.SCAN_EXCLUDE_DIRS)

    def test_a_real_device_token_and_a_private_key_are_caught_by_content(self):
        """Un token de 64 hex lipit lângă un nume sugestiv și un bloc de cheie privată opresc publicarea, oriunde ar fi în repo."""
        write(self.root, "deploy/hub.service", "Environment=STUDIO_HARNESS_ADMIN_TOKEN=" + DEVICE_TOKEN + "\n")
        write(self.root, "tests/fixture.py", "device_token = '" + DEVICE_TOKEN + "'\n")
        write(self.root, "design/notite.md", "hub-admin-token: `" + DEVICE_TOKEN + "`\n")
        write(self.root, "deploy/tls.pem", PEM_RSA)
        write(self.root, "deploy/hub.key", PEM_PLAIN)
        findings = dict(finding.split(": ", 1) for finding in publish_release.scan_secrets(self.root))
        self.assertEqual(findings, {
            "deploy/hub.service": "token hex cu nume de secret (STUDIO_H…)",
            "tests/fixture.py": "token hex cu nume de secret (device_t…)",
            "design/notite.md": "token hex cu nume de secret (hub-admi…)",
            "deploy/tls.pem": "cheie privată (-----BEG…)",
            "deploy/hub.key": "cheie privată (-----BEG…)",
        })
        # Cheile și certificatele nu se împachetează niciodată, dar `git add -A` le-ar publica: scanarea trebuie să le citească.
        for suffix in (".pem", ".key", ".crt"):
            self.assertIn(suffix, publish_release.SCAN_SUFFIXES)
            self.assertNotIn(suffix, publish_release.TEXT_SUFFIXES)
        self.assertNotIn(DEVICE_TOKEN, "\n".join(publish_release.scan_secrets(self.root)))

    def test_every_shape_a_pasted_device_token_takes_is_caught(self):
        """Aceeași valoare, scrisă cum apare într-un .env, .yml, .ps1, .sh sau într-o notă: numele sugestiv o dă de gol."""
        for name, line in (("a.sh", "export HUB_ADMIN_TOKEN=" + DEVICE_TOKEN),
                           ("b.yml", "admin_token: " + DEVICE_TOKEN),
                           ("c.ps1", "$adminToken = '" + DEVICE_TOKEN + "'"),
                           ("d.md", "token = " + DEVICE_TOKEN),
                           ("e.conf", "local-token=" + DEVICE_TOKEN),
                           ("f.txt", "api_key: \"" + DEVICE_TOKEN + "\"")):
            with self.subTest(name=name):
                path = write(self.root, name, line + "\n")
                findings = publish_release.scan_secrets(self.root)
                self.assertEqual(len(findings), 1, findings)
                self.assertTrue(findings[0].startswith(name + ": token hex cu nume de secret ("), findings[0])
                # Constatarea se tipărește la publicare: arată numele variabilei, niciodată valoarea.
                self.assertNotIn(DEVICE_TOKEN[:16], findings[0])
                path.unlink()

    def test_hashes_and_token_shaped_test_fixtures_stay_clean(self):
        """Regulile de conținut sunt precise: sumele SHA256 și șirurile „arată a token” din fixture-uri nu opresc publicarea."""
        write(self.root, "releases/manifest.json", json.dumps({"version": "1.0.0", "files": {"plugin": {"sha256": DEVICE_TOKEN, "size": 10}}}))
        write(self.root, "releases/pachet.zip.sha256", DEVICE_TOKEN + "  roblox-studio-harness-1.0.0.zip\n")
        write(self.root, "tests/test_deploy.py", 'ADMIN_CODE = "cod-admin-de-test-0123456789"\nUI_TOKEN = "test-ui-token-0123456789"\n')
        write(self.root, "tests/test_client.py", 'DEVICE_TOKEN = "0123456789abcdef" * 4\nDEVICE_ID = "9d2b9935bf53b6f6"\n')
        write(self.root, "scripts/state.py", 'def new_device_token():\n    return secrets.token_hex(32)\n')
        write(self.root, "design/DESIGN.md", "Tokenul de dispozitiv are 64 de caractere hex; nu apare niciodată în jurnal.\n")
        self.assertEqual(publish_release.scan_secrets(self.root), [])

    def test_the_real_repository_scans_clean_with_tests_and_design_included(self):
        """Exact scanarea care rulează la publicare, pe repo-ul real: dacă ar avea fals-pozitive, nimic nu s-ar mai putea publica."""
        self.assertEqual(publish_release.scan_secrets(ROOT), [])

    def test_a_studio_package_with_an_injected_token_stops_the_publish(self):
        """`dist/StudioHarness.rbxmx` copiat dintr-o instalare are tokenul UI în locul placeholder-ului; nu ajunge pe GitHub."""
        build_plugin(self.root, local_token="cod-local-0123456789")
        self.assertEqual(publish_release.scan_secrets(self.root),
                         ["dist/StudioHarness.rbxmx: plugin cu tokenul local injectat (se publică doar cu placeholder)"])
        build_plugin(self.root)
        self.assertEqual(publish_release.scan_secrets(self.root), [])
        self.assertIn(".rbxmx", publish_release.TEXT_SUFFIXES)


class BuildTests(unittest.TestCase):
    """build() pe repo-ul real, cu directorul de release într-un director temporar."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        cls.release = Path(cls.temp.name) / "release"
        cls.version = updater.current_version(ROOT)
        cls.manifest = publish_release.build(cls.version, cls.release, BASE_URL)
        cls.plugin_zip = cls.release / f"roblox-studio-harness-{cls.version}.zip"
        cls.hub_zip = cls.release / f"studio-harness-hub-{cls.version}-ubuntu.zip"
        cls.app_zip = cls.release / f"studio-harness-app-{cls.version}.zip"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def members(self, path):
        with zipfile.ZipFile(path) as bundle:
            return bundle.infolist()

    def test_manifest_matches_the_zips_and_validates(self):
        self.assertEqual(self.manifest["version"], self.version)
        # 0.8: pachetul `studio_app` (modulele Luau), `loader_version` din antetul loader-ului și `bundle_revision`.
        self.assertEqual(set(self.manifest["files"]), {"plugin", "hub", "studio_app"})
        self.assertEqual(self.manifest["loader_version"], publish_release.loader_version(ROOT) or self.version)
        self.assertEqual(self.manifest["bundle_revision"], updater.bundle_revision(updater.read_luau_modules(ROOT / "studio-plugin" / "modules")))
        self.assertIs(updater.validate_manifest(self.manifest), self.manifest)
        for kind, path in (("plugin", self.plugin_zip), ("hub", self.hub_zip), ("studio_app", self.app_zip)):
            with self.subTest(kind=kind):
                entry = self.manifest["files"][kind]
                self.assertEqual(entry["url"], BASE_URL + path.name)
                self.assertEqual(entry["sha256"], updater.bundle_sha256(path))
                self.assertEqual(entry["size"], path.stat().st_size)
                self.assertEqual((self.release / (path.name + ".sha256")).read_text(encoding="utf-8"), entry["sha256"] + "  " + path.name + "\n")
        written = (self.release / "manifest.json").read_bytes()
        self.assertEqual(json.loads(written.decode("utf-8")), self.manifest)
        self.assertNotIn(b"\r", written)
        self.assertEqual(sorted(path.name for path in self.release.iterdir()),
                         sorted(["manifest.json", self.plugin_zip.name, self.plugin_zip.name + ".sha256", self.hub_zip.name, self.hub_zip.name + ".sha256",
                                 self.app_zip.name, self.app_zip.name + ".sha256"]))

    def test_plugin_bundle_has_a_top_folder_and_leaves_out_tests_design_runtime_and_release(self):
        top = f"roblox-studio-harness-{self.version}/"
        infos = self.members(self.plugin_zip)
        names = [info.filename for info in infos]
        self.assertTrue(all(name.startswith(top) and not name.endswith("/") for name in names), names[:5])
        relative = {name[len(top):]: info for name, info in zip(names, infos)}
        for required in ("scripts/studio_bridge.py", "scripts/team_hub.py", "scripts/updater.py", "scripts/harness_mcp.py", ".claude-plugin/plugin.json",
                         "update-channel.json", "deploy/ubuntu/install.sh", "dist/StudioHarness.rbxmx", "README.md", "Dockerfile", ".mcp.json",
                         "Publish.cmd", "Install-Studio-Plugin.cmd", "Start-Daemon.cmd", "deploy/ubuntu/Caddyfile"):
            self.assertIn(required, relative)
        # 1.0: fără echipe: Setup-Team.cmd, setup-team.ps1 și Start-Team-Hub.cmd nu intră în pachet nici dacă mai există în checkout;
        # Start-Hub.cmd (hub local) și Publish.cmd se livrează odată cu pluginul.
        for removed in ("Setup-Team.cmd", "scripts/setup-team.ps1", "Start-Team-Hub.cmd"):
            self.assertNotIn(removed, relative)
            self.assertNotIn(removed, publish_release.PLUGIN_INCLUDE)
            self.assertIn(removed, publish_release.EXCLUDE_FILES)
        for shipped in ("Start-Hub.cmd", "Publish.cmd"):
            self.assertIn(shipped, publish_release.PLUGIN_INCLUDE)
            if (ROOT / shipped).is_file():
                self.assertIn(shipped, relative)
        for forbidden in ("tests/", "design/", ".runtime/", "release/", "releases/", ".git/"):
            self.assertEqual([name for name in relative if name.startswith(forbidden)], [], forbidden)
        self.assertEqual([name for name in relative if "__pycache__" in name or name.endswith(".pyc")], [])
        for name in relative:
            self.assertNotIn(Path(name).name, publish_release.SECRET_NAMES, name)
        self.assertEqual(relative["deploy/ubuntu/install.sh"].external_attr >> 16, 0o100755)
        self.assertEqual(relative["scripts/studio_bridge.py"].external_attr >> 16, 0o100644)
        self.assertEqual({info.create_system for info in infos}, {3})

    def test_text_files_have_no_carriage_returns_and_binaries_are_untouched(self):
        top = f"roblox-studio-harness-{self.version}/"
        with zipfile.ZipFile(self.plugin_zip) as bundle:
            checked = 0
            for info in bundle.infolist():
                if Path(info.filename).suffix.lower() in publish_release.TEXT_SUFFIXES:
                    self.assertNotIn(b"\r", bundle.read(info), info.filename)
                    checked += 1
            self.assertGreater(checked, 10)
            self.assertEqual(bundle.read(top + "dist/StudioHarness.rbxmx"), (ROOT / "dist" / "StudioHarness.rbxmx").read_bytes())
            self.assertEqual(bundle.read(top + "scripts/updater.py"), (ROOT / "scripts" / "updater.py").read_bytes().replace(b"\r\n", b"\n"))

    def test_hub_bundle_contains_only_the_hosting_files(self):
        top = f"studio-harness-hub-{self.version}/"
        infos = self.members(self.hub_zip)
        relative = {info.filename[len(top):]: info for info in infos}
        self.assertTrue(all(info.filename.startswith(top) for info in infos))
        expected = sorted(key for key, source in publish_release.HUB_FILES.items() if (ROOT / source).is_file())
        self.assertEqual(sorted(relative), expected)
        for required in ("scripts/team_hub.py", "scripts/claims.py", "scripts/local_state.py", "scripts/updater.py", "scripts/project_map.py",
                         "update-channel.json", "deploy/install.sh", "deploy/studio-harness-hub.service", "README.md"):
            self.assertIn(required, relative)
        self.assertNotIn("scripts/studio_bridge.py", relative)
        self.assertEqual(relative["deploy/install.sh"].external_attr >> 16, 0o100755)
        with zipfile.ZipFile(self.hub_zip) as bundle:
            self.assertEqual(bundle.read(top + "README.md"), (ROOT / "deploy" / "ubuntu" / "README.md").read_bytes().replace(b"\r\n", b"\n"))
        # 0.7: panoul web este declarat în ambele pachete; în repo intră în zip doar dacă fișierul există (alt agent îl livrează).
        self.assertEqual(publish_release.HUB_FILES["panel/index.html"], "panel/index.html")
        self.assertIn("panel", publish_release.PLUGIN_INCLUDE)
        self.assertIn(".html", publish_release.TEXT_SUFFIXES)
        if (ROOT / "panel" / "index.html").is_file():
            self.assertIn("panel/index.html", relative)
            self.assertIn(f"roblox-studio-harness-{self.version}/panel/index.html", [info.filename for info in self.members(self.plugin_zip)])

    def test_hub_bundle_downloads_and_applies_flat_into_the_app_dir_like_the_hosted_hub(self):
        # Ca în team_hub.main: pachetul `hub` din manifest este descărcat, verificat și aplicat cu flatten="scripts/".
        entry = dict(self.manifest["files"]["hub"])
        with LocalChannel() as channel, tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP")) as temp:
            entry["url"] = channel.serve("/" + self.hub_zip.name, self.hub_zip.read_bytes())
            data = updater.download(entry, timeout=5)
            self.assertEqual(data, self.hub_zip.read_bytes())
            app = Path(temp) / "app"
            app.mkdir()
            (app / "team_hub.py").write_bytes(b"# hub vechi\n")
            result = updater.apply_bundle(data, app, Path(temp) / "state" / "backups", flatten="scripts/")
            self.assertEqual((app / "team_hub.py").read_bytes(), (ROOT / "scripts" / "team_hub.py").read_bytes().replace(b"\r\n", b"\n"))
            for name in ("claims.py", "local_state.py", "project_map.py", "updater.py", "update-channel.json", "README.md", "deploy/install.sh"):
                self.assertTrue((app / name).is_file(), name)
            self.assertFalse((app / "scripts").exists())
            self.assertIn("team_hub.py", result["written"])
            self.assertEqual((Path(result["backup"]) / "team_hub.py").read_bytes(), b"# hub vechi\n")


class FakeRepo:
    """Repo minimal cu versiunile aliniate, într-un director temporar."""

    def __init__(self, base, version="0.7.0", bridge_version=None, hub_version=None):
        self.root = Path(base) / "repo"
        write(self.root, ".claude-plugin/plugin.json", json.dumps({"name": "roblox-studio-harness", "version": version}))
        write(self.root, "scripts/studio_bridge.py", 'VERSION = "' + (bridge_version or version) + '"\n')
        write(self.root, "scripts/team_hub.py", 'VERSION = "' + (hub_version or version) + '"\n')
        for name in ("updater.py", "claims.py", "local_state.py", "project_map.py"):
            write(self.root, "scripts/" + name, "# " + name + "\n")
        write(self.root, "update-channel.json", json.dumps({"manifest_url": "https://huggingface.co/spaces/OWNER/studio-harness/resolve/main/releases/manifest.json", "auto": True}, indent=1) + "\n")
        write(self.root, "README.md", '# Doc\r\n{"team_token": "..."}\r\n')
        write(self.root, "deploy/ubuntu/install.sh", "#!/usr/bin/env bash\necho ok\n")
        write(self.root, "deploy/ubuntu/README.md", "# Hub\n")
        write(self.root, "panel/index.html", "<!doctype html>\r\n<title>Panou</title>\r\n")
        write(self.root, "studio-plugin/StudioHarness.server.luau", "-- Studio Harness Loader 1.1.0\nlocal token = plugin\nreturn token\n")
        write(self.root, "studio-plugin/modules/Main.luau", "-- Studio Harness App " + version + "\nreturn {}\n")
        write(self.root, "dist/StudioHarness.rbxmx", "<roblox/>")
        write(self.root, "tests/test_local.py", "# testele nu intră în pachete, dar sunt scanate\n")
        write(self.root, ".runtime/team.json", json.dumps({"team_token": REAL_TEAM_TOKEN}) + "\n")
        # 1.0: lansatoarele livrate (Start-Hub.cmd, Publish.cmd) și cele eliminate, rămase într-un checkout vechi.
        write(self.root, "Start-Hub.cmd", "@echo off\r\npython -u scripts\\team_hub.py\r\n")
        write(self.root, "Publish.cmd", "@echo off\r\npython scripts\\publish_release.py --github --bump patch\r\n")
        write(self.root, "Setup-Team.cmd", "@echo off\r\nrem eliminat in 1.0\r\n")
        write(self.root, "Start-Team-Hub.cmd", "@echo off\r\nrem eliminat in 1.0\r\n")
        write(self.root, "scripts/setup-team.ps1", "# eliminat in 1.0\r\n")

    def channel(self):
        return (self.root / "update-channel.json").read_bytes()


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.repo = FakeRepo(self.temp.name)
        self.use_repo(self.repo)
        self.real_channel = (ROOT / "update-channel.json").read_bytes()
        self.uploads = []

    def tearDown(self):
        self.assertEqual((ROOT / "update-channel.json").read_bytes(), self.real_channel)
        self.temp.cleanup()

    def use_repo(self, repo):
        patcher = patch.object(publish_release, "ROOT", repo.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *argv, upload=None):
        def fake_upload(repo, folder):
            self.uploads.append((repo, folder))
            return "https://huggingface.co/spaces/" + repo

        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["publish_release.py", *argv]), patch.object(publish_release, "upload", upload or fake_upload), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = publish_release.main()
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

    def test_set_channel_keeps_the_public_channel_and_writes_the_hf_upstream(self):
        # 0.8: developerii și hub-ul găzduit folosesc canalul public (lostcube.pro); Hugging Face rămâne upstream-ul din care hub-ul oglindește.
        self.assertEqual(PUBLIC_CHANNEL, "https://lostcube.pro/releases/manifest.json")
        self.assertEqual(json.loads((ROOT / "update-channel.json").read_text(encoding="utf-8"))["manifest_url"], PUBLIC_CHANNEL)
        # Placeholder OWNER în fișier: canalul public devine cel implicit, upstream-ul este repo-ul HF.
        self.assertIn(b"OWNER", self.repo.channel())
        url = publish_release.set_channel("ana/studio-harness")
        self.assertEqual(url, BASE_URL)
        raw = self.repo.channel()
        self.assertNotIn(b"\r", raw)
        expected = {"manifest_url": PUBLIC_CHANNEL, "upstream_manifest_url": BASE_URL + "manifest.json", "auto": True}
        self.assertEqual(json.loads(raw.decode("utf-8")), expected)
        self.assertEqual(updater.channel(self.repo.root, {}), expected)
        # Un canal public deja configurat (alt domeniu) și `auto` dezactivat se păstrează; doar upstream-ul se rescrie.
        write(self.repo.root, "update-channel.json", json.dumps({"manifest_url": "https://hub.example.com/releases/manifest.json",
                                                                 "upstream_manifest_url": "https://vechi.example.com/manifest.json", "auto": False}))
        self.assertEqual(publish_release.set_channel("dan/alt-repo"), "https://huggingface.co/spaces/dan/alt-repo/resolve/main/releases/")
        self.assertEqual(json.loads(self.repo.channel().decode("utf-8")),
                         {"manifest_url": "https://hub.example.com/releases/manifest.json",
                          "upstream_manifest_url": "https://huggingface.co/spaces/dan/alt-repo/resolve/main/releases/manifest.json", "auto": False})
        # Fișier lipsă, stricat sau cu manifest_url care nu este text: canalul public implicit, auto activ.
        for content in (None, "{", "[1]", "null", json.dumps({"manifest_url": 5}), json.dumps({"upstream_manifest_url": BASE_URL + "manifest.json"})):
            with self.subTest(content=content):
                (self.repo.root / "update-channel.json").unlink()
                if content is not None:
                    write(self.repo.root, "update-channel.json", content)
                publish_release.set_channel("ana/studio-harness")
                self.assertEqual(json.loads(self.repo.channel().decode("utf-8")), expected)
        # Doar `true` literal păstrează auto.
        write(self.repo.root, "update-channel.json", json.dumps({"manifest_url": PUBLIC_CHANNEL, "auto": "da"}))
        publish_release.set_channel("ana/studio-harness")
        self.assertEqual(json.loads(self.repo.channel().decode("utf-8")), {**expected, "auto": False})

    def test_dry_run_builds_and_scans_without_touching_the_channel_or_uploading(self):
        placeholder = self.repo.channel()
        self.assertIn(b"OWNER", placeholder)
        imported = "huggingface_hub" in sys.modules
        code, out, err = self.run_main("--repo", "ana/studio-harness", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.uploads, [])
        self.assertEqual("huggingface_hub" in sys.modules, imported)
        self.assertEqual(self.repo.channel(), placeholder)
        self.assertNotIn(b"upstream_manifest_url", self.repo.channel())
        self.assertIn("Versiune: 0.7.0", out)
        self.assertIn("Scanare secrete: nimic găsit.", out)
        self.assertIn("Dry-run: fără git push și fără upload.", out)
        manifest = json.loads((self.repo.root / "releases" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.7.0")
        self.assertEqual({kind: entry["url"] for kind, entry in manifest["files"].items()},
                         {"plugin": BASE_URL + "roblox-studio-harness-0.7.0.zip", "hub": BASE_URL + "studio-harness-hub-0.7.0-ubuntu.zip",
                          "studio_app": BASE_URL + "studio-harness-app-0.7.0.zip"})
        # Pachetul Studio se reconstruiește din surse, cu placeholder-ul LocalToken (tokenul se injectează abia la instalare).
        self.assertIn("Plugin Studio reconstruit: dist/StudioHarness.rbxmx (loader 1.1.0)", out)
        self.assertIn(updater.LOCAL_TOKEN_PLACEHOLDER, (self.repo.root / "dist" / "StudioHarness.rbxmx").read_text(encoding="utf-8"))
        with zipfile.ZipFile(self.repo.root / "releases" / "roblox-studio-harness-0.7.0.zip") as bundle:
            names = bundle.namelist()
            self.assertIn("roblox-studio-harness-0.7.0/README.md", names)
            self.assertEqual([name for name in names if "/tests/" in name or "/.runtime/" in name or "/releases/" in name], [])
            self.assertEqual(bundle.read("roblox-studio-harness-0.7.0/README.md"), b'# Doc\n{"team_token": "..."}\n')
            # 1.0: lansatoarele fără echipă intră în pachet, cele eliminate nu (chiar dacă au rămas în checkout).
            self.assertIn("roblox-studio-harness-0.7.0/Start-Hub.cmd", names)
            self.assertIn("roblox-studio-harness-0.7.0/Publish.cmd", names)
            for removed in ("Setup-Team.cmd", "Start-Team-Hub.cmd", "scripts/setup-team.ps1"):
                self.assertNotIn("roblox-studio-harness-0.7.0/" + removed, names)
            self.assertEqual(bundle.read("roblox-studio-harness-0.7.0/Start-Hub.cmd"), b"@echo off\npython -u scripts\\team_hub.py\n")
            # 0.7: panoul web merge în ambele pachete, cu liniile normalizate ca orice fișier text.
            self.assertEqual(bundle.read("roblox-studio-harness-0.7.0/panel/index.html"), b"<!doctype html>\n<title>Panou</title>\n")
        with zipfile.ZipFile(self.repo.root / "releases" / "studio-harness-hub-0.7.0-ubuntu.zip") as bundle:
            self.assertEqual(bundle.read("studio-harness-hub-0.7.0/panel/index.html"), b"<!doctype html>\n<title>Panou</title>\n")
            self.assertEqual(bundle.getinfo("studio-harness-hub-0.7.0/panel/index.html").external_attr >> 16, 0o100644)

    def test_publish_sets_the_channel_then_uploads_the_repo(self):
        code, out, err = self.run_main("--repo", "ana/studio-harness")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.uploads, [("ana/studio-harness", self.repo.root)])
        self.assertEqual(json.loads(self.repo.channel().decode("utf-8")),
                         {"manifest_url": PUBLIC_CHANNEL, "upstream_manifest_url": BASE_URL + "manifest.json", "auto": True})
        # Pachetele publicate arată spre Hugging Face (upstream); hub-ul le oglindește la lostcube.pro când se actualizează.
        manifest = json.loads((self.repo.root / "releases" / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(all(entry["url"].startswith(BASE_URL) for entry in manifest["files"].values()), manifest)
        self.assertIn("Publicat: https://huggingface.co/spaces/ana/studio-harness", out)
        # 1.0: codul de admin este Secretul Space-ului; developerii indică hub-ul prin config.json sau STUDIO_HARNESS_HUB_URL.
        self.assertIn('{"hub_url": "https://ana-studio-harness.hf.space"}', out)
        self.assertIn("STUDIO_HARNESS_ADMIN_TOKEN", out)
        self.assertIn("STUDIO_HARNESS_HUB_URL", out)
        self.assertNotIn("TEAM_TOKEN", out)
        self.assertNotIn("team.json", out)

    def test_secrets_stop_the_publish_before_upload(self):
        write(self.repo.root, ".env", "HF_TOKEN=" + HF_TOKEN + "\n")
        write(self.repo.root, "scripts/keys.py", 'KEY = "' + ANTHROPIC_KEY + '"\n')
        code, out, err = self.run_main("--repo", "ana/studio-harness", upload=lambda *_: self.fail("upload nu trebuie apelat"))
        self.assertEqual(code, 2)
        self.assertIn("Publicare oprită; posibile secrete:", out)
        self.assertIn("  - .env: fișier care nu se publică", out)
        self.assertIn("  - scripts/keys.py: cheie Anthropic", out)
        self.assertNotIn(HF_TOKEN, out)
        self.assertNotIn(ANTHROPIC_KEY, out)
        self.assertEqual(self.uploads, [])

    def test_a_prebuilt_package_is_verified_when_the_loader_source_is_missing(self):
        """Fără sursa loader-ului nu se reconstruiește nimic: pachetul din dist/ se publică doar dacă are placeholder-ul."""
        package = build_plugin(self.repo.root)
        shutil.rmtree(self.repo.root / "studio-plugin")
        code, out, err = self.run_main("--repo", "ana/studio-harness", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("Plugin Studio: dist/StudioHarness.rbxmx păstrat ca atare", out)
        package.write_bytes(package.read_bytes().replace(updater.LOCAL_TOKEN_PLACEHOLDER.encode(), b"cod-local-0123456789"))
        code, out, err = self.run_main("--repo", "ana/studio-harness", "--dry-run")
        self.assertIn("token local injectat", str(code))
        self.assertEqual(self.uploads, [])
        package.write_bytes(b"<roblox>nu e pachetul nostru</roblox>")
        code, out, err = self.run_main("--repo", "ana/studio-harness", "--dry-run")
        self.assertIn("nu poate fi verificat", str(code))

    def test_invalid_repo_is_rejected_before_building(self):
        for repo in ("ana", "ana/studio/harness", "", "ana/", "/studio-harness", "ana studio/harness", "ana/studio harness"):
            with self.subTest(repo=repo):
                code, out, err = self.run_main("--repo", repo, "--dry-run")
                self.assertEqual(code, 2)
                self.assertIn("--repo trebuie să fie OWNER/nume.", err)
        # 0.8: fără --github și fără --repo publicarea nu are unde să meargă (dry-run singur rămâne permis).
        code, out, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("--github OWNER/REPO", err)
        self.assertFalse((self.repo.root / "releases").exists())
        self.assertEqual(self.uploads, [])

    def test_misaligned_script_versions_stop_the_publish(self):
        for kind in ("bridge_version", "hub_version"):
            with self.subTest(kind=kind):
                repo = FakeRepo(Path(self.temp.name) / kind, **{kind: "0.5.0"})
                self.use_repo(repo)
                code, out, err = self.run_main("--repo", "ana/studio-harness", "--dry-run")
                self.assertIn("aliniază versiunile", str(code))
                self.assertIn("0.7.0", str(code))
                self.assertFalse((repo.root / "releases").exists())
                self.assertIn(b"OWNER", repo.channel())
        self.assertEqual(self.uploads, [])


if __name__ == "__main__":
    unittest.main()
