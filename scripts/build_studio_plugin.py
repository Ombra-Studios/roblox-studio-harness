"""Construiește pluginul .rbxmx, inclusiv ModuleScript-urile locale.

0.8: pluginul instalat este un loader (`StudioHarness.server.luau`, antet `-- Studio Harness Loader|Hub X.Y.Z`) plus folderul
`Modules` cu aplicația (intrarea `Main`); `verify` confirmă că fișierul rezultat le conține pe amândouă.

1.0: loader-ul primește un copil `StringValue` numit `LocalToken` cu valoarea `STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER`.
Fișierul din `dist/` (și din pachetele publicate) conține doar placeholder-ul; `install-studio-plugin.ps1` și
`updater.install_studio_plugin` îl înlocuiesc cu tokenul UI local când scriu fișierul în `Roblox\\Plugins\\`."""

import argparse
import itertools
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from updater import LOCAL_TOKEN_PLACEHOLDER

MAX_SOURCE_BYTES = 2 * 1024 * 1024
DEFAULT_ENTRY = "Main"
LOCAL_TOKEN_NAME = "LocalToken"
HEADER_VERSION = re.compile(r"^[ \t]*--[ \t]*Studio Harness (?:Loader|Hub|App)[ \t]+(\d+(?:\.\d+){1,3})\b", re.MULTILINE)


def header_version(code: str) -> str | None:
    """Versiunea din antetul `-- Studio Harness Loader|Hub|App X.Y.Z` (prima linie de comentariu a sursei)."""
    match = HEADER_VERSION.search(code)
    return match.group(1) if match else None


def local_token_value(loader: ET.Element) -> str | None:
    """Valoarea StringValue-ului `LocalToken` de sub scriptul loader; None dacă lipsește."""
    for node in loader.findall("Item[@class='StringValue']"):
        if node.findtext("Properties/string[@name='Name']") == LOCAL_TOKEN_NAME:
            return node.findtext("Properties/string[@name='Value']") or ""
    return None


def verify(destination: Path, entry: str | None = DEFAULT_ENTRY, require_version: bool = True) -> dict:
    """Verifică .rbxmx-ul: scriptul loader cu sursă (și versiune în antet), `LocalToken`, folderul Modules și modulul de intrare.

    `local_token` din rezumat este `"placeholder"` pentru fișierul construit și `"injected"` pentru unul instalat (token înlocuit)."""
    root = ET.parse(destination).getroot()
    model = root.find("Item[@class='Model']")
    if model is None:
        raise ValueError("Pachetul nu conține modelul pluginului.")
    loader = model.find("Item[@class='Script']")
    source = loader.find("Properties/ProtectedString[@name='Source']") if loader is not None else None
    if loader is None or source is None or not (source.text or "").strip():
        raise ValueError("Pachetul nu conține scriptul loader al pluginului.")
    version = header_version(source.text or "")
    if require_version and version is None:
        raise ValueError("Antetul loader-ului nu are versiune (`-- Studio Harness Loader X.Y.Z`).")
    token = local_token_value(loader)
    if not token:
        raise ValueError("Pachetul nu conține StringValue-ul LocalToken al loader-ului (sau are valoarea goală).")
    modules = next((node for node in model.findall("Item[@class='Folder']")
                    if node.findtext("Properties/string[@name='Name']") == "Modules"), None)
    names = [] if modules is None else [node.findtext("Properties/string[@name='Name']") for node in modules.findall("Item[@class='ModuleScript']")]
    if entry is not None:
        if modules is None:
            raise ValueError("Pachetul nu conține folderul Modules cu aplicația.")
        if entry not in names:
            raise ValueError("Pachetul nu conține modulul de intrare " + entry + ".luau în Modules.")
    return {"loader_version": version, "modules": names, "entry": entry,
            "local_token": "placeholder" if token == LOCAL_TOKEN_PLACEHOLDER else "injected"}


def read_source(path: Path) -> str:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"Sursa {path.name} depășește limita de dimensiune.")
    code = path.read_text(encoding="utf-8-sig")
    if not code.strip():
        raise ValueError(f"Sursa {path.name} este goală.")
    return code


def build(source: Path, destination: Path, entry: str | None = None, require_version: bool = False) -> Path:
    """Construiește .rbxmx-ul; cu `entry` verifică și că modulul de intrare există, cu `require_version` că loader-ul are antet versionat."""
    code = read_source(source)
    if "plugin" not in code:
        raise ValueError("Sursa nu conține un plugin Studio.")
    root = ET.Element("roblox", {"version": "4"})
    ET.SubElement(root, "External").text = "null"
    ET.SubElement(root, "External").text = "nil"
    references = itertools.count()
    expected_sources = {}

    def item(parent, class_name, name):
        node = ET.SubElement(parent, "Item", {"class": class_name, "referent": f"RBX{next(references)}"})
        properties = ET.SubElement(node, "Properties")
        ET.SubElement(properties, "string", {"name": "Name"}).text = name
        return node, properties

    model, _ = item(root, "Model", "Studio Harness")
    script, properties = item(model, "Script", "StudioHarness")
    ET.SubElement(properties, "bool", {"name": "Disabled"}).text = "false"
    ET.SubElement(properties, "ProtectedString", {"name": "Source"}).text = code
    expected_sources[script.attrib["referent"]] = code
    # 1.0: loader-ul citește `script:FindFirstChild("LocalToken")`; instalarea înlocuiește placeholder-ul cu tokenul UI local.
    _, token_properties = item(script, "StringValue", LOCAL_TOKEN_NAME)
    ET.SubElement(token_properties, "string", {"name": "Value"}).text = LOCAL_TOKEN_PLACEHOLDER

    modules_root = source.parent / "modules"
    if modules_root.exists() and not modules_root.resolve().is_relative_to(source.parent.resolve()):
        raise ValueError("Directorul modules nu poate indica o locație externă.")
    module_files = sorted(modules_root.rglob("*.luau")) if modules_root.is_dir() else []
    if module_files:
        modules, _ = item(model, "Folder", "Modules")
        folders = {(): modules}
        children = {(): set()}
        for path in module_files:
            if path.is_symlink() or not path.resolve().is_relative_to(modules_root.resolve()):
                raise ValueError("Modulele nu pot ieși din directorul sursă prin legături.")
            relative = path.relative_to(modules_root)
            parent_parts = ()
            for part in relative.parts[:-1]:
                key = (*parent_parts, part)
                if key not in folders:
                    if part.casefold() in children[parent_parts]:
                        raise ValueError("Două componente au același nume în pachet.")
                    folder, _ = item(folders[parent_parts], "Folder", part)
                    folders[key] = folder
                    children[parent_parts].add(part.casefold())
                    children[key] = set()
                parent_parts = key
            name = path.stem
            if name.casefold() in children[parent_parts]:
                raise ValueError("Două componente au același nume în pachet.")
            children[parent_parts].add(name.casefold())
            module_code = read_source(path)
            module, properties = item(folders[parent_parts], "ModuleScript", name)
            ET.SubElement(properties, "ProtectedString", {"name": "Source"}).text = module_code
            expected_sources[module.attrib["referent"]] = module_code

    ET.indent(root, space="  ")
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    recovered = {}
    for node in ET.fromstring(data).iter("Item"):
        text = node.find("./Properties/ProtectedString[@name='Source']")
        if text is not None:
            recovered[node.attrib["referent"]] = text.text
    if recovered != expected_sources:
        raise ValueError("Sursele Luau nu au trecut verificarea XML round-trip.")
    if require_version and header_version(code) is None:
        raise ValueError("Antetul loader-ului nu are versiune (`-- Studio Harness Loader X.Y.Z`).")
    if entry is not None and not any(path.parent == modules_root and path.stem == entry for path in module_files):
        raise ValueError("Modulul de intrare " + entry + ".luau lipsește din " + str(modules_root) + ".")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    temporary.write_bytes(data)
    try:
        verify(temporary, entry, require_version)
    except ValueError:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(destination)
    return destination


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=root / "studio-plugin" / "StudioHarness.server.luau")
    parser.add_argument("--output", type=Path, default=root / "dist" / "StudioHarness.rbxmx")
    parser.add_argument("--entry", default=DEFAULT_ENTRY, help="Modulul de intrare al aplicației (implicit Main); '' dezactivează verificarea.")
    args = parser.parse_args()
    result = build(args.source, args.output, entry=args.entry or None, require_version=True)
    summary = verify(result, args.entry or None, True)
    print(f"Plugin Roblox Studio construit: {result.name} (loader {summary['loader_version']}, {len(summary['modules'])} module, "
          f"LocalToken: {summary['local_token']})")


if __name__ == "__main__":
    main()
