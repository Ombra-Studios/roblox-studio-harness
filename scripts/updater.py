"""Auto-update: manifest public cu SHA256, descărcare verificată, aplicare atomică cu backup, fără secrete.

Canalul este un fișier JSON servit prin HTTPS (canalul public al hub-ului, oglindit de acesta din upstream-ul GitHub):
{"version": "1.0.0", "published": "...", "notes": "...", "loader_version": "1.1.0", "bundle_revision": "...",
 "files": {"plugin": {"url": "...zip", "sha256": "...", "size": 123}, "hub": {...}, "studio_app": {...}}}
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from local_state import TOKEN_PATTERN as LOCAL_TOKEN_PATTERN

MAX_MANIFEST = 64 * 1024
MAX_BUNDLE = 64 * 1024 * 1024
# 0.8: `studio_app` = zip cu modulele Luau ale aplicației din Studio (instalate în Roblox\Plugins\StudioHarness\app\).
BUNDLE_KINDS = ("plugin", "hub", "studio_app")
PLACEHOLDER = "OWNER"
# 1.0: `build_studio_plugin.py` pune în .rbxmx un StringValue `LocalToken` cu această valoare; la instalare este înlocuită cu
# conținutul lui `local-token`, ca loader-ul din Studio să se conecteze singur la daemon (fără cod de asociere tastat).
LOCAL_TOKEN_PLACEHOLDER = "STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER"
STUDIO_APP_DIR_VARIABLE = "STUDIO_HARNESS_STUDIO_APP_DIR"
INSTALLED_LOADER_FILE = "installed-loader.txt"
MAX_MODULE_BYTES = 2 * 1024 * 1024
MAX_MODULES = 256
REVISION_LENGTH = 16
_HEX = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{8,64}")
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_LOADER_HEADER = re.compile(r"^[ \t]*--[ \t]*Studio Harness Loader[ \t]+(\d+(?:\.\d+){1,3})\b", re.MULTILINE)
_APP_HEADER = re.compile(r"^[ \t]*--[ \t]*Studio Harness (?:Hub|App)[ \t]+(\d+(?:\.\d+){1,3})\b", re.MULTILINE)


class UpdateError(RuntimeError):
    pass


def parse_version(text: Any) -> tuple[int, ...]:
    if not isinstance(text, str):
        raise UpdateError("Versiune invalidă.")
    parts = text.strip().split(".")
    try:
        numbers = tuple(int(part) for part in parts)
    except ValueError:
        raise UpdateError("Versiune invalidă: " + text) from None
    if not 2 <= len(numbers) <= 4 or any(number < 0 for number in numbers):
        raise UpdateError("Versiune invalidă: " + text)
    return numbers + (0,) * (4 - len(numbers))


def current_version(root: Path) -> str:
    try:
        value = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
        parse_version(value)
        return value
    except (OSError, ValueError, UpdateError):
        return "0.0.0"


def channel(root: Path, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """URL-ul manifestului și dacă aplicarea este automată. `OWNER` neînlocuit înseamnă canal neconfigurat."""
    env = os.environ if environ is None else environ
    settings: dict[str, Any] = {"manifest_url": None, "upstream_manifest_url": None, "auto": True}
    try:
        loaded = json.loads((root / "update-channel.json").read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            settings.update({key: loaded[key] for key in ("manifest_url", "upstream_manifest_url", "auto") if key in loaded})
    except (OSError, ValueError):
        pass
    override = env.get("STUDIO_HARNESS_UPDATE_URL")
    if override:
        settings["manifest_url"] = override
    if env.get("STUDIO_HARNESS_AUTO_UPDATE", "").lower() in ("0", "false", "no", "off"):
        settings["auto"] = False
    for key in ("manifest_url", "upstream_manifest_url"):
        url = settings.get(key)
        if not isinstance(url, str) or PLACEHOLDER in url or not _allowed_url(url):
            settings[key] = None
    settings["auto"] = settings.get("auto") is True
    return settings


def _allowed_url(url: str) -> bool:
    return url.startswith("https://") or url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:")


def _get(url: str, limit: int, timeout: float) -> bytes:
    if not _allowed_url(url):
        raise UpdateError("Canalul de actualizare trebuie să fie HTTPS.")
    request = Request(url, headers={"User-Agent": "StudioHarnessUpdater", "Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except HTTPError as error:
        raise UpdateError(f"Canalul de actualizare a răspuns HTTP {error.code}.") from None
    except (URLError, TimeoutError, OSError) as error:
        raise UpdateError("Canalul de actualizare nu răspunde: " + type(error).__name__) from None
    if len(data) > limit:
        raise UpdateError("Răspunsul canalului depășește limita.")
    return data


def fetch_manifest(url: str, timeout: float = 15) -> dict[str, Any]:
    try:
        manifest = json.loads(_get(url, MAX_MANIFEST, timeout).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("Manifestul de actualizare nu este JSON valid.") from None
    return validate_manifest(manifest)


def validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise UpdateError("Manifestul trebuie să fie un obiect JSON.")
    parse_version(manifest.get("version"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise UpdateError("Manifestul nu conține fișiere.")
    for kind, entry in files.items():
        if kind not in BUNDLE_KINDS:
            raise UpdateError("Tip de pachet necunoscut în manifest: " + str(kind))
        if (not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not _allowed_url(entry["url"])
                or not isinstance(entry.get("sha256"), str) or not _HEX.fullmatch(entry["sha256"])
                or type(entry.get("size")) is not int or not 0 < entry["size"] <= MAX_BUNDLE):
            raise UpdateError("Intrarea " + str(kind) + " din manifest este invalidă.")
    # 0.8: versiunea loader-ului din Studio (rescrierea .rbxmx doar când se schimbă) și revizia modulelor; ambele opționale.
    if manifest.get("loader_version") is not None:
        try:
            parse_version(manifest["loader_version"])
        except UpdateError:
            raise UpdateError("loader_version din manifest este invalid.") from None
    revision = manifest.get("bundle_revision")
    if revision is not None and (not isinstance(revision, str) or not _REVISION.fullmatch(revision)):
        raise UpdateError("bundle_revision din manifest este invalid.")
    return manifest


def check(root: Path, timeout: float = 15, environ: dict[str, str] | None = None, current: str | None = None,
          manifest_url: str | None = None) -> dict[str, Any]:
    """Nu descarcă nimic: doar compară versiunea curentă cu manifestul (implicit `manifest_url` din canal)."""
    settings = channel(root, environ)
    if manifest_url:
        settings["manifest_url"] = manifest_url if _allowed_url(manifest_url) and PLACEHOLDER not in manifest_url else None
    current = current or current_version(root)
    result = {"current": current, "available": None, "newer": False, "auto": settings["auto"],
              "channel": settings["manifest_url"], "manifest": None, "error": None}
    if not settings["manifest_url"]:
        result["error"] = "Canal de actualizare neconfigurat (update-channel.json)."
        return result
    try:
        manifest = fetch_manifest(settings["manifest_url"], timeout)
    except UpdateError as error:
        result["error"] = str(error)
        return result
    result["manifest"] = manifest
    result["available"] = manifest["version"]
    result["newer"] = parse_version(manifest["version"]) > parse_version(current)
    return result


def download(entry: dict[str, Any], timeout: float = 120) -> bytes:
    data = _get(entry["url"], min(entry["size"], MAX_BUNDLE), timeout)
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise UpdateError("Pachetul descărcat nu corespunde sumei SHA256 din manifest; nu a fost aplicat.")
    return data


def _members(archive: zipfile.ZipFile) -> list[tuple[str, zipfile.ZipInfo]]:
    r"""Elimină folderul de top și refuză căile care ies din pachet.

    Verificarea acoperă toate segmentele, nu doar primul: folderul de top este eliminat, deci `pachet-1.0/C:/rău.py` ar avea
    `parts[0]` curat, iar `relative` („C:/rău.py”) ar deveni pe Windows o cale absolută la `staging / relative` (separatorii
    `\` sunt deja normalizați la `/` mai sus). De aceea `:` este căutat în toate segmentele, iar calea rezultată este
    verificată încă o dată cu ambele gramatici de căi.

    Tot ce nu stă sub folderul de top este ignorat (`relative` gol): pachetele construite de `publish_release` pun totul sub
    `roblox-studio-harness-<versiune>/`, iar un fișier lăsat la rădăcina arhivei ar ajunge altfel lângă fișierele deja
    aplatizate, ca și cum ar fi venit din pachet."""
    members = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.endswith("/"):
            continue
        parts = [part for part in name.split("/") if part]
        invalid = not parts or name.startswith("/") or any(part in ("..", "") or ":" in part for part in parts)
        relative = "/".join(parts[1:])
        if invalid or PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).is_absolute():
            raise UpdateError("Pachetul conține o cale invalidă: " + info.filename)
        if not relative:
            continue
        members.append((relative, info))
    if not members:
        raise UpdateError("Pachetul este gol.")
    return members


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)
    except OSError:
        return False


def is_git_checkout(target: Path) -> bool:
    return (target / ".git").exists()


def apply_bundle(data: bytes, target: Path, backup_root: Path, protect: tuple[str, ...] = (), flatten: str = "") -> dict[str, Any]:
    """Extrage pachetul lângă țintă, face backup la fișierele înlocuite și le mută atomic pe cât permite sistemul."""
    if is_git_checkout(target):
        raise UpdateError("Ținta este un checkout git; actualizarea se face manual (git pull), nu automat.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("Pachetul nu este un zip valid.") from None
    members = _members(archive)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = target.parent / (target.name + ".update-" + stamp)
    backup = backup_root / stamp
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    written: list[str] = []
    try:
        for relative, info in members:
            if any(relative == item or relative.startswith(item.rstrip("/") + "/") for item in protect):
                continue
            if flatten and relative.startswith(flatten):
                relative = relative[len(flatten):]
            destination = staging / relative
            # A doua apărare, după `_members`: nimic nu se scrie în afara directorului de lucru, oricât de creativ ar fi pachetul.
            if not _inside(destination, staging):
                raise UpdateError("Pachetul conține o cale invalidă: " + relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as sink:
                shutil.copyfileobj(source, sink, 1024 * 1024)
            if relative.endswith(".sh") and os.name != "nt":
                os.chmod(destination, 0o755)
            written.append(relative)
        backup.mkdir(parents=True, exist_ok=True)
        for relative in written:
            current = target / relative
            if current.exists():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, saved)
        for relative in written:
            source, current = staging / relative, target / relative
            current.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, current)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"written": written, "backup": str(backup)}


def studio_plugins_dir(environ: dict[str, str] | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    local = env.get("LOCALAPPDATA")
    if os.name != "nt" or not local:
        return None
    return Path(local) / "Roblox" / "Plugins"


def inject_local_token(data: bytes, local_token: str | None) -> bytes:
    """Conținutul .rbxmx-ului cu placeholder-ul `LocalToken` înlocuit de tokenul UI; neschimbat fără token sau fără placeholder.

    Tokenul are doar caractere URL-safe (`local_state.TOKEN_PATTERN`), deci nu are nevoie de escapare XML; orice altceva
    este refuzat, ca fișierul instalat să rămână un XML valid."""
    if local_token is None:
        return data
    if not isinstance(local_token, str) or not LOCAL_TOKEN_PATTERN.fullmatch(local_token):
        raise UpdateError("Codul local nu are formatul așteptat; pluginul nu a fost instalat.")
    return data.replace(LOCAL_TOKEN_PLACEHOLDER.encode("utf-8"), local_token.encode("utf-8"))


def install_studio_plugin(source: Path, backup_root: Path, environ: dict[str, str] | None = None,
                          local_token: str | None = None) -> Path | None:
    """Scrie StudioHarness.rbxmx în folderul de pluginuri Studio, cu backup; Studio îl încarcă la repornire.

    1.0: cu `local_token`, placeholder-ul `LocalToken` este înlocuit în fișierul instalat (pachetul din `dist/` rămâne neatins),
    iar „fișier identic” se decide după substituire, ca reinstalarea aceluiași plugin cu același token să nu facă backup."""
    plugins = studio_plugins_dir(environ)
    if plugins is None or not source.is_file():
        return None
    data = inject_local_token(source.read_bytes(), local_token)
    plugins.mkdir(parents=True, exist_ok=True)
    target = plugins / source.name
    if target.exists():
        if target.read_bytes() == data:
            return target
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_root / (source.stem + "-" + time.strftime("%Y%m%d-%H%M%S") + source.suffix))
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return target


# ----- 0.8: aplicația din Studio (loader + module Luau) -----


def studio_app_dir(environ: dict[str, str] | None = None) -> Path | None:
    """`Roblox\\Plugins\\StudioHarness\\app` (Windows) sau folderul dat prin STUDIO_HARNESS_STUDIO_APP_DIR (orice sistem, teste)."""
    env = os.environ if environ is None else environ
    override = env.get(STUDIO_APP_DIR_VARIABLE)
    if override:
        return Path(override)
    plugins = studio_plugins_dir(env)
    return None if plugins is None else plugins / "StudioHarness" / "app"


def loader_version(source: str) -> str | None:
    """Versiunea din antetul `-- Studio Harness Loader X.Y.Z`; None dacă sursa nu este un loader."""
    match = _LOADER_HEADER.search(source)
    return match.group(1) if match else None


def app_version(source: str) -> str | None:
    """Versiunea din antetul `-- Studio Harness Hub|App X.Y.Z` al aplicației."""
    match = _APP_HEADER.search(source)
    return match.group(1) if match else None


def _module_source(name: str, data: bytes) -> str:
    if len(data) > MAX_MODULE_BYTES:
        raise UpdateError("Modulul " + name + " depășește limita de dimensiune.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UpdateError("Modulul " + name + " nu este UTF-8.") from None
    return text.replace("\r\n", "\n")


def read_luau_modules(directory: Path) -> dict[str, str]:
    """Modulele `Nume.luau` direct din folder (fără subfoldere): {Nume: sursă cu linii LF}. Numele invalide sunt ignorate."""
    modules: dict[str, str] = {}
    if not directory.is_dir():
        return modules
    for path in sorted(directory.iterdir()):
        if path.suffix != ".luau" or not path.is_file():
            continue
        name = path.name[:-len(".luau")]
        if not _MODULE_NAME.fullmatch(name):
            continue
        if len(modules) >= MAX_MODULES:
            raise UpdateError("Prea multe module Luau în " + str(directory) + ".")
        modules[name] = _module_source(name, path.read_bytes())
    return modules


def extract_luau_modules(data: bytes) -> dict[str, str]:
    """Modulele dintr-un pachet `studio_app` (orice adâncime, după numele fișierului); refuză pachetele fără module."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("Pachetul aplicației Studio nu este un zip valid.") from None
    modules: dict[str, str] = {}
    for relative, info in _members(archive):
        base = relative.rsplit("/", 1)[-1]
        if not base.endswith(".luau"):
            continue
        name = base[:-len(".luau")]
        if not _MODULE_NAME.fullmatch(name):
            raise UpdateError("Pachetul aplicației Studio conține un modul cu nume invalid: " + base)
        if name in modules:
            raise UpdateError("Pachetul aplicației Studio conține modulul " + name + " de două ori.")
        if len(modules) >= MAX_MODULES:
            raise UpdateError("Pachetul aplicației Studio conține prea multe module.")
        with archive.open(info) as source:
            modules[name] = _module_source(name, source.read(MAX_MODULE_BYTES + 1))
    if not modules:
        raise UpdateError("Pachetul aplicației Studio nu conține module Luau.")
    return modules


def bundle_revision(modules: dict[str, str]) -> str:
    """Primele 16 hex din SHA256 peste (nume, sursă) sortate: aceeași revizie pentru același conținut, oriunde ar fi."""
    digest = hashlib.sha256()
    for name in sorted(modules):
        digest.update(name.encode("utf-8") + b"\0" + modules[name].encode("utf-8") + b"\0")
    return digest.hexdigest()[:REVISION_LENGTH]


def write_studio_app(modules: dict[str, str], environ: dict[str, str] | None = None,
                     backup_root: Path | None = None) -> dict[str, Any] | None:
    """Scrie modulele în folderul aplicației Studio, atomic (folder nou, apoi înlocuire), cu folderul vechi mutat în backup.

    None când nu există un folder de pluginuri Studio (alt sistem) sau nu sunt module; `changed` False când conținutul era identic."""
    app = studio_app_dir(environ)
    if app is None or not modules:
        return None
    revision = bundle_revision(modules)
    if app.is_dir():
        try:
            current = read_luau_modules(app)
        except UpdateError:
            current = {}
        if current == modules:
            return {"path": str(app), "revision": revision, "modules": len(modules), "changed": False, "backup": None}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = app.parent / (app.name + ".new-" + stamp)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name, source in modules.items():
        (staging / (name + ".luau")).write_bytes(source.encode("utf-8"))
    backup: Path | None = None
    if app.exists():
        if backup_root is not None and app.is_dir():
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = backup_root / ("app-" + stamp)
            counter = 1
            while backup.exists():
                counter += 1
                backup = backup_root / ("app-" + stamp + "-" + str(counter))
            shutil.move(str(app), str(backup))
        elif app.is_dir():
            shutil.rmtree(app)
        else:
            app.unlink()
    os.replace(staging, app)
    return {"path": str(app), "revision": revision, "modules": len(modules), "changed": True, "backup": str(backup) if backup else None}


def install_studio_app(source_dir: Path, environ: dict[str, str] | None = None, backup_root: Path | None = None) -> dict[str, Any] | None:
    """Copiază `studio-plugin/modules/*.luau` în Roblox\\Plugins\\StudioHarness\\app\\; loader-ul din Studio le încarcă fără repornire."""
    return write_studio_app(read_luau_modules(Path(source_dir)), environ, backup_root)


def installed_loader_version(state: Path) -> str | None:
    """Versiunea loader-ului instalat în Studio (state/installed-loader.txt); None dacă nu s-a înregistrat niciuna."""
    try:
        text = (Path(state) / INSTALLED_LOADER_FILE).read_text(encoding="utf-8").strip()
        parse_version(text)
        return text
    except (OSError, UpdateError):
        return None


def record_loader_version(state: Path, version: str) -> None:
    parse_version(version)
    directory = Path(state)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / (INSTALLED_LOADER_FILE + ".part")
    temporary.write_text(version.strip() + "\n", encoding="utf-8")
    os.replace(temporary, directory / INSTALLED_LOADER_FILE)


def mirror(manifest: dict[str, Any], directory: Path, public_base: str, timeout: float = 120) -> dict[str, Any]:
    """Descarcă și verifică pachetele din manifest într-un director servit public și rescrie URL-urile spre `public_base`."""
    validate_manifest(manifest)
    if not public_base.startswith("https://") and not public_base.startswith("http://127.0.0.1:") and not public_base.startswith("http://localhost:"):
        raise UpdateError("Baza publică a canalului trebuie să fie HTTPS.")
    directory.mkdir(parents=True, exist_ok=True)
    copied = json.loads(json.dumps(manifest))
    for kind, entry in copied["files"].items():
        name = entry["url"].rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name) or name in (".", ".."):
            raise UpdateError("Nume de fișier invalid în manifest: " + name)
        target = directory / name
        if not (target.is_file() and target.stat().st_size == entry["size"] and bundle_sha256(target) == entry["sha256"]):
            data = download(entry, timeout)
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        (directory / (name + ".sha256")).write_bytes((entry["sha256"] + "  " + name + "\n").encode("utf-8"))
        entry["url"] = public_base.rstrip("/") + "/" + name
    temporary = directory / "manifest.json.part"
    temporary.write_bytes((json.dumps(copied, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    os.replace(temporary, directory / "manifest.json")
    return copied


def bundle_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
