"""Instalatorul .exe (1.0): sursa C#, scriptul de compilare și comportamentul executabilului construit.

Nu instalează nimic: executabilul este rulat doar cu `--help` și `--dry-run`, pe un pachet fals dintr-un director temporar."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "installer" / "StudioHarnessSetup.cs"
BUILD = ROOT / "scripts" / "build-installer.ps1"


def compiler() -> Path | None:
    """csc.exe din .NET Framework 4, dacă există pe această mașină."""
    windows = os.environ.get("WINDIR")
    if os.name != "nt" or not windows:
        return None
    for folder in ("Framework64", "Framework"):
        candidate = Path(windows) / "Microsoft.NET" / folder / "v4.0.30319" / "csc.exe"
        if candidate.is_file():
            return candidate
    return None


def powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


class SourceTests(unittest.TestCase):
    """Sursa instalatorului: text UTF-8 cu LF și garanțiile pe care le cere descărcarea unui pachet de pe internet."""

    def setUp(self):
        self.raw = SOURCE.read_bytes()
        self.code = self.raw.decode("utf-8")

    def test_source_is_utf8_lf_without_bom(self):
        self.assertFalse(self.raw.startswith(b"\xef\xbb\xbf"), "sursa C# se ține fără BOM, ca restul repo-ului")
        self.assertNotIn(b"\r", self.raw, "sursa C# se ține cu linii LF")
        self.assertIn("Studio Harness", self.code)

    def test_the_download_path_is_verified_and_cannot_escape_the_target_folder(self):
        # HTTPS impus, TLS 1.2 (implicitul vechi al .NET Framework ar fi refuzat de GitHub), sumă verificată, plafon de mărime.
        self.assertIn("SecurityProtocolType)3072", self.code)
        self.assertIn('url.StartsWith("https://"', self.code)
        self.assertIn("Suma de control nu se potrivește", self.code)
        self.assertIn("MaxPackageBytes", self.code)
        # Zip slip: fiecare intrare trebuie să rămână sub folderul de instalare.
        self.assertIn("destination.StartsWith(full", self.code)
        self.assertIn("Arhiva conține o cale în afara folderului de instalare", self.code)

    def test_it_never_writes_or_prints_a_token(self):
        for forbidden in ("local-token", "device-token", "hub-admin-token", "team_token"):
            self.assertNotIn(forbidden, self.code, "instalatorul nu are de-a face cu tokenuri")

    def test_the_three_steps_and_the_python_requirement_are_present(self):
        for needle in ("install-studio-plugin.ps1", "plugin marketplace add", "plugin install ",
                       "install-codex-config.ps1", "sys.version_info >= (3, 10)"):
            self.assertIn(needle, self.code)

    def test_build_script_is_valid_powershell_with_a_bom(self):
        raw = BUILD.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "scripturile .ps1 se salvează UTF-8 cu BOM")
        self.assertNotIn(b"\r", raw)
        shell = powershell()
        if not shell:
            self.skipTest("PowerShell nu este disponibil")
        command = ("$e = $null; [void][System.Management.Automation.PSParser]::Tokenize("
                   "(Get-Content -Raw '" + str(BUILD) + "'), [ref]$e); if ($e.Count) { $e[0].Message; exit 1 }")
        done = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class ExecutableTests(unittest.TestCase):
    """Compilează instalatorul într-un director temporar și îl rulează fără să instaleze nimic."""

    exe: Path | None = None
    temp: tempfile.TemporaryDirectory | None = None

    @classmethod
    def setUpClass(cls):
        csc = compiler()
        if csc is None:
            raise unittest.SkipTest("csc.exe (.NET Framework 4) există doar pe Windows")
        cls.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HARNESS_TEST_TMP"))
        target = Path(cls.temp.name) / "StudioHarnessSetup.exe"
        framework = csc.parent
        arguments = [str(csc), "/nologo", "/target:exe", "/platform:anycpu", "/optimize+", "/utf8output", "/out:" + str(target)]
        for reference in ("System.dll", "System.Core.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll"):
            arguments.append("/reference:" + str(framework / reference))
        arguments.append(str(SOURCE))
        done = subprocess.run(arguments, capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            cls.temp.cleanup()
            raise AssertionError("instalatorul nu compilează:\n" + done.stdout + done.stderr)
        cls.exe = target

    @classmethod
    def tearDownClass(cls):
        if cls.temp is not None:
            cls.temp.cleanup()

    def run_setup(self, *arguments, expect=0):
        done = subprocess.run([str(self.exe), *arguments], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
        self.assertEqual(done.returncode, expect, done.stdout + done.stderr)
        return done.stdout

    def fake_package(self):
        """Un folder care arată ca un pachet Studio Harness, fără niciun script real."""
        folder = Path(self.temp.name) / "pachet"
        (folder / "scripts").mkdir(parents=True, exist_ok=True)
        (folder / ".claude-plugin").mkdir(exist_ok=True)
        (folder / "dist").mkdir(exist_ok=True)
        (folder / "scripts" / "install-studio-plugin.ps1").write_text("# fals\n", encoding="utf-8")
        (folder / "scripts" / "install-codex-config.ps1").write_text("# fals\n", encoding="utf-8")
        (folder / ".claude-plugin" / "marketplace.json").write_text('{"name": "studio-harness"}\n', encoding="utf-8")
        (folder / "dist" / "StudioHarness.rbxmx").write_text("<roblox/>\n", encoding="utf-8")
        return folder

    def test_help_lists_every_option_and_changes_nothing(self):
        text = self.run_setup("--help")
        for option in ("--dry-run", "--yes", "--dir=", "--manifest=", "--skip-studio", "--skip-claude", "--skip-codex"):
            self.assertIn(option, text)

    def test_an_unknown_option_is_refused(self):
        self.assertIn("Opțiune necunoscută", self.run_setup("--ce-e-asta", expect=2))

    def test_dry_run_walks_the_three_steps_without_touching_anything(self):
        folder = self.fake_package()
        before = sorted(path.name for path in folder.rglob("*"))
        text = self.run_setup("--dry-run", "--yes", "--dir=" + str(folder))
        self.assertIn("Mod de probă", text)
        self.assertIn("Pluginul pentru Roblox Studio", text)
        self.assertIn("Pluginul pentru Claude Code", text)
        self.assertIn("Configurația pentru Codex", text)
        self.assertIn("(probă)", text)
        self.assertIn("Rezumat", text)
        self.assertNotIn("Eroare", text)
        self.assertEqual(sorted(path.name for path in folder.rglob("*")), before, "proba nu are voie să scrie nimic")

    def test_skipping_steps_is_reported(self):
        text = self.run_setup("--dry-run", "--yes", "--skip-claude", "--skip-codex", "--dir=" + str(self.fake_package()))
        self.assertIn("cerut explicit", text)
        self.assertNotIn("plugin marketplace add", text)

    def test_a_folder_that_is_not_a_package_is_refused(self):
        empty = Path(self.temp.name) / "gol"
        empty.mkdir(exist_ok=True)
        text = self.run_setup("--dry-run", "--yes", "--dir=" + str(empty), expect=1)
        self.assertIn("nu găsesc un pachet Studio Harness", text)


if __name__ == "__main__":
    unittest.main()
