"""Construiește pachetele de release, manifestul canalului de actualizare și publică repo-ul pe GitHub și/sau Hugging Face.

Fără secrete în repo: git folosește credențialele utilizatorului, tokenul HF vine doar din HF_TOKEN (mediul publicatorului),
codul de administrator al hub-ului este un Secret al Space-ului (sau fișierul `hub-admin-token` de pe server), iar scanarea
de secrete oprește publicarea dacă găsește ceva suspect — în tot ce trimite `git add -A`, inclusiv `tests/` și `design/`, care
nu intră în pachete, dar ajung pe GitHub. 1.0: pachetele nu mai conțin `Setup-Team.cmd`/`setup-team.ps1`
(nu există echipe), iar `dist/StudioHarness.rbxmx` conține doar placeholder-ul `LocalToken` (tokenul se injectează la instalare).

    python scripts/publish_release.py --github OWNER/REPO --bump patch      # 0.8: bump + build + scan + git add/commit/push
    python scripts/publish_release.py --github                              # OWNER/REPO din update-channel.json (github_repo)
    python scripts/publish_release.py --repo OWNER/studio-harness           # build + scan + upload pe Hugging Face
    python scripts/publish_release.py --github OWNER/REPO --dry-run         # doar build + scan
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import updater  # noqa: E402
from updater import current_version, parse_version  # noqa: E402
from xml.etree.ElementTree import ParseError as ET_ERROR  # noqa: E402

HUB_FILES = {
    "scripts/team_hub.py": "scripts/team_hub.py", "scripts/claims.py": "scripts/claims.py",
    "scripts/local_state.py": "scripts/local_state.py", "scripts/updater.py": "scripts/updater.py",
    # 1.0: team_hub.py importă project_map (workspace_meta); fără el hub-ul găzduit ar reporni cu ImportError după auto-update.
    "scripts/project_map.py": "scripts/project_map.py",
    "update-channel.json": "update-channel.json",
    "deploy/install.sh": "deploy/ubuntu/install.sh", "deploy/studio-harness-hub.service": "deploy/ubuntu/studio-harness-hub.service",
    "deploy/Caddyfile": "deploy/ubuntu/Caddyfile", "deploy/nginx-hub.conf": "deploy/ubuntu/nginx-hub.conf",
    "deploy/Dockerfile": "deploy/ubuntu/Dockerfile", "README.md": "deploy/ubuntu/README.md", "AI-BRIEF.md": "deploy/ubuntu/AI-BRIEF.md",
    # 0.7: panoul web, servit de hub la /panel (aplicat aplatizat în <app>/panel/index.html).
    "panel/index.html": "panel/index.html",
}
# 1.0: fără `Setup-Team.cmd`/`Start-Team-Hub.cmd` (nu există echipe); `Start-Hub.cmd` pornește un hub local, `Publish.cmd` publică.
PLUGIN_INCLUDE = ("scripts", "hooks", "skills", "studio-plugin", "dist", "codex", "deploy", "panel", ".claude-plugin", ".mcp.json",
                  "update-channel.json", "README.md", "STUDIO_HUB_CONTRACT.md", "STUDIO_BRIDGE_CONTRACT.md", "VERIFICATION.md",
                  "Install-Studio-Plugin.cmd", "Install-Codex-Config.cmd", "Start-Daemon.cmd", "Start-Hub.cmd", "Publish.cmd", "Dockerfile")
EXCLUDE_DIRS = {"__pycache__", ".runtime", "release", ".git", ".venv", "node_modules", "tests", "design", ".harness-output"}
# Scanarea de secrete privește tot ce ajunge în commit (`git add -A`), nu doar ce intră în pachete: `tests/` și `design/` sunt
# publicate pe GitHub, deci un token lipit într-un fixture sau într-un document de design trebuie să oprească publicarea.
SCAN_EXCLUDE_DIRS = {"__pycache__", ".runtime", "release", ".git", ".venv", "node_modules", ".harness-output"}
# Fișiere eliminate în 1.0 care pot rămâne într-un checkout vechi: nu intră în pachete chiar dacă folderul lor este inclus.
EXCLUDE_FILES = {"scripts/setup-team.ps1", "Setup-Team.cmd", "Start-Team-Hub.cmd"}
EXCLUDE_UPLOAD = ["release/**", ".runtime/**", "**/__pycache__/**", "*.pyc", ".git/**", ".venv/**", "*.token", "team.json",
                  "local-token", "device-token", "hub-admin-token", "team-token", ".env", ".env.*", "*.log"]
SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "cheie Anthropic"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), "token Hugging Face"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "token GitHub"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "cheie AWS"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "cheie API"),
    (re.compile(r"(?i)\"(team_token|local_token|device_token|admin_token|hub_admin_token|api_key|password)\"\s*:\s*\"(?!\.\.\.|<)[^\"]{12,}\""),
     "secret în JSON"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"), "cheie privată"),
    # Tokenul de dispozitiv este `secrets.token_hex(32)`, adică 64 de caractere hex; regula cere și un nume sugestiv (orice
    # identificator care se termină în token/key/secret/password/code, inclusiv `STUDIO_HARNESS_ADMIN_TOKEN=…`), ca sumele
    # SHA256 din manifest și id-urile scurte din fixture-uri să nu fie raportate.
    # Numele este plafonat la 64 de caractere ca să nu existe backtracking pătratic pe un fișier de 2 MB fără spații.
    (re.compile(r"(?i)\b[A-Za-z0-9_-]{0,64}(?:token|key|secret|password|passwd|code)s?[\"'`]?[ \t]*[=:][ \t]*[\"'`]?[0-9a-f]{32,}"),
     "token hex cu nume de secret"),
]
SECRET_NAMES = {".env", "team.json", "local-token", "device-token", "hub-admin-token", "team-token", "daemon.log"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".txt", ".cmd", ".ps1", ".sh", ".luau", ".conf", ".service", ".yml", ".yaml", ".html", ".css", ".js",
                 ".rbxmx", ""}
# Doar pentru scanare: chei și certificate nu se împachetează niciodată, dar `git add -A` le-ar publica, deci trebuie citite.
SCAN_SUFFIXES = TEXT_SUFFIXES | {".pem", ".key", ".crt", ".cer", ".env"}

PUBLIC_CHANNEL = "https://lostcube.pro/roblox/harness/releases/manifest.json"
REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
BRANCH_PATTERN = re.compile(r"[A-Za-z0-9._/-]{1,120}")
FROM_CHANNEL = "@update-channel"
DEFAULT_BRANCH = "main"
LOADER_SOURCE = "studio-plugin/StudioHarness.server.luau"
MODULES_DIR = "studio-plugin/modules"
MAIN_SOURCE = MODULES_DIR + "/Main.luau"
# --bump: versiunea aplicației (`Hub`/`App`) din loader și din Main.luau; antetul `Loader` are versiunea lui, schimbată manual.
LUAU_VERSION_FILES = (LOADER_SOURCE, MAIN_SOURCE)
# 1.0: primul loader care citește `LocalToken`; un loader mai vechi ar ignora tokenul injectat la instalare.
MIN_LOADER_VERSION = "1.1.0"
_LUAU_APP_VERSION = re.compile(r"(--[ \t]*Studio Harness (?:Hub|App)[ \t]+)\d+(?:\.\d+){1,3}")
_SERVER_VERSION = re.compile(r'(server_version = "StudioHarness(?:Hub)?/)\d+\.\d+"')


def _files(root: Path, excluded: set[str]):
    for path in sorted(root.rglob("*")):
        if path.is_dir() or any(part in excluded for part in path.relative_to(root).parts):
            continue
        yield path


def _relative_files(root: Path):
    """Fișierele împachetate: fără teste, fără design, fără directoarele de lucru."""
    return _files(root, EXCLUDE_DIRS)


def _injected_token(path: Path) -> bool:
    """True dacă un `.rbxmx` are `LocalToken` cu alt conținut decât placeholder-ul, adică pachetul unui plugin deja instalat."""
    import build_studio_plugin
    try:
        return build_studio_plugin.verify(path, entry=None, require_version=False)["local_token"] != "placeholder"
    except (ValueError, OSError, ET_ERROR):
        return False


def scan_secrets(root: Path) -> list[str]:
    """Singura barieră dinaintea `git add -A`: se uită la tot repo-ul (inclusiv `tests/` și `design/`), nu doar la pachete.

    Un fișier dă cel mult o constatare (prima regulă care se potrivește): scopul este să oprească publicarea, nu să inventarieze.
    Regulile sunt precise intenționat — fixture-urile de test conțin șiruri care seamănă cu tokenuri (`"cod-admin-de-test-…"`),
    iar o scanare lacomă ar opri orice publicare."""
    findings = []
    for path in _files(root, SCAN_EXCLUDE_DIRS):
        relative = path.relative_to(root).as_posix()
        if path.name in SECRET_NAMES or path.name.startswith(".env"):
            findings.append(relative + ": fișier care nu se publică")
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES or path.stat().st_size > 2 * 1024 * 1024:
            continue
        if path.suffix.lower() == ".rbxmx" and _injected_token(path):
            findings.append(relative + ": plugin cu tokenul local injectat (se publică doar cu placeholder)")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                # Doar începutul potrivirii (prefixul mărcii sau numele variabilei): constatarea se tipărește, nu trebuie să scurgă secretul.
                findings.append(relative + ": " + label + " (" + match.group(0)[:8] + "…)")
                break
    return findings


def _zip(paths: dict[str, Path], top: str, destination: Path) -> dict:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        for relative, source in sorted(paths.items()):
            data = source.read_bytes()
            if source.suffix.lower() in TEXT_SUFFIXES and source.suffix.lower() not in (".rbxmx",):
                data = data.replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(top + "/" + relative, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = ((0o100755 if relative.endswith(".sh") else 0o100644) << 16)
            bundle.writestr(info, data)
    data = destination.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def loader_version(root: Path | None = None) -> str | None:
    """Versiunea loader-ului (`-- Studio Harness Loader X.Y.Z`); None când sursa lipsește sau antetul nu este de loader."""
    source = (root or ROOT) / LOADER_SOURCE
    try:
        return updater.loader_version(source.read_text(encoding="utf-8-sig"))
    except OSError:
        return None


def build(version: str, release: Path, base_url: str) -> dict:
    release.mkdir(parents=True, exist_ok=True)
    plugin_paths = {}
    for item in PLUGIN_INCLUDE:
        path = ROOT / item
        if path.is_file():
            plugin_paths[item] = path
        elif path.is_dir():
            for file in _relative_files(path):
                plugin_paths[file.relative_to(ROOT).as_posix()] = file
    for excluded in EXCLUDE_FILES:
        plugin_paths.pop(excluded, None)
    hub_paths = {relative: ROOT / source for relative, source in HUB_FILES.items() if (ROOT / source).is_file()}
    plugin_zip = release / f"roblox-studio-harness-{version}.zip"
    hub_zip = release / f"studio-harness-hub-{version}-ubuntu.zip"
    files = {
        "plugin": {"url": base_url + plugin_zip.name, **_zip(plugin_paths, f"roblox-studio-harness-{version}", plugin_zip)},
        "hub": {"url": base_url + hub_zip.name, **_zip(hub_paths, f"studio-harness-hub-{version}", hub_zip)},
    }
    manifest = {"version": version, "published": time.strftime("%Y-%m-%dT%H:%M:%S"), "notes": "Studio Harness " + version,
                "loader_version": loader_version(ROOT) or version, "files": files}
    # 0.8: modulele Luau ale aplicației din Studio, ca pachet separat (updater-ul le pune în Roblox\Plugins\StudioHarness\app\).
    modules_dir = ROOT / MODULES_DIR
    modules = updater.read_luau_modules(modules_dir) if modules_dir.is_dir() else {}
    zips = [plugin_zip, hub_zip]
    if modules:
        app_zip = release / f"studio-harness-app-{version}.zip"
        files["studio_app"] = {"url": base_url + app_zip.name,
                               **_zip({name + ".luau": modules_dir / (name + ".luau") for name in modules}, f"studio-harness-app-{version}", app_zip)}
        manifest["bundle_revision"] = updater.bundle_revision(modules)
        zips.append(app_zip)
    updater.validate_manifest(manifest)
    (release / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    for kind, path in zip(("plugin", "hub", "studio_app"), zips):
        (release / (path.name + ".sha256")).write_bytes((files[kind]["sha256"] + "  " + path.name + "\n").encode("utf-8"))
    return manifest


def read_channel(root: Path | None = None) -> dict:
    try:
        loaded = json.loads(((root or ROOT) / "update-channel.json").read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def hf_base(repo: str) -> str:
    return f"https://huggingface.co/spaces/{repo}/resolve/main/releases/"


def github_base(repo: str, branch: str = DEFAULT_BRANCH) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/releases/"


def set_channel(repo: str | None = None, github: str | None = None, branch: str = DEFAULT_BRANCH) -> str:
    """Scrie update-channel.json: canalul public (lostcube.pro) rămâne, upstream-ul devine GitHub raw (preferat) sau Hugging Face.

    `github_repo`/`github_branch` sunt salvate ca Publish.cmd să refolosească repo-ul. Întoarce baza upstream a pachetelor."""
    if not repo and not github:
        raise ValueError("set_channel cere --github sau --repo.")
    current = read_channel()
    upstream = github_base(github, branch) if github else hf_base(repo or "")
    manifest_url = current.get("manifest_url")
    public = manifest_url if isinstance(manifest_url, str) and "OWNER" not in manifest_url else PUBLIC_CHANNEL
    settings: dict = {"manifest_url": public, "upstream_manifest_url": upstream + "manifest.json", "auto": current.get("auto", True) is True}
    github_repo = github or (current.get("github_repo") if isinstance(current.get("github_repo"), str) else None)
    if github_repo:
        settings["github_repo"] = github_repo
        settings["github_branch"] = branch if github else (current.get("github_branch") if isinstance(current.get("github_branch"), str) else DEFAULT_BRANCH)
    (ROOT / "update-channel.json").write_bytes((json.dumps(settings, indent=1) + "\n").encode("utf-8"))
    return upstream


def next_version(version: str, kind: str) -> str:
    major, minor, patch = parse_version(version)[:3]
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    raise ValueError("--bump acceptă patch sau minor.")


def _rewrite(path: Path, pattern: re.Pattern[str], replacement: str, required: bool) -> bool:
    """Înlocuiește în fișier păstrând octeții (BOM, CRLF); False dacă fișierul lipsește sau modelul nu apare."""
    try:
        raw = path.read_bytes()
    except OSError:
        if required:
            raise SystemExit(f"{path.relative_to(ROOT).as_posix()} lipsește; nu pot actualiza versiunea.") from None
        return False
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    updated, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        if required:
            raise SystemExit(f"{path.relative_to(ROOT).as_posix()} nu conține versiunea de înlocuit; aliniază versiunile mai întâi.")
        return False
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + updated.encode("utf-8"))
    return True


def bump_version(kind: str) -> tuple[str, str]:
    """Crește versiunea (patch|minor) în plugin.json, studio_bridge.py, team_hub.py și antetul aplicației Luau (Hub|App)."""
    current = current_version(ROOT)
    parse_version(current)
    new = next_version(current, kind)
    major_minor = ".".join(new.split(".")[:2])
    _rewrite(ROOT / ".claude-plugin" / "plugin.json", re.compile(r'("version"\s*:\s*")' + re.escape(current) + '"'), r"\g<1>" + new + '"', True)
    for script in ("scripts/studio_bridge.py", "scripts/team_hub.py"):
        _rewrite(ROOT / script, re.compile(r'(VERSION = ")' + re.escape(current) + '"'), r"\g<1>" + new + '"', True)
        _rewrite(ROOT / script, _SERVER_VERSION, r"\g<1>" + major_minor + '"', False)
    for relative in LUAU_VERSION_FILES:
        _rewrite(ROOT / relative, _LUAU_APP_VERSION, r"\g<1>" + new, False)
    return current, new


def app_header_version(root: Path | None = None) -> str | None:
    """Versiunea din antetul `-- Studio Harness App X.Y.Z` al lui Main.luau; None când aplicația lipsește."""
    source = (root or ROOT) / MAIN_SOURCE
    try:
        return updater.app_version(source.read_text(encoding="utf-8-sig"))
    except OSError:
        return None


def verify_versions(version: str) -> None:
    """Oprește publicarea când versiunile nu sunt aliniate: daemon, hub, antetul `App` din Main.luau, loader-ul ≥ 1.1.0.

    Loader-ul are versiunea lui (schimbată manual), dar 1.0 cere cel puțin `MIN_LOADER_VERSION`: doar de acolo loader-ul
    citește `LocalToken` injectat la instalare."""
    for script, constant in (("scripts/studio_bridge.py", "VERSION"), ("scripts/team_hub.py", "VERSION")):
        text = (ROOT / script).read_text(encoding="utf-8")
        if f'{constant} = "{version}"' not in text:
            raise SystemExit(f"{script} nu are {constant} = \"{version}\"; aliniază versiunile înainte de publicare.")
    if (ROOT / MAIN_SOURCE).is_file():
        app = app_header_version(ROOT)
        if app != version:
            raise SystemExit(f"{MAIN_SOURCE} nu are antetul `-- Studio Harness App {version}` (are {app or 'niciunul'}); "
                             "aliniază versiunile înainte de publicare.")
    loader = loader_version(ROOT)
    if loader is not None and parse_version(loader) < parse_version(MIN_LOADER_VERSION):
        raise SystemExit(f"{LOADER_SOURCE} are loader-ul {loader}; 1.0 cere cel puțin {MIN_LOADER_VERSION} "
                         "(primul loader care citește LocalToken).")


def git_publish(branch: str, version: str, run=None) -> bool:
    """`git add -A`, `git commit`, `git push origin BRANCH` prin subprocess (fără shell), doar când repo-ul are `.git`."""
    run = run or subprocess.run
    if not (ROOT / ".git").exists():
        print("Repo-ul nu este încă un depozit git; publicarea pe GitHub nu s-a făcut. O singură dată:")
        print("  git init -b " + branch)
        print("  git remote add origin https://github.com/OWNER/REPO.git")
        print("apoi rulează din nou Publish.cmd.")
        return False
    git = shutil.which("git")
    if not git:
        raise SystemExit("git nu este instalat sau nu este în PATH; publicarea pe GitHub nu s-a făcut.")

    def call(*arguments: str):
        return run([git, *arguments], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")

    result = call("add", "-A")
    if result.returncode != 0:
        raise SystemExit("git add a eșuat: " + (result.stderr or result.stdout).strip()[-500:])
    result = call("commit", "-m", "Studio Harness " + version)
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        if "nothing to commit" not in output and "nimic de comis" not in output:
            raise SystemExit("git commit a eșuat: " + output.strip()[-500:])
        print("Nimic nou de comis; trimit ce există.")
    result = call("push", "origin", branch)
    if result.returncode != 0:
        raise SystemExit("git push origin " + branch + " a eșuat: " + (result.stderr or result.stdout).strip()[-500:])
    return True


def upload(repo: str, folder: Path) -> str:
    try:
        from huggingface_hub import HfApi
    except ImportError:
        raise SystemExit("Instalează întâi: pip install huggingface_hub (tokenul vine din HF_TOKEN, nu se salvează în repo).")
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("Setează HF_TOKEN în mediul acestui terminal (scriere pe repo). Nu îl pune în fișiere.")
    api = HfApi(token=token)
    api.create_repo(repo_id=repo, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    api.upload_folder(folder_path=str(folder), repo_id=repo, repo_type="space", ignore_patterns=EXCLUDE_UPLOAD,
                      commit_message="Studio Harness " + current_version(ROOT))
    return f"https://huggingface.co/spaces/{repo}"


def rebuild_studio_plugin() -> str | None:
    """Reconstruiește dist/StudioHarness.rbxmx din surse (loader + module, intrarea Main); None când sursele lipsesc."""
    source = ROOT / LOADER_SOURCE
    if not source.is_file():
        return None
    import build_studio_plugin
    try:
        destination = build_studio_plugin.build(source, ROOT / "dist" / "StudioHarness.rbxmx", entry=build_studio_plugin.DEFAULT_ENTRY, require_version=True)
    except ValueError as error:
        raise SystemExit("Pluginul Studio nu a putut fi construit: " + str(error)) from None
    return str(destination)


def check_prebuilt_plugin() -> None:
    """Fără sursa loader-ului nu putem reconstrui pachetul: cel din `dist/` se publică doar dacă este verificabil și cu placeholder."""
    package = ROOT / "dist" / "StudioHarness.rbxmx"
    if not package.is_file():
        return
    import build_studio_plugin
    try:
        summary = build_studio_plugin.verify(package, entry=None, require_version=False)
    except (ValueError, OSError, ET_ERROR) as error:
        raise SystemExit("dist/StudioHarness.rbxmx nu poate fi verificat (" + str(error) + ") și " + LOADER_SOURCE
                         + " lipsește; nu public un pachet neverificabil.") from None
    if summary["local_token"] != "placeholder":
        raise SystemExit("dist/StudioHarness.rbxmx conține un token local injectat (pachet instalat, nu construit); "
                         "reconstruiește-l cu python scripts/build_studio_plugin.py înainte de publicare.")
    print("Plugin Studio: dist/StudioHarness.rbxmx păstrat ca atare (" + LOADER_SOURCE + " lipsește), cu placeholder-ul LocalToken.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build + manifest + publicare pe GitHub (sursa canalului) și/sau Hugging Face (Space Docker).")
    parser.add_argument("--github", nargs="?", const=FROM_CHANNEL, default=None,
                        help="OWNER/REPO pe GitHub; fără valoare folosește github_repo din update-channel.json.")
    parser.add_argument("--branch", default=None, help="Branch-ul GitHub (implicit github_branch din update-channel.json sau main).")
    parser.add_argument("--repo", default=None, help="OWNER/nume pe Hugging Face, de exemplu ellob/studio-harness (opțional).")
    parser.add_argument("--bump", choices=("patch", "minor"), default=None, help="Crește versiunea înainte de build.")
    parser.add_argument("--dry-run", action="store_true", help="Doar construiește și scanează; fără git push și fără upload.")
    options = parser.parse_args()
    channel = read_channel()
    github = options.github
    if github == FROM_CHANNEL:
        github = channel.get("github_repo") if isinstance(channel.get("github_repo"), str) else None
        if not github:
            raise SystemExit("github_repo lipsește din update-channel.json. Rulează o dată: python scripts\\publish_release.py --github OWNER/REPO --bump patch"
                             " (Publish.cmd refolosește apoi repo-ul salvat).")
    if github is not None and not REPO_PATTERN.fullmatch(github):
        parser.error("--github trebuie să fie OWNER/REPO.")
    if options.repo is not None and not REPO_PATTERN.fullmatch(options.repo):
        parser.error("--repo trebuie să fie OWNER/nume.")
    if options.branch is not None:
        branch = options.branch
    else:
        branch = (channel.get("github_branch") if isinstance(channel.get("github_branch"), str) else None) or DEFAULT_BRANCH
    if not BRANCH_PATTERN.fullmatch(branch) or branch.startswith("-") or ".." in branch:
        parser.error("--branch este invalid.")
    if not github and not options.repo and not options.dry_run:
        parser.error("Dă --github OWNER/REPO (sursa canalului) și/sau --repo OWNER/nume (Hugging Face).")
    if options.bump:
        old, new = bump_version(options.bump)
        print("Versiune: " + old + " → " + new)
    version = current_version(ROOT)
    parse_version(version)
    verify_versions(version)
    rebuilt = rebuild_studio_plugin()
    if rebuilt:
        print("Plugin Studio reconstruit: dist/StudioHarness.rbxmx (loader " + (loader_version(ROOT) or version) + ")")
    else:
        check_prebuilt_plugin()
    if github:
        base_url = github_base(github, branch)
    elif options.repo:
        base_url = hf_base(options.repo)
    else:
        # Dry-run fără țintă: URL-urile pachetelor urmează canalul deja configurat (upstream, altfel cel public).
        upstream = channel.get("upstream_manifest_url") or channel.get("manifest_url")
        base_url = upstream.rsplit("/", 1)[0] + "/" if isinstance(upstream, str) and upstream.endswith("/manifest.json") else PUBLIC_CHANNEL.rsplit("/", 1)[0] + "/"
    if not options.dry_run:
        set_channel(options.repo, github, branch)
    release = ROOT / "releases"
    shutil.rmtree(release, ignore_errors=True)
    manifest = build(version, release, base_url)
    findings = scan_secrets(ROOT)
    print("Versiune:", version)
    print("Manifest:", json.dumps({kind: {"size": entry["size"], "sha256": entry["sha256"][:16] + "…"} for kind, entry in manifest["files"].items()}))
    print("Loader:", manifest["loader_version"] + ("; revizie module " + manifest["bundle_revision"] if manifest.get("bundle_revision") else ""))
    if findings:
        print("Publicare oprită; posibile secrete:")
        for finding in findings:
            print("  -", finding)
        return 2
    print("Scanare secrete: nimic găsit.")
    if options.dry_run:
        print("Dry-run: fără git push și fără upload. Fișierele sunt în", release)
        return 0
    code = 0
    if github:
        if git_publish(branch, version):
            print("Publicat pe GitHub: https://github.com/" + github + " (" + branch + ")")
            print("Manifest upstream: " + github_base(github, branch) + "manifest.json; hub-ul de pe lostcube.pro îl oglindește la următoarea verificare.")
        else:
            code = 3
    if options.repo:
        url = upload(options.repo, ROOT)
        print("Publicat:", url)
        print("Setează Secretul Space-ului STUDIO_HARNESS_ADMIN_TOKEN (Settings → Variables and secrets); developerii pun "
              '{"hub_url": "https://' + options.repo.replace("/", "-") + '.hf.space"} în config.json sau setează STUDIO_HARNESS_HUB_URL.')
    return code


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
