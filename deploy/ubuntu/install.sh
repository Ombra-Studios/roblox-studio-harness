#!/usr/bin/env bash
# Instalează hub-ul central Studio Harness 1.0 ca serviciu systemd pe Ubuntu (22.04+ / 24.04+).
# Rulează ca root din directorul repo-ului sau al pachetului: sudo bash deploy/ubuntu/install.sh [--public] [--port 34880]
set -euo pipefail

LISTEN="127.0.0.1"
PORT="34880"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --public) LISTEN="0.0.0.0"; shift ;;
    --port) PORT="${2:?--port cere o valoare}"; shift 2 ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "Opțiune necunoscută: $1" >&2; exit 2 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Rulează cu sudo." >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Funcționează atât din repo (deploy/ubuntu/ + scripts/) cât și din pachetul de hosting (deploy/ + scripts/).
if [[ -f "$HERE/../scripts/team_hub.py" ]]; then
  REPO="$(cd "$HERE/.." && pwd)"
else
  REPO="$(cd "$HERE/../.." && pwd)"
fi
APP_DIR="/var/lib/studio-harness/app"
STATE_DIR="/var/lib/studio-harness"
SERVICE="studio-harness-hub"
ADMIN_TOKEN_FILE="$STATE_DIR/hub-admin-token"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Instalez python3..."
  apt-get update -qq && apt-get install -y -qq python3
fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Este necesar Python 3.10 sau mai nou (Ubuntu 22.04+).")
PY

if ! id -u studio-harness >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin studio-harness
fi

# Starea (hub-state.json, hub-admin-token, backups/) este doar a serviciului; aplicația este scriibilă de serviciu, ca
# auto-update-ul să poată aplica pachetul `hub`.
install -d -m 0700 -o studio-harness -g studio-harness "$STATE_DIR"
install -d -m 0755 -o studio-harness -g studio-harness "$APP_DIR"
# Modulele Python ale hub-ului (team_hub.py importă claims, local_state, project_map și updater).
for file in team_hub.py claims.py local_state.py project_map.py updater.py; do
  install -m 0644 -o studio-harness -g studio-harness "$REPO/scripts/$file" "$APP_DIR/$file"
done
for file in update-channel.json; do
  if [[ -f "$REPO/$file" ]]; then install -m 0644 -o studio-harness -g studio-harness "$REPO/$file" "$APP_DIR/$file"; fi
done
# Panoul web, servit de hub la /panel (un singur fișier, fără CDN); hub-ul îl citește la fiecare cerere.
install -d -m 0755 -o studio-harness -g studio-harness "$APP_DIR/panel"
if [[ -f "$REPO/panel/index.html" ]]; then
  install -m 0644 -o studio-harness -g studio-harness "$REPO/panel/index.html" "$APP_DIR/panel/index.html"
else
  echo "Atenție: panel/index.html lipsește; /panel va răspunde 404 până la instalarea panoului." >&2
fi
install -m 0644 -o studio-harness -g studio-harness "$HERE/README.md" "$APP_DIR/README.md"
# Canalul de actualizare servit de hub la https://lostcube.pro/roblox/harness/releases/ (pachetele + manifest.json), dacă vin cu pachetul.
if [[ -d "$REPO/releases" ]]; then
  install -d -m 0755 -o studio-harness -g studio-harness "$APP_DIR/releases"
  for file in "$REPO"/releases/*; do
    [[ -f "$file" ]] && install -m 0644 -o studio-harness -g studio-harness "$file" "$APP_DIR/releases/$(basename "$file")"
  done
fi

UNIT="/etc/systemd/system/$SERVICE.service"
sed -e "s|--listen 127.0.0.1|--listen $LISTEN|" -e "s|--port 34880|--port $PORT|" \
  "$HERE/$SERVICE.service" > "$UNIT"
systemctl daemon-reload
systemctl enable --now "$SERVICE"

# Hub-ul creează codul de administrator la prima pornire (hub-admin-token, doar în directorul de stare), în afară de cazul
# în care unit-ul îl dă prin Environment=STUDIO_HARNESS_ADMIN_TOKEN=...
if ! grep -q "^Environment=STUDIO_HARNESS_ADMIN_TOKEN=" "$UNIT"; then
  for _ in $(seq 1 20); do
    if [[ -s "$ADMIN_TOKEN_FILE" ]]; then break; fi
    sleep 0.5
  done
fi
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "Serviciul nu a pornit; vezi: journalctl -u $SERVICE -n 50" >&2
  exit 1
fi
if command -v curl >/dev/null 2>&1; then
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null && echo "healthz: OK"
fi

echo
echo "Hub instalat și pornit: $SERVICE ascultă pe $LISTEN:$PORT"
if [[ -s "$ADMIN_TOKEN_FILE" ]]; then
  echo "Codul de administrator (pentru login în panou și pentru --approve-pending; nu este o credențială Claude, ChatGPT sau Roblox):"
  echo "  sudo cat $STATE_DIR/hub-admin-token"
else
  echo "Codul de administrator vine din STUDIO_HARNESS_ADMIN_TOKEN (unit-ul systemd); fișierul $STATE_DIR/hub-admin-token nu este folosit."
fi
echo
echo "Cum intră developerii: instalează pluginul Studio Harness și deschid Roblox Studio; daemon-ul lor se înregistrează singur,"
echo "iar fiecare PC apare în panou ca dispozitiv „în așteptare” până îl aprobi (o singură dată per PC):"
echo "  - din panou: lipește codul de administrator la login, fila Dispozitive → „Aprobă”;"
echo "  - din terminal: sudo -u studio-harness python3 $APP_DIR/team_hub.py --state-dir $STATE_DIR --port $PORT --approve-pending"
echo "  - înrolare deschisă (aprobare automată, doar în rețele de încredere): din panou, fila Dispozitive (modul se salvează în hub-state.json),"
echo "    sau --open-enrollment în ExecStart, care forțează modul la fiecare pornire."
echo
if [[ "$LISTEN" == "127.0.0.1" ]]; then
  echo "Hub-ul este accesibil doar local. Pune un reverse proxy HTTPS în față (deploy/ubuntu/Caddyfile sau nginx-hub.conf):"
  echo "  panou:   https://<domeniul-tău>/roblox/harness/panel"
  echo "  healthz: https://<domeniul-tău>/roblox/harness/healthz"
  echo "Daemon-ii se conectează implicit la https://lostcube.pro/roblox/harness. Pentru alt domeniu, fiecare developer pune"
  echo '  {"hub_url": "https://<domeniul-tău>/roblox/harness"} în %LOCALAPPDATA%\StudioHarness\config.json (sau STUDIO_HARNESS_HUB_URL).'
else
  echo "Hub-ul ascultă public pe portul $PORT (HTTP). Recomandat doar în LAN; pentru internet folosește HTTPS prin proxy."
  echo "Firewall: sudo ufw allow $PORT/tcp"
fi
echo "Panou web local: http://$LISTEN:$PORT/panel (pagina este publică; datele cer un dispozitiv aprobat sau codul de administrator)."
echo "Jurnal: journalctl -u $SERVICE -f"
