"""Publicare 0.8/1.0: GitHub ca sursă a canalului (raw.githubusercontent.com), git prin subprocess fără shell (înlocuit în teste),
`--bump patch|minor` în plugin.json/studio_bridge.py/team_hub.py/antetul Luau al aplicației, `Publish.cmd`, manifestul cu
`loader_version`, `bundle_revision` și `files.studio_app`, reconstruirea .rbxmx-ului (cu placeholder-ul LocalToken) înainte de
build, verificarea versiunilor (antetul `App` aliniat, loader ≥ 1.1.0).

Fără rețea și fără git real: `subprocess.run` și `shutil.which` sunt înlocuite; repo-ul este unul fals dintr-un director temporar."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_studio_plugin  # noqa: E402
import publish_release  # noqa: E402
import updater  # noqa: E402
from test_publish_release import FakeRepo, write  # noqa: E402

RAW = "https://raw.githubusercontent.com/ana/harness/main/releases/"
HF = "https://huggingface.co/spaces/ana/studio-harness/resolve/main/releases/"
LOADER = "-- Studio Harness Loader 1.1.0. Loader de test.\r\nlocal app = require(script.Parent.Modules.Main).start(plugin)\r\n"
# Loader 0.8: nu citește LocalToken; 1.0 refuză publicarea cu el (publish_release.MIN_LOADER_VERSION).
OLD_LOADER = LOADER.replace("Loader 1.1.0", "Loader 1.0.0")
MAIN = "\ufeff-- Studio Harness App 0.7.0. Aplicația.\nreturn {start = function() end}\n"


class LuauRepo(FakeRepo):
    """Repo fals cu sursele pluginului 0.8: loader versionat + modules/Main.luau (+ Theme), ca build-ul .rbxmx să fie posibil."""

    def __init__(self, base, loader=LOADER, main=MAIN, **kwargs):
        super().__init__(base, **kwargs)
        # `FakeRepo` scrie deja un loader și un `Main.luau`; aici `None` înseamnă „sursa lipsește din checkout”, deci le ștergem întâi.
        shutil.rmtree(self.root / "studio-plugin" / "modules", ignore_errors=True)
        (self.root / "studio-plugin" / "StudioHarness.server.luau").unlink(missing_ok=True)
        if loader is not None:
            write(self.root, "studio-plugin/StudioHarness.server.luau", loader)
        if main is not None:
            write(self.root, "studio-plugin/modules/Main.luau", main)
            write(self.root, "studio-plugin/modules/Theme.luau", "return {accent = 'verde'}\r\n")

    def read(self, relative):
        return (self.root / relative).read_bytes()


class FakeGit:
    """Înregistrează comenzile git; poate eșua la o subcomandă dată."""

    def __init__(self, fail=None, output=""):
        self.calls = []
        self.fail = fail
        self.output = output

    def __call__(self, arguments, **kwargs):
        self.calls.append((list(arguments), kwargs))
        self.assert_safe(kwargs)
        if self.fail == arguments[1]:
            return subprocess.CompletedProcess(arguments, 1, stdout=self.output, stderr="eroare simulată")
        return subprocess.CompletedProcess(arguments, 0, stdout="ok", stderr="")

    @staticmethod
    def assert_safe(kwargs):
        assert not kwargs.get("shell"), "git trebuie rulat fără shell"
        assert kwargs.get("capture_output") is True

    def commands(self):
        return [call[0][1:] for call in self.calls]


class PublishBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        self.repo = LuauRepo(self.temp.name)
        self.use_repo(self.repo)
        self.real_channel = (ROOT / "update-channel.json").read_bytes()
        self.git = FakeGit()
        self.uploads = []

    def tearDown(self):
        self.assertEqual((ROOT / "update-channel.json").read_bytes(), self.real_channel)
        self.temp.cleanup()

    def use_repo(self, repo):
        patcher = patch.object(publish_release, "ROOT", repo.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *argv, git=None, which="C:\\Git\\git.exe"):
        def fake_upload(repo, folder):
            self.uploads.append((repo, folder))
            return "https://huggingface.co/spaces/" + repo

        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["publish_release.py", *argv]), patch.object(publish_release, "upload", fake_upload), \
                patch.object(publish_release.subprocess, "run", git or self.git), patch.object(publish_release.shutil, "which", lambda name: which), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = publish_release.main()
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

    def channel(self):
        return json.loads(self.repo.channel().decode("utf-8"))

    def manifest(self):
        return json.loads((self.repo.root / "releases" / "manifest.json").read_text(encoding="utf-8"))


class BumpTests(PublishBase):
    def test_next_version(self):
        self.assertEqual(publish_release.next_version("0.7.0", "patch"), "0.7.1")
        self.assertEqual(publish_release.next_version("0.7.9", "patch"), "0.7.10")
        self.assertEqual(publish_release.next_version("0.7.3", "minor"), "0.8.0")
        self.assertEqual(publish_release.next_version("1.2", "minor"), "1.3.0")
        with self.assertRaises(ValueError):
            publish_release.next_version("0.7.0", "major")
        with self.assertRaises(updater.UpdateError):
            publish_release.next_version("x", "patch")

    def test_bump_patch_updates_every_version_holder_and_keeps_bytes_otherwise(self):
        write(self.repo.root, ".claude-plugin/plugin.json", '{\n  "name": "roblox-studio-harness",\n  "version": "0.7.0",\n  "description": "v 0.7.0"\n}\n')
        write(self.repo.root, "scripts/studio_bridge.py", 'VERSION = "0.7.0"\nX = 1\nclass H:\n    server_version = "StudioHarness/0.7"\n')
        write(self.repo.root, "scripts/team_hub.py", 'VERSION = "0.7.0"\r\n    server_version = "StudioHarnessHub/0.7"\r\n')
        self.assertEqual(publish_release.bump_version("patch"), ("0.7.0", "0.7.1"))
        self.assertEqual(self.repo.read(".claude-plugin/plugin.json"), b'{\n  "name": "roblox-studio-harness",\n  "version": "0.7.1",\n  "description": "v 0.7.0"\n}\n')
        self.assertEqual(self.repo.read("scripts/studio_bridge.py"), b'VERSION = "0.7.1"\nX = 1\nclass H:\n    server_version = "StudioHarness/0.7"\n')
        self.assertEqual(self.repo.read("scripts/team_hub.py"), b'VERSION = "0.7.1"\r\n    server_version = "StudioHarnessHub/0.7"\r\n')
        # Antetul aplicației (App, în Main.luau) se actualizează cu BOM-ul păstrat; antetul Loader are versiunea lui și rămâne neschimbat (CRLF păstrat).
        self.assertEqual(self.repo.read("studio-plugin/modules/Main.luau"), "\ufeff-- Studio Harness App 0.7.1. Aplicația.\nreturn {start = function() end}\n".encode("utf-8"))
        self.assertEqual(self.repo.read("studio-plugin/StudioHarness.server.luau"), LOADER.encode("utf-8"))
        self.assertEqual(updater.current_version(self.repo.root), "0.7.1")
        # minor: 0.7.1 → 0.8.0, inclusiv server_version major.minor.
        self.assertEqual(publish_release.bump_version("minor"), ("0.7.1", "0.8.0"))
        self.assertIn(b'server_version = "StudioHarness/0.8"', self.repo.read("scripts/studio_bridge.py"))
        self.assertIn(b'server_version = "StudioHarnessHub/0.8"', self.repo.read("scripts/team_hub.py"))
        self.assertIn("App 0.8.0.", self.repo.read("studio-plugin/modules/Main.luau").decode("utf-8-sig"))

    def test_bump_updates_a_hub_header_in_the_loader_and_tolerates_missing_main(self):
        repo = LuauRepo(Path(self.temp.name) / "hub", loader="-- Studio Harness Hub 0.7.0. UI nativ.\nlocal x = plugin\n", main=None)
        self.use_repo(repo)
        self.assertEqual(publish_release.bump_version("patch"), ("0.7.0", "0.7.1"))
        self.assertEqual(repo.read("studio-plugin/StudioHarness.server.luau"), b"-- Studio Harness Hub 0.7.1. UI nativ.\nlocal x = plugin\n")
        self.assertFalse((repo.root / "studio-plugin" / "modules").exists())

    def test_bump_refuses_misaligned_scripts_before_touching_anything(self):
        repo = LuauRepo(Path(self.temp.name) / "mis", hub_version="0.5.0")
        self.use_repo(repo)
        with self.assertRaisesRegex(SystemExit, "team_hub.py"):
            publish_release.bump_version("patch")
        # plugin.json și studio_bridge.py au fost deja rescrise (ordinea: plugin.json, bridge, hub), hub-ul nealiniat oprește totul.
        self.assertEqual(updater.current_version(repo.root), "0.7.1")


class ChannelTests(PublishBase):
    def test_set_channel_github_writes_raw_upstream_and_remembers_the_repo(self):
        upstream = publish_release.set_channel(None, "ana/harness", "main")
        self.assertEqual(upstream, RAW)
        self.assertEqual(self.channel(), {"manifest_url": publish_release.PUBLIC_CHANNEL, "upstream_manifest_url": RAW + "manifest.json", "auto": True,
                                          "github_repo": "ana/harness", "github_branch": "main"})
        self.assertEqual(updater.channel(self.repo.root, {})["upstream_manifest_url"], RAW + "manifest.json")
        # Hugging Face singur păstrează repo-ul GitHub memorat; GitHub cu alt branch îl rescrie; ambele: upstream-ul este GitHub.
        self.assertEqual(publish_release.set_channel("ana/studio-harness"), HF)
        self.assertEqual((self.channel()["upstream_manifest_url"], self.channel()["github_repo"], self.channel()["github_branch"]),
                         (HF + "manifest.json", "ana/harness", "main"))
        publish_release.set_channel("ana/studio-harness", "dan/alt", "dev")
        self.assertEqual((self.channel()["upstream_manifest_url"], self.channel()["github_repo"], self.channel()["github_branch"]),
                         ("https://raw.githubusercontent.com/dan/alt/dev/releases/manifest.json", "dan/alt", "dev"))
        with self.assertRaises(ValueError):
            publish_release.set_channel()
        self.assertEqual(publish_release.read_channel(self.repo.root)["github_repo"], "dan/alt")
        (self.repo.root / "update-channel.json").write_text("{", encoding="utf-8")
        self.assertEqual(publish_release.read_channel(self.repo.root), {})


class GithubPublishTests(PublishBase):
    def test_github_publish_builds_raw_urls_rebuilds_the_plugin_then_adds_commits_and_pushes(self):
        (self.repo.root / ".git").mkdir()
        code, out, err = self.run_main("--github", "ana/harness")
        self.assertEqual(code, 0, err)
        git = "C:\\Git\\git.exe"
        self.assertEqual([call[0] for call in self.git.calls], [[git, "add", "-A"], [git, "commit", "-m", "Studio Harness 0.7.0"], [git, "push", "origin", "main"]])
        self.assertTrue(all(call[1]["cwd"] == str(self.repo.root) and "shell" not in call[1] for call in self.git.calls))
        self.assertEqual(self.uploads, [])
        manifest = self.manifest()
        self.assertEqual(set(manifest["files"]), {"plugin", "hub", "studio_app"})
        self.assertTrue(all(entry["url"].startswith(RAW) for entry in manifest["files"].values()), manifest)
        self.assertEqual((manifest["loader_version"], manifest["bundle_revision"]), ("1.1.0", updater.bundle_revision(updater.read_luau_modules(self.repo.root / "studio-plugin" / "modules"))))
        self.assertEqual(self.channel(), {"manifest_url": publish_release.PUBLIC_CHANNEL, "upstream_manifest_url": RAW + "manifest.json", "auto": True,
                                          "github_repo": "ana/harness", "github_branch": "main"})
        # .rbxmx-ul din dist a fost reconstruit din surse (loader + Modules cu Main), nu mai este fișierul fals.
        rbxmx = self.repo.read("dist/StudioHarness.rbxmx")
        self.assertIn(b"Studio Harness Loader 1.1.0", rbxmx)
        self.assertIn(b'<string name="Name">Modules</string>', rbxmx)
        # 1.0: pachetul conține doar placeholder-ul LocalToken (tokenul se injectează la instalare, nu la publicare).
        self.assertIn(b'<string name="Name">LocalToken</string>', rbxmx)
        self.assertIn(updater.LOCAL_TOKEN_PLACEHOLDER.encode("utf-8"), rbxmx)
        self.assertEqual(build_studio_plugin.verify(self.repo.root / "dist" / "StudioHarness.rbxmx")["local_token"], "placeholder")
        with zipfile.ZipFile(self.repo.root / "releases" / "roblox-studio-harness-0.7.0.zip") as bundle:
            self.assertEqual(bundle.read("roblox-studio-harness-0.7.0/dist/StudioHarness.rbxmx"), rbxmx)
        with zipfile.ZipFile(self.repo.root / "releases" / "studio-harness-app-0.7.0.zip") as bundle:
            self.assertEqual(sorted(bundle.namelist()), ["studio-harness-app-0.7.0/Main.luau", "studio-harness-app-0.7.0/Theme.luau"])
            self.assertEqual(bundle.read("studio-harness-app-0.7.0/Theme.luau"), b"return {accent = 'verde'}\n")
            self.assertEqual(updater.extract_luau_modules((self.repo.root / "releases" / "studio-harness-app-0.7.0.zip").read_bytes()),
                             updater.read_luau_modules(self.repo.root / "studio-plugin" / "modules"))
        self.assertTrue((self.repo.root / "releases" / "studio-harness-app-0.7.0.zip.sha256").is_file())
        self.assertIn("Plugin Studio reconstruit: dist/StudioHarness.rbxmx (loader 1.1.0)", out)
        self.assertIn("Loader: 1.1.0; revizie module " + manifest["bundle_revision"], out)
        self.assertIn("Publicat pe GitHub: https://github.com/ana/harness (main)", out)
        self.assertIn("Manifest upstream: " + RAW + "manifest.json", out)

    def test_without_a_git_checkout_the_publish_explains_git_init_and_returns_3(self):
        code, out, err = self.run_main("--github", "ana/harness")
        self.assertEqual(code, 3)
        self.assertEqual(self.git.calls, [])
        self.assertIn("git init -b main", out)
        self.assertIn("git remote add origin", out)
        self.assertIn("Scanare secrete: nimic găsit.", out)
        # Pachetele și canalul sunt gata: după `git init` publicarea următoare le trimite.
        self.assertTrue((self.repo.root / "releases" / "manifest.json").is_file())
        self.assertEqual(self.channel()["github_repo"], "ana/harness")

    def test_nothing_to_commit_still_pushes_and_git_failures_stop_with_a_message(self):
        (self.repo.root / ".git").mkdir()
        git = FakeGit(fail="commit", output="nothing to commit, working tree clean")
        code, out, err = self.run_main("--github", "ana/harness", git=git)
        self.assertEqual(code, 0, err)
        self.assertEqual(git.commands(), [["add", "-A"], ["commit", "-m", "Studio Harness 0.7.0"], ["push", "origin", "main"]])
        self.assertIn("Nimic nou de comis", out)
        for step in ("add", "commit", "push"):
            with self.subTest(step=step):
                git = FakeGit(fail=step)
                code, out, err = self.run_main("--github", "ana/harness", git=git)
                self.assertIn("git " + step, str(code))
                self.assertIn("eroare simulată", str(code))
                self.assertEqual(len(git.commands()), {"add": 1, "commit": 2, "push": 3}[step])
        code, out, err = self.run_main("--github", "ana/harness", which=None)
        self.assertIn("git nu este instalat", str(code))

    def test_github_without_a_value_uses_the_channel_and_explains_when_it_is_missing(self):
        code, out, err = self.run_main("--github")
        self.assertIn("github_repo lipsește din update-channel.json", str(code))
        self.assertIn("--github OWNER/REPO --bump patch", str(code))
        self.assertFalse((self.repo.root / "releases").exists())
        write(self.repo.root, "update-channel.json", json.dumps({"manifest_url": publish_release.PUBLIC_CHANNEL, "auto": True, "github_repo": "ana/harness", "github_branch": "dev"}))
        (self.repo.root / ".git").mkdir()
        code, out, err = self.run_main("--github")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git.commands()[-1], ["push", "origin", "dev"])
        self.assertTrue(all(entry["url"].startswith("https://raw.githubusercontent.com/ana/harness/dev/releases/") for entry in self.manifest()["files"].values()))
        # --branch explicit câștigă în fața celui din canal.
        code, out, err = self.run_main("--github", "--branch", "release")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git.commands()[-1], ["push", "origin", "release"])
        self.assertEqual(self.channel()["github_branch"], "release")

    def test_bump_patch_with_dry_run_updates_versions_and_builds_without_git_or_channel_changes(self):
        placeholder = self.repo.channel()
        code, out, err = self.run_main("--github", "ana/harness", "--bump", "patch", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git.calls, [])
        self.assertEqual(self.repo.channel(), placeholder)
        self.assertIn("Versiune: 0.7.0 → 0.7.1", out)
        self.assertIn("Dry-run: fără git push și fără upload.", out)
        self.assertEqual(updater.current_version(self.repo.root), "0.7.1")
        self.assertIn(b'VERSION = "0.7.1"', self.repo.read("scripts/studio_bridge.py"))
        self.assertIn(b'VERSION = "0.7.1"', self.repo.read("scripts/team_hub.py"))
        manifest = self.manifest()
        self.assertEqual((manifest["version"], manifest["loader_version"]), ("0.7.1", "1.1.0"))
        self.assertEqual(manifest["files"]["plugin"]["url"], RAW + "roblox-studio-harness-0.7.1.zip")
        self.assertIn("App 0.7.1.", self.repo.read("studio-plugin/modules/Main.luau").decode("utf-8-sig"))
        with zipfile.ZipFile(self.repo.root / "releases" / "studio-harness-app-0.7.1.zip") as bundle:
            self.assertIn(b"App 0.7.1.", bundle.read("studio-harness-app-0.7.1/Main.luau"))
        # Un dry-run fără țintă folosește upstream-ul din canal (sau canalul public) doar pentru URL-uri.
        code, out, err = self.run_main("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.manifest()["files"]["plugin"]["url"].startswith("https://huggingface.co/spaces/OWNER/"))

    def test_github_and_hugging_face_together_use_github_as_upstream(self):
        (self.repo.root / ".git").mkdir()
        code, out, err = self.run_main("--github", "ana/harness", "--repo", "ana/studio-harness")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.git.commands()), 3)
        self.assertEqual(self.uploads, [("ana/studio-harness", self.repo.root)])
        self.assertEqual(self.channel()["upstream_manifest_url"], RAW + "manifest.json")
        self.assertTrue(all(entry["url"].startswith(RAW) for entry in self.manifest()["files"].values()))
        self.assertIn("Publicat pe GitHub", out)
        self.assertIn("Publicat: https://huggingface.co/spaces/ana/studio-harness", out)

    def test_invalid_github_repo_or_branch_is_rejected_before_building(self):
        for argv in (("--github", "ana"), ("--github", "ana/x/y"), ("--github", "ana/harness", "--branch", "-bad"),
                     ("--github", "ana/harness", "--branch", "a..b"), ("--github", "ana/harness", "--branch", "")):
            with self.subTest(argv=argv):
                code, out, err = self.run_main(*argv, "--dry-run")
                self.assertEqual(code, 2)
                self.assertIn("--github" if argv[1] in ("ana", "ana/x/y") else "--branch", err)
        self.assertFalse((self.repo.root / "releases").exists())
        self.assertEqual(self.git.calls, [])

    def test_a_broken_loader_stops_the_publish_with_the_builder_message(self):
        (self.repo.root / "studio-plugin" / "modules" / "Main.luau").unlink()
        code, out, err = self.run_main("--github", "ana/harness", "--dry-run")
        self.assertIn("Pluginul Studio nu a putut fi construit", str(code))
        self.assertIn("Main.luau", str(code))
        self.assertFalse((self.repo.root / "releases").exists())
        self.assertEqual(self.repo.read("dist/StudioHarness.rbxmx"), b"<roblox/>")

    def test_an_old_loader_or_a_misaligned_app_header_stops_the_publish_before_building(self):
        # 1.0: loader-ul trebuie să fie cel puțin 1.1.0 (citește LocalToken); antetul `App` din Main.luau trebuie să fie versiunea publicată.
        self.assertEqual(publish_release.MIN_LOADER_VERSION, "1.1.0")
        write(self.repo.root, "studio-plugin/StudioHarness.server.luau", OLD_LOADER)
        code, out, err = self.run_main("--github", "ana/harness", "--dry-run")
        self.assertIn("loader-ul 1.0.0", str(code))
        self.assertIn("cel puțin 1.1.0", str(code))
        self.assertFalse((self.repo.root / "releases").exists())
        self.assertEqual(self.repo.read("dist/StudioHarness.rbxmx"), b"<roblox/>")
        write(self.repo.root, "studio-plugin/StudioHarness.server.luau", LOADER)
        write(self.repo.root, "studio-plugin/modules/Main.luau", MAIN.replace("App 0.7.0", "App 0.6.9"))
        code, out, err = self.run_main("--github", "ana/harness", "--dry-run")
        self.assertIn("Main.luau", str(code))
        self.assertIn("App 0.7.0", str(code))
        self.assertIn("are 0.6.9", str(code))
        self.assertIn("aliniază versiunile", str(code))
        self.assertFalse((self.repo.root / "releases").exists())
        write(self.repo.root, "studio-plugin/modules/Main.luau", "-- fără antet\nreturn {}\n")
        code, out, err = self.run_main("--github", "ana/harness", "--dry-run")
        self.assertIn("are niciunul", str(code))
        self.assertEqual(self.git.calls, [])
        self.assertIsNone(publish_release.app_header_version(self.repo.root))
        write(self.repo.root, "studio-plugin/modules/Main.luau", MAIN)
        self.assertEqual(publish_release.app_header_version(self.repo.root), "0.7.0")
        code, out, err = self.run_main("--github", "ana/harness", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.repo.root / "releases" / "manifest.json").is_file())

    def test_manifest_without_luau_sources_keeps_the_0_7_shape(self):
        repo = LuauRepo(Path(self.temp.name) / "plain", loader=None, main=None)
        # Un checkout fără surse Luau nu are nici pachetul construit: `dist/StudioHarness.rbxmx` fals ar fi refuzat ca neverificabil (acoperit în test_publish_release).
        (repo.root / "dist" / "StudioHarness.rbxmx").unlink()
        self.use_repo(repo)
        code, out, err = self.run_main("--github", "ana/harness", "--dry-run")
        self.assertEqual(code, 0, err)
        manifest = json.loads((repo.root / "releases" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["files"]), {"plugin", "hub"})
        self.assertEqual(manifest["loader_version"], "0.7.0")
        self.assertNotIn("bundle_revision", manifest)
        self.assertIs(updater.validate_manifest(manifest), manifest)
        self.assertNotIn("reconstruit", out)


class PublishCmdTests(unittest.TestCase):
    def test_publish_cmd_uses_the_channel_repo_and_bumps_patch(self):
        launcher = (ROOT / "Publish.cmd").read_text(encoding="utf-8")
        self.assertTrue(launcher.isascii(), "Publish.cmd rulează în cmd.exe: doar ASCII")
        self.assertIn("scripts\\publish_release.py --github --bump patch", launcher)
        self.assertIn('if not "%CODE%"=="0" pause', launcher)
        self.assertIn("exit /b %CODE%", launcher)
        self.assertIn("github_repo", launcher)
        self.assertIn('cd /d "%~dp0"', launcher)


if __name__ == "__main__":
    unittest.main()
