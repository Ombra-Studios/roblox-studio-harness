"""Hosting-ul hub-ului 1.0 (deploy/ubuntu, Docker, lansatoarele Windows).

Acoperă: /healthz și prefixul public fără antete, înregistrarea și dispozitivele offline în jurnal fără token-uri, fișierele de deploy
coerente cu modulele pe care team_hub.py le importă, lansatoarele 1.0 (Start-Hub.cmd, Start-Daemon.cmd, fără Setup-Team) și CLI-ul
hub-ului rulat ca proces separat, exact cum îl pornesc systemd/docker/Start-Hub.cmd (--show-admin-code, --approve-pending,
--open-enrollment). Serverele ascultă pe loopback, pe porturi libere (niciodată 34871/34880); starea se scrie doar în
%TEMP%\\studio-harness-1.0\\deploy; nimic nu atinge %LOCALAPPDATA% real sau rețeaua externă."""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import team_hub  # noqa: E402

DEPLOY = ROOT / "deploy" / "ubuntu"
HUB_SCRIPT = ROOT / "scripts" / "team_hub.py"
TEST_TMP = Path(os.environ.get("HARNESS_TEST_TMP") or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "studio-harness-1.0" / "deploy")
ADMIN_CODE = "cod-admin-de-test-0123456789"
DEVICE_TOKEN = hashlib.sha256(b"dispozitiv-de-test-deploy").hexdigest()
DEVICE_ID = hashlib.sha256(DEVICE_TOKEN.encode("utf-8")).hexdigest()[:16]
REGISTER_BODY = {"roblox": {"user_id": 101, "name": "ana"}, "machine": "pc-ana", "bridge_id": "bridge-ana", "version": team_hub.VERSION,
                 "workspace": {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"}}
# Porturile daemon-ului și hub-ului reale de pe acest PC: testele nu le ating niciodată.
RESERVED_PORTS = {34871, 34880}
# Variabile care ar schimba comportamentul hub-ului de test dacă ar veni din mediul developerului.
HUB_ENVIRONMENT = ("STUDIO_HARNESS_ADMIN_TOKEN", "STUDIO_HARNESS_OPEN_ENROLLMENT", "STUDIO_HARNESS_STATE_DIR", "STUDIO_HARNESS_HUB_URL")
# Termeni din 0.8 care nu mai au ce căuta în fișierele de deploy și în lansatoare.
TEAM_ERA_TERMS = ("team-token", "team_token", "TEAM_TOKEN", "team.json", "X-Studio-Harness-Team", "Setup-Team", "Start-Team-Hub", "/team/")


def read(path):
    return path.read_text(encoding="utf-8")


def temp_dir():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(TEST_TMP))


def free_port():
    """Un port liber pe loopback pe care hub-ul de test îl primește explicit prin --port (niciodată 34871/34880)."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port
    raise RuntimeError("Nu am găsit un port liber pe loopback.")


def hub_environment(extra=None):
    """Mediul proceselor de test: fără variabilele Studio Harness ale developerului, cu ieșirea în UTF-8."""
    environment = {name: value for name, value in os.environ.items() if name not in HUB_ENVIRONMENT}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.update(extra or {})
    return environment


def call(base, path, body=None, device=None, admin=None):
    """`(status, json, antete)` pentru o cerere către hub, cu antetele de dispozitiv/admin cerute; erorile HTTP nu ridică excepții."""
    headers = {}
    if device is not None:
        headers[team_hub.DEVICE_HEADER] = device
    if admin is not None:
        headers[team_hub.ADMIN_HEADER] = admin
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8")), dict(response.headers)
    except HTTPError as error:
        payload = error.read()
        return error.code, json.loads(payload.decode("utf-8")) if payload else None, dict(error.headers)


def local_modules(script):
    """Modulele din scripts/ importate (tranzitiv) de `script`: exact fișierele pe care install.sh și Dockerfile-urile trebuie să le copieze."""
    found, pending = set(), [script.stem]
    while pending:
        source = read(ROOT / "scripts" / (pending.pop() + ".py"))
        for match in re.finditer(r"^(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", source, re.MULTILINE):
            module = match.group(1)
            if module != script.stem and module not in found and (ROOT / "scripts" / (module + ".py")).is_file():
                found.add(module)
                pending.append(module)
    return sorted(found)


class HubProcess:
    """Hub-ul pornit ca proces separat (`python -u scripts/team_hub.py ...`), cu jurnalul capturat; se oprește la ieșirea din `with`."""

    def __init__(self, state, port, *flags, environ=None):
        self.state, self.port, self.flags, self.environ = Path(state), port, flags, environ
        self.base = "http://127.0.0.1:" + str(port)
        self.process = None
        self.output = ""

    def __enter__(self):
        command = [sys.executable, "-u", str(HUB_SCRIPT), "--listen", "127.0.0.1", "--port", str(self.port), "--state-dir", str(self.state),
                   "--no-auto-update", *self.flags]
        self.process = subprocess.Popen(command, cwd=str(ROOT), env=hub_environment(self.environ), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("hub-ul s-a oprit înainte de a răspunde la /healthz:\n" + self.stop())
            try:
                with urlopen(self.base + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        return self
            except (URLError, OSError):
                pass
            time.sleep(0.1)
        raise AssertionError("hub-ul nu a răspuns la /healthz în 20 s:\n" + self.stop())

    def __exit__(self, *_):
        self.stop()

    def stop(self):
        if self.process is None:
            return self.output
        if self.process.poll() is None:
            self.process.terminate()
        try:
            data, _ = self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            data, _ = self.process.communicate()
        self.output = (data or b"").decode("utf-8", "replace")
        self.process = None
        return self.output


class HealthzTests(unittest.TestCase):
    """Hub-ul în proces, pe port 0: rutele publice pentru monitorizare/proxy și jurnalul fără token-uri."""

    def setUp(self):
        self.lines = []
        self.now = 1_700_000_000.0
        self.hub = team_hub.Hub(ADMIN_CODE, clock=lambda: self.now, logger=self.lines.append)
        self.server = team_hub.HubServer("127.0.0.1", 0, self.hub)
        self.base = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_healthz_needs_no_headers_and_exposes_nothing(self):
        status, body, headers = call(self.base, "/healthz")
        self.assertEqual((status, body), (200, {"ok": True, "version": team_hub.VERSION}))
        self.assertEqual(headers.get("Server"), "StudioHarnessHub/1.0")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        # Fără antete, rutele hub-ului spun doar că dispozitivul este necunoscut; rutele /team/* din 0.8 nu mai există.
        status, body, _ = call(self.base, "/hub/status")
        self.assertEqual((status, body["ok"], body["status"]), (401, False, "unknown"))
        self.assertEqual(call(self.base, "/team/status")[0], 404)
        # Aceleași rute și sub prefixul public /roblox/harness (proxy care nu îl scoate).
        self.assertEqual(call(self.base, "/roblox/harness/healthz")[:2], (200, {"ok": True, "version": team_hub.VERSION}))
        self.assertEqual(call(self.base, "/roblox/harness/hub/status")[0], 401)
        self.assertEqual(call(self.base, "/roblox/harness/team/status")[0], 404)
        self.assertEqual(self.lines, [])

    def test_register_and_offline_are_logged_without_the_token(self):
        status, payload, _ = call(self.base, "/hub/register", REGISTER_BODY, device=DEVICE_TOKEN)
        self.assertEqual((status, payload["ok"], payload["status"], payload["device_id"]), (202, True, "pending", DEVICE_ID))
        self.assertIn("dispozitiv nou în așteptare: ana @ pc-ana (" + DEVICE_ID + ") (daemon " + team_hub.VERSION + ")", self.lines)
        # Un dispozitiv în așteptare își vede starea, dar rutele de membru îl refuză cu 403.
        status, payload, _ = call(self.base, "/hub/status", device=DEVICE_TOKEN)
        self.assertEqual((status, payload["device"]["status"]), (200, "pending"))
        status, payload, _ = call(self.base, "/hub/workspaces", device=DEVICE_TOKEN)
        self.assertEqual((status, payload["status"], payload["device_id"]), (403, "pending", DEVICE_ID))
        # Adminul îl aprobă (ca din fila Dispozitive a panoului); după 60 s fără sync, hub-ul îl declară offline.
        self.assertEqual(call(self.base, "/hub/admin/devices/approve", {"device_id": DEVICE_ID}, admin=ADMIN_CODE)[0], 200)
        self.assertEqual(call(self.base, "/hub/workspaces", device=DEVICE_TOKEN)[0], 200)
        self.now += team_hub.OFFLINE_SECONDS + 1
        self.assertEqual(call(self.base, "/hub/status", admin=ADMIN_CODE)[0], 200)
        self.assertIn("dispozitiv offline: ana @ pc-ana (" + DEVICE_ID + "); claims-urile lui au fost eliberate", self.lines)
        text = "\n".join(self.lines)
        for secret in (DEVICE_TOKEN, hashlib.sha256(DEVICE_TOKEN.encode("utf-8")).hexdigest(), ADMIN_CODE):
            self.assertNotIn(secret, text)


class DeployFilesTests(unittest.TestCase):
    """install.sh, unitatea systemd, Caddy/nginx și Dockerfile-urile descriu exact hub-ul 1.0 livrat."""

    def test_service_unit_and_installer_reference_the_shipped_files(self):
        unit = read(DEPLOY / "studio-harness-hub.service")
        # Aplicația stă în /var/lib/studio-harness/app (scriibil de serviciu, ca auto-update-ul să poată aplica pachetul `hub`).
        self.assertIn("/var/lib/studio-harness/app/team_hub.py", unit)
        self.assertIn("WorkingDirectory=/var/lib/studio-harness/app", unit)
        self.assertIn("--listen 127.0.0.1 --port 34880 --state-dir /var/lib/studio-harness", unit)
        self.assertNotIn("/opt/studio-harness", unit)
        self.assertIn("User=studio-harness", unit)
        self.assertIn("ReadWritePaths=/var/lib/studio-harness", unit)
        installer = read(DEPLOY / "install.sh")
        self.assertIn('APP_DIR="/var/lib/studio-harness/app"', installer)
        self.assertIn('STATE_DIR="/var/lib/studio-harness"', installer)
        self.assertIn('install -d -m 0700 -o studio-harness -g studio-harness "$STATE_DIR"', installer)
        # 1.0: team_hub.py importă și project_map (workspace_meta); installer-ul și imaginile copiază toate modulele importate.
        modules = local_modules(HUB_SCRIPT)
        self.assertLessEqual({"claims", "local_state", "project_map", "updater"}, set(modules))
        copied = re.search(r"^for file in (team_hub\.py[^;]*); do$", installer, re.MULTILINE)
        self.assertIsNotNone(copied, "install.sh copiază modulele hub-ului cu `for file in team_hub.py ...; do`")
        self.assertEqual(set(copied.group(1).split()), {"team_hub.py", *(name + ".py" for name in modules)})
        self.assertIn('"$REPO/scripts/$file" "$APP_DIR/$file"', installer)
        self.assertIn("update-channel.json", installer)
        # Panoul web ajunge în $APP_DIR/panel/index.html, de unde team_hub.py îl servește la /panel.
        self.assertIn('"$APP_DIR/panel"', installer)
        self.assertIn('"$REPO/panel/index.html" "$APP_DIR/panel/index.html"', installer)
        for dockerfile in (DEPLOY / "Dockerfile", ROOT / "Dockerfile"):
            text = read(dockerfile)
            for name in ["team_hub", *modules]:
                self.assertIn("scripts/" + name + ".py", text, str(dockerfile))
            self.assertIn("COPY update-channel.json ./", text)
            self.assertIn("COPY panel/index.html ./panel/", text)
        self.assertIn("/healthz", read(DEPLOY / "Dockerfile"))

    def test_installer_creates_the_admin_code_and_explains_the_approval_flow(self):
        installer = read(DEPLOY / "install.sh")
        self.assertIn('ADMIN_TOKEN_FILE="$STATE_DIR/hub-admin-token"', installer)
        # Așteaptă fișierul creat de hub după pornirea serviciului și afișează doar calea lui, niciodată conținutul.
        self.assertLess(installer.index("systemctl enable --now"), installer.index('-s "$ADMIN_TOKEN_FILE"'))
        self.assertIn("sudo cat $STATE_DIR/hub-admin-token", installer)
        self.assertNotIn('cat "$ADMIN_TOKEN_FILE"', installer)
        self.assertIn("STUDIO_HARNESS_ADMIN_TOKEN", installer)
        # Explică fluxul 1.0: dispozitivele apar „în așteptare”, adminul le aprobă din panou (Dispozitive) sau cu --approve-pending.
        self.assertIn("Dispozitive", installer)
        self.assertIn("--approve-pending", installer)
        self.assertIn("--open-enrollment", installer)
        self.assertIn("/roblox/harness/panel", installer)
        self.assertIn("https://lostcube.pro/roblox/harness", installer)
        self.assertIn("config.json", installer)
        self.assertIn("STUDIO_HARNESS_HUB_URL", installer)
        self.assertIn("--listen $LISTEN", installer)
        self.assertIn("--port $PORT", installer)
        for term in TEAM_ERA_TERMS:
            self.assertNotIn(term, installer)

    def test_reverse_proxies_installer_and_channel_point_at_lostcube(self):
        # Hub-ul găzduit rulează pe lostcube.pro sub /roblox/harness/ și servește canalul de actualizare la /releases/ din $APP_DIR/releases.
        caddy = read(DEPLOY / "Caddyfile")
        self.assertIn("lostcube.pro {", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:34880", caddy)
        # Proxy-ul scoate prefixul (handle_path / proxy_pass cu `/`), iar /roblox/harness trimite la panou.
        self.assertIn("handle_path /roblox/harness/* {", caddy)
        self.assertIn("redir /roblox/harness /roblox/harness/panel 302", caddy)
        self.assertIn("response_header_timeout 90s", caddy)
        self.assertEqual(caddy.count("{"), caddy.count("}"))
        nginx = read(DEPLOY / "nginx-hub.conf")
        self.assertIn("server_name lostcube.pro;", nginx)
        self.assertIn("/etc/letsencrypt/live/lostcube.pro/", nginx)
        self.assertIn("location /roblox/harness/ {", nginx)
        self.assertIn("proxy_pass http://127.0.0.1:34880/;", nginx)
        self.assertIn("location = /roblox/harness { return 302 /roblox/harness/panel; }", nginx)
        self.assertIn("proxy_read_timeout 90s;", nginx)
        self.assertEqual(nginx.count("{"), nginx.count("}"))
        self.assertEqual(team_hub.BASE_PATH, "/roblox/harness")
        self.assertEqual(team_hub.strip_base("/roblox/harness/hub/status"), "/hub/status")
        # Panoul servit sub prefix cere /hub/... relativ la pagina lui, nu de la rădăcina domeniului.
        panel = read(ROOT / "panel" / "index.html")
        self.assertIn("BASE_PATH", panel)
        self.assertNotIn('fetch("/', panel)
        self.assertNotIn("fetch('/", panel)
        installer = read(DEPLOY / "install.sh")
        self.assertIn('"$APP_DIR/releases"', installer)
        self.assertIn('"$REPO"/releases/*', installer)
        self.assertIn('"$APP_DIR/releases/$(basename "$file")"', installer)
        self.assertIn("-o studio-harness -g studio-harness", installer[installer.index('"$APP_DIR/releases"'):][:400])
        self.assertLess(installer.index('"$APP_DIR/releases"'), installer.index("systemctl daemon-reload"))
        channel = json.loads(read(ROOT / "update-channel.json"))
        self.assertEqual(channel["manifest_url"], "https://lostcube.pro/roblox/harness/releases/manifest.json")
        readme = read(DEPLOY / "README.md")
        self.assertIn("https://lostcube.pro/roblox/harness/releases/", readme)
        self.assertIn("upstream_manifest_url", readme)

    def test_dockerfiles_take_the_admin_code_from_the_environment_without_embedding_it(self):
        self.assertEqual(team_hub.ADMIN_ENV, "STUDIO_HARNESS_ADMIN_TOKEN")
        space = read(ROOT / "Dockerfile")
        self.assertIn("STUDIO_HARNESS_ADMIN_TOKEN", space)
        self.assertIn("STUDIO_HARNESS_STATE_DIR=/home/hub/state", space)
        self.assertIn("EXPOSE 7860", space)
        self.assertIn('"--port", "7860", "--state-dir", "/home/hub/state"', space)
        docker = read(DEPLOY / "Dockerfile")
        self.assertIn("STUDIO_HARNESS_ADMIN_TOKEN", docker)
        self.assertIn("hub-admin-token", docker)
        self.assertIn("--approve-pending", docker)
        self.assertIn('VOLUME ["/data"]', docker)
        self.assertIn('"--port", "34880", "--state-dir", "/data"', docker)
        for text in (space, docker):
            # Codul vine din Secret/-e la rulare; nicio valoare în imagine și niciun rest din 0.8.
            self.assertNotRegex(text, r"STUDIO_HARNESS_ADMIN_TOKEN\s*=")
            self.assertIn("USER ", text)
            for term in TEAM_ERA_TERMS:
                self.assertNotIn(term, text)

    def test_readme_documents_the_persistent_state_and_the_no_gap_restart(self):
        # Starea hub-ului supraviețuiește repornirii, iar pe Linux update-ul se aplică prin execv cu socketul moștenit (contract §3.8).
        readme = read(DEPLOY / "README.md")
        self.assertIn("hub-state.json", readme)
        self.assertIn("--inherit-socket", readme)
        self.assertIn("execv", readme)
        unit = read(DEPLOY / "studio-harness-hub.service")
        # Relansarea prin execv rămâne în același unit: starea și socketul nu au nevoie de drepturi noi.
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ReadWritePaths=/var/lib/studio-harness", unit)
        self.assertIn("hub-admin-token", unit)
        for term in TEAM_ERA_TERMS:
            self.assertNotIn(term, unit)

    def test_installer_is_valid_bash(self):
        installer = read(DEPLOY / "install.sh")
        self.assertTrue(installer.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("set -euo pipefail", installer)
        self.assertNotIn("\r", installer)
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash indisponibil")
        result = subprocess.run([bash, "-n", str(DEPLOY / "install.sh")], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reverse_proxies_have_no_team_era_leftovers(self):
        for name in ("Caddyfile", "nginx-hub.conf"):
            text = read(DEPLOY / name)
            self.assertIn("/hub/claims/wait", text)
            for term in TEAM_ERA_TERMS:
                self.assertNotIn(term, text, name)


class LauncherTests(unittest.TestCase):
    """Lansatoarele Windows 1.0: Start-Hub.cmd (în locul lui Start-Team-Hub.cmd), Start-Daemon.cmd; Setup-Team a dispărut."""

    def test_team_era_launchers_and_scripts_are_gone(self):
        for name in ("Setup-Team.cmd", "Start-Team-Hub.cmd", "scripts/setup-team.ps1"):
            self.assertFalse((ROOT / name).exists(), name)

    def test_start_hub_launcher_runs_the_hub_and_forwards_its_flags(self):
        raw = (ROOT / "Start-Hub.cmd").read_bytes()
        self.assertTrue(raw.isascii(), "Start-Hub.cmd rulează în cmd.exe: doar ASCII")
        text = raw.decode("ascii")
        self.assertTrue(text.startswith("@echo off\n"))
        self.assertIn('cd /d "%~dp0"', text)
        # %*: Start-Hub.cmd --show-admin-code / --approve-pending / --open-enrollment ajung la team_hub.py.
        self.assertIn("python -u scripts\\team_hub.py %*", text)
        for flag in ("--show-admin-code", "--approve-pending", "--open-enrollment"):
            self.assertIn(flag, text)
        # Hub-ul ascultă implicit pe loopback (team_hub.main), iar launcher-ul spune cum se cere expunerea în rețea.
        self.assertIn("Implicit hub-ul asculta doar pe 127.0.0.1", text)
        self.assertIn("--listen 0.0.0.0", text)
        self.assertIn("hub-admin-token", text)
        self.assertIn("config.json", text)
        self.assertIn("STUDIO_HARNESS_HUB_URL", text)
        self.assertIn("https://lostcube.pro/roblox/harness", text)
        for term in TEAM_ERA_TERMS:
            self.assertNotIn(term, text)

    def test_start_daemon_launcher_runs_the_bridge_without_team_configuration(self):
        raw = (ROOT / "Start-Daemon.cmd").read_bytes()
        self.assertTrue(raw.isascii(), "Start-Daemon.cmd rulează în cmd.exe: doar ASCII")
        text = raw.decode("ascii")
        self.assertTrue(text.startswith("@echo off\n"))
        self.assertIn('cd /d "%~dp0"', text)
        self.assertIn("python -u scripts\\studio_bridge.py %*", text)
        self.assertIn("--no-hub", text)
        for term in TEAM_ERA_TERMS:
            self.assertNotIn(term, text)

    def test_gitignore_keeps_every_state_file_out_of_the_repo(self):
        rules = {line.strip() for line in read(ROOT / ".gitignore").splitlines() if line.strip() and not line.startswith("#")}
        for name in ("local-token", "device-token", "hub-admin-token", "hub-state.json", "*.token", ".env", ".env.*", "*.log", "release/", ".runtime/"):
            self.assertIn(name, rules)
        # config.json (hub_url pentru self-hosting) nu este secret și nu există în repo: nu se ignoră global.
        self.assertNotIn("config.json", rules)
        for name in ("local-token", "device-token", "hub-admin-token", "hub-state.json", "team.json", "team-token"):
            self.assertFalse((ROOT / name).exists(), name + " nu trebuie să existe în repo")


class HubCliTests(unittest.TestCase):
    """CLI-ul hub-ului ca proces separat, cum îl pornesc systemd/docker/Start-Hub.cmd, cu stare temporară și porturi libere."""

    def setUp(self):
        self.temp = temp_dir()
        self.state = Path(self.temp.name) / "state"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, environ=None):
        result = subprocess.run([sys.executable, "-u", str(HUB_SCRIPT), "--state-dir", str(self.state), *args], cwd=str(ROOT),
                                env=hub_environment(environ), stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")

    def admin_code(self):
        return (self.state / "hub-admin-token").read_text(encoding="utf-8").strip()

    def test_admin_code_from_the_environment_must_have_16_to_512_characters(self):
        code, out, err = self.run_cli("--port", str(free_port()), environ={"STUDIO_HARNESS_ADMIN_TOKEN": "scurt"})
        self.assertNotEqual(code, 0)
        self.assertIn("16-512", err)
        # Env-ul înlocuiește fișierul: un cod refuzat nu creează nimic în directorul de stare.
        self.assertFalse((self.state / "hub-admin-token").exists())
        self.assertNotIn("scurt", out)

    def test_approve_pending_edits_the_state_file_when_the_hub_is_stopped(self):
        pending = {"device_id": DEVICE_ID, "token_hash": hashlib.sha256(DEVICE_TOKEN.encode("utf-8")).hexdigest(), "roblox_user_id": 101,
                   "roblox_name": "ana", "machine": "pc-ana", "bridge_id": "b", "version": team_hub.VERSION, "status": "pending",
                   "first_seen": 1.0, "last_seen": 1.0, "approved_at": 0.0, "approved_by": "", "workspace": None}
        approved = dict(pending, device_id="0123456789abcdef", token_hash="f" * 64, roblox_name="dan", machine="pc-dan", status="approved",
                        approved_at=1.0, approved_by="admin")
        state = {"version": team_hub.STATE_VERSION, "hub_version": team_hub.VERSION, "saved": 1.0, "hub_id": "hub-de-test", "enrollment": "approve",
                 "devices": {pending["device_id"]: pending, approved["device_id"]: approved}, "workspaces": {}, "sessions": {}, "claims": {},
                 "journal": [], "journal_seq": 0}
        self.state.mkdir(parents=True)
        (self.state / team_hub.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        port = free_port()  # nimeni nu ascultă: CLI-ul editează direct hub-state.json
        code, out, err = self.run_cli("--port", str(port), "--approve-pending")
        self.assertEqual(code, 0, err)
        self.assertIn("1 dispozitive aprobate direct în hub-state.json", out)
        saved = json.loads((self.state / team_hub.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual((saved["devices"][DEVICE_ID]["status"], saved["devices"][DEVICE_ID]["approved_by"]), ("approved", "cli"))
        self.assertEqual(saved["devices"]["0123456789abcdef"], approved)
        self.assertGreater(saved["devices"][DEVICE_ID]["approved_at"], 1.0)
        # Codul de administrator a fost creat pentru CLI, dar nu apare în ieșire.
        self.assertGreaterEqual(len(self.admin_code()), 16)
        self.assertNotIn(self.admin_code(), out + err)

    def test_running_hub_shows_the_code_on_request_and_approves_pending_devices_from_the_cli(self):
        port = free_port()
        with HubProcess(self.state, port, "--show-admin-code") as hub:
            admin = self.admin_code()
            status, body, _ = call(hub.base, "/hub/status", admin=admin)
            self.assertEqual((status, body["ok"], body["admin"], body["device"], body["enrollment"]), (200, True, True, None, "approve"))
            self.assertEqual(call(hub.base, "/hub/status", admin="alt-cod-0123456789abcdef")[0], 401)
            # Un daemon nou se înregistrează și rămâne în așteptare.
            status, body, _ = call(hub.base, "/hub/register", REGISTER_BODY, device=DEVICE_TOKEN)
            self.assertEqual((status, body["status"], body["device_id"], body["enrollment"]), (202, "pending", DEVICE_ID, "approve"))
            # Aprobarea din terminal (install.sh o recomandă): --approve-pending vorbește cu hub-ul în execuție, cu codul din fișier.
            code, out, err = self.run_cli("--port", str(port), "--approve-pending")
            self.assertEqual(code, 0, err)
            self.assertIn("1 dispozitive aprobate prin hub-ul în execuție (port " + str(port) + ")", out)
            self.assertNotIn(admin, out + err)
            status, body, _ = call(hub.base, "/hub/status", device=DEVICE_TOKEN)
            self.assertEqual((status, body["device"]["status"]), (200, "approved"))
            self.assertEqual(call(hub.base, "/hub/workspaces", device=DEVICE_TOKEN)[0], 200)
            # Cu un cod greșit hub-ul în execuție refuză, iar CLI-ul iese cu 1 fără să atingă hub-state.json.
            code, out, err = self.run_cli("--port", str(port), "--approve-pending", environ={"STUDIO_HARNESS_ADMIN_TOKEN": "alt-cod-0123456789abcdef"})
            self.assertEqual(code, 1)
            self.assertIn("HTTP 401", out)
        output = hub.output
        self.assertIn("Studio Harness hub " + team_hub.VERSION + " ascultă pe http://127.0.0.1:" + str(port), output)
        self.assertIn("Cod de administrator (pentru panou și --approve-pending): " + admin, output)
        self.assertIn("înrolare: approve", output)
        self.assertIn("dispozitiv nou în așteptare: ana @ pc-ana (" + DEVICE_ID + ") (daemon " + team_hub.VERSION + ")", output)
        self.assertIn("dispozitiv aprobat de admin: ana @ pc-ana (" + DEVICE_ID + ")", output)
        self.assertNotIn(DEVICE_TOKEN, output)
        self.assertNotIn("team.json", output)

    def test_open_enrollment_approves_new_devices_and_the_code_stays_out_of_the_log(self):
        port = free_port()
        with HubProcess(self.state, port, "--open-enrollment") as hub:
            status, body, _ = call(hub.base, "/hub/register", REGISTER_BODY, device=DEVICE_TOKEN)
            self.assertEqual((status, body["status"], body["device_id"], body["enrollment"]), (200, "approved", DEVICE_ID, "open"))
            status, body, _ = call(hub.base, "/hub/status", device=DEVICE_TOKEN)
            self.assertEqual((status, body["device"]["status"], body["enrollment"]), (200, "approved", "open"))
        admin = self.admin_code()
        output = hub.output
        self.assertIn("Codul de administrator: " + str(self.state / "hub-admin-token") + " (pornește cu --show-admin-code ca să îl afișezi)", output)
        self.assertNotIn(admin, output)
        self.assertIn("înrolare: open (dispozitivele noi sunt aprobate automat)", output)
        self.assertIn("dispozitiv aprobat automat (înrolare deschisă): ana @ pc-ana (" + DEVICE_ID + ")", output)
        self.assertNotIn(DEVICE_TOKEN, output)


if __name__ == "__main__":
    unittest.main()
