# Hub-ul central Studio Harness pe server Ubuntu

Hub-ul (`scripts/team_hub.py`, versiunea 1.0.0) este Python 3.10+ fără dependențe externe. Ține dispozitivele aprobate, workspace-urile (un workspace = un joc Roblox), sesiunile agenților, claims-urile, jurnalul și harta proiectelor, și servește panoul web la `/panel`, canalul de actualizări la `/releases/` și `/healthz`. Starea este persistată în `/var/lib/studio-harness/hub-state.json`, deci o repornire nu pierde aprobările dispozitivelor.

Pe server **nu ajunge nicio credențială** Claude, ChatGPT sau Roblox. Singurul secret este **codul de administrator**, creat de hub la prima pornire. Developerii nu au niciun token de pus: PC-ul fiecăruia își generează local un token de dispozitiv, iar adminul aprobă dispozitivul o singură dată din panou.

## Instalare ca serviciu systemd

Pe server, cu repo-ul sau pachetul copiat (de exemplu prin `git clone` sau `scp`):

```bash
sudo bash deploy/ubuntu/install.sh          # în pachetul de hosting: sudo bash deploy/install.sh
sudo cat /var/lib/studio-harness/hub-admin-token
```

Scriptul creează utilizatorul de sistem `studio-harness`, copiază în `/var/lib/studio-harness/app` modulele hub-ului (`team_hub.py`, `claims.py`, `local_state.py`, `project_map.py`, `updater.py`), `update-channel.json`, `panel/index.html` și, dacă vin cu pachetul, `releases/`. Starea stă în `/var/lib/studio-harness` (0700, doar utilizatorul serviciului o poate citi), iar serviciul `studio-harness-hub.service` pornește cu izolare systemd (`ProtectSystem=strict`, `NoNewPrivileges`, fără acces la `/home`).

Implicit hub-ul ascultă doar pe `127.0.0.1:34880` (implicitul din `team_hub.py`, păstrat de unitul systemd), ca să fie expus exclusiv prin HTTPS. Pentru un server aflat doar în LAN, fără proxy: `sudo bash deploy/ubuntu/install.sh --public` și `sudo ufw allow 34880/tcp`; în acest caz hub-ul scrie la fiecare pornire linia „atenție: hub-ul ascultă în rețea pe HTTP simplu (0.0.0.0); folosește-l doar în spatele unui proxy HTTPS”, iar avertismentul rămâne valabil: fără TLS în față, codurile de dispozitiv circulă în clar.

Starea (`hub-state.json` și fișierul temporar `.part`) este scrisă cu permisiuni `0600`, pe lângă directorul `0700`: un hub pornit manual, cu alt `--state-dir`, nu lasă hash-urile tokenurilor lizibile sub umask-ul implicit 022.

## HTTPS (obligatoriu pentru orice acces din afara LAN-ului)

Alege una dintre variante și pune un record DNS A/AAAA către server.

**Caddy** (certificat automat):

```bash
sudo apt install caddy
sudo cp deploy/ubuntu/Caddyfile /etc/caddy/Caddyfile   # editează domeniul
sudo systemctl reload caddy
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

**nginx + certbot**:

```bash
sudo apt install nginx certbot python3-certbot-nginx
sudo cp deploy/ubuntu/nginx-hub.conf /etc/nginx/sites-available/studio-harness-hub   # editează domeniul
sudo ln -s /etc/nginx/sites-available/studio-harness-hub /etc/nginx/sites-enabled/
sudo certbot --nginx -d lostcube.pro
sudo nginx -t && sudo systemctl reload nginx
```

Ambele configurații trimit calea neatinsă către hub și lasă timeout-ul de citire la 90 s, pentru că `POST /hub/claims/wait` poate ține conexiunea deschisă până la 60 s. Dacă preferi hub-ul sub o cale (`https://domeniu/prefix/…`), pune-l acolo în proxy: hub-ul acceptă calea și cu prefix, și fără.

Adrese publice după instalare:

| Adresă | Rol |
| --- | --- |
| `https://lostcube.pro/panel` | panoul web (pagina este publică; datele cer un dispozitiv aprobat sau codul de administrator) |
| `https://lostcube.pro/healthz` | verificare, fără antete |
| `https://lostcube.pro/releases/manifest.json` | canalul de actualizări |
| `https://lostcube.pro/hub/...` | API-ul dispozitivelor și al adminului |
| `https://lostcube.pro/hub/avatar?user=<id>` | avatarul Roblox pentru panou (public, fără antete; 204 la eșec) |

## Verificări

```bash
curl -s https://lostcube.pro/healthz
# {"ok": true, "version": "1.0.0"}

curl -s -o /dev/null -w "%{http_code}\n" https://lostcube.pro/hub/status
# 401 — fără antete, corect ({"ok": false, "error": "...", "status": "unknown"})

curl -s -H "X-Studio-Harness-Admin: $(sudo cat /var/lib/studio-harness/hub-admin-token)" \
  https://lostcube.pro/hub/status
# {"ok": true, "version": "1.0.0", "hub_id": "...", "enrollment": "approve", "admin": true, "device": null, ...}

curl -s -H "X-Studio-Harness-Admin: $(sudo cat /var/lib/studio-harness/hub-admin-token)" \
  https://lostcube.pro/hub/admin/devices
# {"ok": true, "devices": [{"device_id": "...", "status": "pending", ...}]}
```

Rutele de dispozitiv folosesc antetul `X-Studio-Harness-Device` (tokenul de 64 hex al PC-ului, niciodată tastat de om), iar rutele de administrator `X-Studio-Harness-Admin`. Un dispozitiv neaprobat primește 403 cu `status: "pending"`.

## Canalul de actualizări servit de hub

Hub-ul servește `https://lostcube.pro/releases/manifest.json` și pachetele din `/var/lib/studio-harness/app/releases/`, fără antete. `install.sh` le copiază din `releases/` al pachetului, dacă există. Când `update-channel.json` are un `upstream_manifest_url` (GitHub sau Hugging Face) diferit de `manifest_url` (lostcube.pro), hub-ul se actualizează primul din upstream și oglindește pachetele noi în `releases/`, iar daemon-ii developerilor se actualizează de la hub.

## Actualizarea unui hub deja instalat

Aceiași pași ca la instalare: `install.sh` este idempotent, nu atinge starea (`hub-state.json`, `hub-admin-token`, dispozitivele aprobate) și repornește serviciul.

```bash
cd /tmp
BASE=https://raw.githubusercontent.com/Ombra-Studios/roblox-studio-harness/main/releases
curl -fsSLO "$BASE/studio-harness-hub-1.0.0-ubuntu.zip"
curl -fsSLO "$BASE/studio-harness-hub-1.0.0-ubuntu.zip.sha256"
sha256sum -c studio-harness-hub-1.0.0-ubuntu.zip.sha256
unzip -oq studio-harness-hub-1.0.0-ubuntu.zip
sudo bash studio-harness-hub-1.0.0/deploy/install.sh
curl -fsS https://<domeniul-vostru>/healthz     # trebuie să arate {"ok": true, "version": "1.0.0"}
```

**De ce contează versiunea:** 0.8 avea rutele `/team/*` și cerea un token de echipă; 1.0 are `/hub/*`, dispozitive aprobate și workspace-uri. Un daemon 1.0 care întâlnește un hub 0.8 la aceeași adresă primește 404, 405 sau 501, rămâne `offline` cu mesajul „Hub-ul rulează o versiune mai veche.” și lucrează mai departe cu claims locale. Invers, un daemon 0.8 lăsat pe un hub 1.0 rămâne solo.

După prima pornire pe 1.0, hub-ul se actualizează singur din `upstream_manifest_url` și oglindește pachetele în `releases/`, deci actualizările următoare nu mai cer pași manuali.

## Cum intră developerii

Nu au nimic de configurat și nu primesc niciun token:

1. Developerul instalează pluginul Studio Harness pe PC-ul lui (`Install-Studio-Plugin.cmd`) și deschide Roblox Studio.
2. Daemon-ul lui local se înregistrează singur la hub cu tokenul de dispozitiv generat pe acel PC; dispozitivul apare în panou ca **în așteptare**.
3. Adminul îl aprobă o singură dată:
   - din panou: intră cu codul de administrator, fila **Dispozitive** → **Aprobă**;
   - din terminalul serverului:

     ```bash
     sudo -u studio-harness python3 /var/lib/studio-harness/app/team_hub.py \
       --state-dir /var/lib/studio-harness --port 34880 --approve-pending
     ```

     Comanda încearcă întâi hub-ul în execuție prin loopback (`127.0.0.1:34880`) și, doar dacă hub-ul nu răspunde deloc, editează direct `hub-state.json`. Dacă hub-ul răspunde, dar refuză codul de administrator, nu schimbă nimic și iese cu codul 1. La fel dacă răspunsul nu este JSON (un proxy intermediar pus din greșeală pe loopback, un corp trunchiat): comanda scrie „Hub-ul în execuție a răspuns neinteligibil; nu editez starea sub un hub activ.” și iese cu 1, fără să atingă starea.
4. Din acel moment developerul apare online în workspace-ul jocului deschis, iar claims-urile lui sunt comune cu ale colegilor.

**Înrolare deschisă** (aprobare automată, doar în rețele de încredere): din panou, fila Dispozitive, sau porniți serviciul cu `--open-enrollment` în `ExecStart` (forțează modul la fiecare pornire). Modul se salvează în `hub-state.json`.

**Alt domeniu decât cel implicit**: daemon-ul se conectează implicit la `https://lostcube.pro`. Pentru un hub propriu, fiecare developer pune pe PC-ul lui, în `%LOCALAPPDATA%\StudioHarness\config.json`:

```json
{"hub_url": "https://<domeniul-vostru>"}
```

sau setează variabila `STUDIO_HARNESS_HUB_URL`. Se acceptă `https://` către orice gazdă și `http://` doar către `127.0.0.1`/`localhost` (hub de test pe același PC); o valoare respinsă lasă daemon-ul în starea `disabled`, fără să trimită date altundeva.

## Operare

- **Jurnal**: `journalctl -u studio-harness-hub -f` — pornire, înregistrări de dispozitive, aprobări, revocări, dispozitive offline, proiecte primite, erori. Niciodată coduri sau tokenuri; codul de administrator se afișează doar la pornire cu `--show-admin-code`.
- **Repornire / oprire**: `sudo systemctl restart studio-harness-hub`, `sudo systemctl stop studio-harness-hub`.
- **Codul de administrator**: creat la prima pornire în `/var/lib/studio-harness/hub-admin-token`. Pentru un cod ales de tine (dintr-un manager de secrete), pune-l în unit ca `Environment=STUDIO_HARNESS_ADMIN_TOKEN=<cod>` (16–512 de caractere, fără spații) și repornește serviciul; fișierul nu mai este folosit.
- **Rotirea codului**: `sudo rm /var/lib/studio-harness/hub-admin-token && sudo systemctl restart studio-harness-hub`, apoi transmite noul cod pe un canal sigur. Dispozitivele aprobate nu sunt afectate: codul de administrator nu este folosit de daemon-i.
- **Revocare**: din panou, fila Dispozitive → **Revocă** (sesiunile și claims-urile dispozitivului se eliberează imediat) sau **Uită** (dispozitivul dispare și poate reveni ca „în așteptare”).
- **Actualizare**: automată, în fiecare oră, din canalul `update-channel.json` (pachetul `hub` din manifest, verificat SHA256, aplicat în `/var/lib/studio-harness/app`). Pe Linux hub-ul salvează starea și se relansează singur prin `execv`, cu socketul de ascultare moștenit (`--inherit-socket`): fără gol de port și fără reînregistrarea daemon-ilor. Pe Windows se închide și este repornit de systemd, docker sau `Start-Hub.cmd`. Manual: rulează din nou `install.sh`. Dezactivare: `--no-auto-update` în unit.
- **Stare persistentă**: `/var/lib/studio-harness/hub-state.json` (versiunea 2: `hub_id`, `hub_version`, `saved`, `journal_seq`, modul de înrolare, dispozitivele cu amprenta tokenului, workspace-urile cu sumarul proiectului, sesiunile cu ultimele 256 de evenimente, claims-urile pe workspace, jurnalul ≤ 1000 de intrări), scrisă atomic la 30 s și la oprire sau înaintea unei actualizări. La pornire este încărcată dacă are versiunea 2; altfel hub-ul pornește curat, păstrează `hub_id` dacă este valid și scrie motivul în jurnal. Fișierul nu conține codul de administrator și nici tokenuri de dispozitiv în clar.
- **Dezinstalare**: `sudo systemctl disable --now studio-harness-hub && sudo rm /etc/systemd/system/studio-harness-hub.service && sudo rm -r /var/lib/studio-harness && sudo userdel studio-harness`.

## Docker (alternativă)

```bash
docker build -f deploy/ubuntu/Dockerfile -t studio-harness-hub .
docker run -d --name studio-harness-hub --restart unless-stopped \
  -p 127.0.0.1:34880:34880 -v studio-harness-hub:/data studio-harness-hub
docker exec studio-harness-hub cat /data/hub-admin-token
```

Portul este legat doar pe `127.0.0.1`; pune Caddy sau nginx în față, ca mai sus. Pentru un cod ales de tine: `docker run ... -e STUDIO_HARNESS_ADMIN_TOKEN ...` (variabila din gazdă, 16–512 de caractere, fără spații). Aprobare din container:

```bash
docker exec studio-harness-hub python3 team_hub.py --state-dir /data --approve-pending
```

`Dockerfile`-ul din rădăcina repo-ului rulează același hub ca Space Docker pe Hugging Face (port 7860, starea în `/home/hub/state` prin `STUDIO_HARNESS_STATE_DIR`), cu codul de administrator din Secretul `STUDIO_HARNESS_ADMIN_TOKEN` și cu auto-update-ul oprit (`STUDIO_HARNESS_AUTO_UPDATE=0`): Space-ul se reconstruiește la fiecare publicare.

## Limite

- Starea este salvată la 30 s: o oprire bruscă pierde cel mult ultimele 30 de secunde (claims noi, intrări de jurnal). La o repornire normală sau la o actualizare, nimic nu se pierde.
- Un dispozitiv fără sync de 60 s este marcat offline: sesiunile lui neterminale devin `lost` și claims-urile lui se eliberează. Peste internet, o întrerupere mai lungă de un minut are acest efect; la revenire, daemon-ul retrimite sesiunile.
- Prezența „online” într-un workspace înseamnă un sync în ultimele 15 secunde.
- Hub-ul este un singur proces, fără bază de date. Trafic: cereri JSON mici la 2–5 s de la fiecare PC de developer; consum de ordinul zecilor de MB RAM.
- Identitatea Roblox afișată lângă fiecare sesiune este informativă (raportată de plugin), nu o credențială verificată de hub. Autorizarea vine exclusiv din tokenul de dispozitiv aprobat.
