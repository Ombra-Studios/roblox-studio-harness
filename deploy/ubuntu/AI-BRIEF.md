# Brief pentru asistentul AI care face hosting-ul

Acest fișier este scris pentru un agent AI (Claude Code, Codex sau similar) care primește pachetul `studio-harness-hub` și trebuie să îl instaleze pe un server Ubuntu. Citește-l integral înainte de prima comandă. Detaliile de operare sunt în `README.md` de lângă el.

## Ce este serviciul

- Un singur proces Python 3.10+ fără dependențe externe: `team_hub.py` (versiunea 1.0.0). Este hub-ul central al pluginului Studio Harness pentru Roblox Studio: fiecare developer are pe PC-ul lui un daemon local care se înregistrează la hub, trimite la ~2 s sesiunile agenților lui AI și primește tabla comună (cine lucrează, în ce joc, ce zone ale scenei sunt revendicate, jurnalul modificărilor).
- Hub-ul **persistă starea** în directorul de stare `/var/lib/studio-harness`: `hub-state.json` (dispozitivele aprobate, workspace-urile, sesiunile, claims-urile, jurnalul), `hub-admin-token` (codul de administrator) și `backups/` (copiile făcute la auto-update). Nu are bază de date și nu scrie nimic în afara acestui director și a lui `/var/lib/studio-harness/app`.
- Servește și panoul web la `/panel` (un singur fișier HTML, fără CDN) și canalul de actualizări la `/releases/`.
- Pe server **nu ajung credențiale** Claude, ChatGPT sau Roblox. Singurul secret este **codul de administrator**, creat de hub la prima pornire în `hub-admin-token` (sau dat de tine prin variabila de mediu `STUDIO_HARNESS_ADMIN_TOKEN`). Developerii **nu primesc niciun token**: PC-ul fiecăruia își generează local un token de dispozitiv, iar adminul aprobă dispozitivul din panou.
- Trafic: cereri HTTP JSON mici (câțiva KB) de la fiecare PC de developer, la 2–5 s; `POST /hub/claims/wait` poate ține o conexiune deschisă până la 60 s (de aceea timeout-ul de citire al proxy-ului trebuie să fie ≥ 90 s). Consum neglijabil: câteva zeci de MB RAM.

## Ce trebuie să obții la final

1. `studio-harness-hub.service` activ, pornit la boot, ascultând pe `127.0.0.1:34880`.
2. HTTPS public pe un domeniu (de exemplu `lostcube.pro`) prin Caddy sau nginx, care face proxy către `127.0.0.1:34880` cu calea neatinsă.
3. `curl https://lostcube.pro/healthz` → `{"ok": true, "version": "1.0.0"}`.
4. Codul de administrator transmis persoanei care a trimis pachetul **printr-un canal sigur**, împreună cu URL-ul și cu explicația de la secțiunea „Ce raportezi la final”.

## Pași

```bash
# 1. Adu pachetul pe server. Fie ți-a fost trimis direct, fie îl iei din release-ul public
#    (https://github.com/Ombra-Studios/roblox-studio-harness/releases/latest):
cd /tmp
VERSIUNE=1.0.0
REL="https://github.com/Ombra-Studios/roblox-studio-harness/releases/download/v$VERSIUNE"
curl -fsSLO "$REL/studio-harness-hub-$VERSIUNE-ubuntu.zip"
curl -fsSLO "$REL/studio-harness-hub-$VERSIUNE-ubuntu.zip.sha256"
sha256sum -c "studio-harness-hub-$VERSIUNE-ubuntu.zip.sha256"   # oprește-te dacă nu scrie „OK”
unzip -oq "studio-harness-hub-$VERSIUNE-ubuntu.zip"
cd "studio-harness-hub-$VERSIUNE"
sudo bash deploy/install.sh             # creează utilizatorul, /var/lib/studio-harness/app, serviciul
sudo systemctl status studio-harness-hub --no-pager
curl -s http://127.0.0.1:34880/healthz  # trebuie {"ok": true, "version": "1.0.0"}

# 2. HTTPS — varianta Caddy (recomandată, certificat automat)
sudo apt install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile     # editează domeniul
sudo systemctl reload caddy
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp   # dacă ufw este activ

#    Varianta nginx: deploy/nginx-hub.conf + certbot (vezi README.md)

# 3. Verificare din afară
curl -s https://lostcube.pro/healthz
curl -s -o /dev/null -w "%{http_code}\n" https://lostcube.pro/hub/status   # 401 fără antete = corect

# 4. Codul de administrator
sudo cat /var/lib/studio-harness/hub-admin-token
```

Cerințe prealabile: un record DNS A/AAAA pentru domeniu către server, porturile 80 și 443 deschise. Portul 34880 **nu** se deschide public.

## Reguli de siguranță

- Nu lipi codul de administrator în tichete, chat-uri, commit-uri sau loguri. Transmite-l prin canalul convenit cu solicitantul (manager de parole, mesaj efemer). Hub-ul nu îl scrie în jurnal decât dacă este pornit cu `--show-admin-code`.
- Nu expune portul 34880 în afara serverului. Fără HTTPS, tokenurile de dispozitiv ar circula în clar.
- Nu aproba tu dispozitive și nu porni înrolarea deschisă fără să ți se ceară: aprobarea unui PC este decizia proprietarului hub-ului.
- Nu modifica `team_hub.py`, `claims.py`, `local_state.py`, `project_map.py` sau `updater.py`; dacă ceva nu merge, raportează în loc să „repari” protocolul.
- Nu rula serviciul ca root și nu schimba `User=studio-harness`. Dacă systemd refuză pornirea din cauza izolării, singura linie pe care o poți scoate din unit este `MemoryDenyWriteExecute=true`; raportează că ai făcut-o.
- Dacă vrei un cod de administrator stabil ales de tine (de exemplu dintr-un manager de secrete), pune-l în unit ca `Environment=STUDIO_HARNESS_ADMIN_TOKEN=...` (16–512 de caractere, fără spații) și repornește serviciul. Sub 32 de caractere hub-ul pornește, dar scrie în jurnal „atenție: codul de administrator din STUDIO_HARNESS_ADMIN_TOKEN are sub 32 de caractere; folosește unul generat aleatoriu” — folosește un cod generat aleatoriu, nu o parolă memorabilă.

## Verificări de acceptanță

| Verificare | Așteptat |
| --- | --- |
| `systemctl is-active studio-harness-hub` | `active` |
| `curl -s http://127.0.0.1:34880/healthz` | `{"ok": true, "version": "1.0.0"}` |
| `curl -s https://lostcube.pro/healthz` | același răspuns, prin HTTPS |
| `curl -s -o /dev/null -w "%{http_code}" https://lostcube.pro/hub/status` | `401` |
| `curl -s -H "X-Studio-Harness-Admin: $(sudo cat /var/lib/studio-harness/hub-admin-token)" https://lostcube.pro/hub/status` | `{"ok": true, "version": "1.0.0", "hub_id": "...", "enrollment": "approve", "admin": true, "device": null, "workspaces": 0, "members_online": 0}` |
| `curl -s -o /dev/null -w "%{http_code}" https://lostcube.pro/panel` | `200` (pagina este publică; datele din ea cer un cod) |
| `curl -s -o /dev/null -w "%{http_code}" https://lostcube.pro/releases/manifest.json` | `200` dacă pachetul a venit cu `releases/`, altfel `404` |
| `sudo journalctl -u studio-harness-hub -n 20` | liniile „ascultă pe http://127.0.0.1:34880”, calea codului de administrator și modul de înrolare, fără niciun cod |
| `sudo ss -ltnp \| grep 34880` | legat doar pe `127.0.0.1` |
| `sudo ls -l /var/lib/studio-harness` | `hub-admin-token` și (după primul minut de rulare) `hub-state.json`, drepturi 0700 pentru director |

## Depanare

- **Serviciul nu pornește**: `sudo journalctl -u studio-harness-hub -n 50`. Cauze tipice: Python sub 3.10 (`python3 --version`; Ubuntu 22.04+ este ok), portul 34880 ocupat (`sudo ss -ltnp | grep 34880`), directorul de stare fără drepturi (`sudo chown -R studio-harness:studio-harness /var/lib/studio-harness`), cod de administrator din mediu mai scurt de 16 caractere sau cu spații (hub-ul refuză pornirea).
- **`healthz` merge local, dar nu prin HTTPS**: DNS-ul nu indică serverul, porturile 80/443 închise, sau domeniul din Caddyfile/nginx nu a fost înlocuit. `sudo journalctl -u caddy -n 50` sau `sudo nginx -t`.
- **Developerii văd „Hub offline”**: hub-ul este oprit sau proxy-ul taie conexiunile lungi; timeout-ul de citire al proxy-ului trebuie să fie ≥ 90 s (este deja în fișierele livrate).
- **Developerii rămân „în așteptare”**: este comportamentul implicit până când adminul îi aprobă din panou (fila Dispozitive) sau cu `--approve-pending`. Nu este o eroare. `--approve-pending` iese cu codul 1 (și nu schimbă nimic) dacă hub-ul în execuție refuză codul de administrator sau dacă răspunde cu ceva ce nu este JSON („Hub-ul în execuție a răspuns neinteligibil; nu editez starea sub un hub activ.” — semn că pe loopback răspunde altceva decât hub-ul).
- **Rotirea codului de administrator**: `sudo rm /var/lib/studio-harness/hub-admin-token && sudo systemctl restart studio-harness-hub`, apoi transmite noul cod. Dispozitivele aprobate nu sunt afectate.
- **Actualizare manuală**: dezarhivează noul pachet și rulează din nou `sudo bash deploy/install.sh`; serviciul este repornit, starea și codul se păstrează. (Automat, hub-ul se actualizează singur în fiecare oră din canalul configurat.)

## Referință rapidă a API-ului (doar pentru diagnostic)

Rutele publice, fără antete: `GET /healthz`, `GET /panel`, `GET /releases/<fișier>`, `GET /hub/avatar?user=<id>` (proxy către thumbnails.roblox.com, cache 1 h cu plafon de 256 de intrări; 204 fără corp când avatarul nu poate fi luat, iar eșecul se reține 60 s). Fiind publică, ruta servește **doar conturile Roblox raportate de dispozitivele hub-ului**: un `user` necunoscut primește 204 fără nicio cerere ieșită, deci nimeni din afară nu poate umfla memoria hub-ului și nici nu îl poate folosi ca amplificator de trafic către Roblox. Rădăcina (`GET /`) redirecționează 302 către panou.

Restul rutelor `/hub/*` cer JSON și unul dintre antete: `X-Studio-Harness-Device: <64 hex>` (tokenul PC-ului unui developer, aprobat) sau `X-Studio-Harness-Admin: <codul de administrator>`. Fără antet corect răspund 401 cu `{"ok": false, "status": "unknown"}`; un dispozitiv neaprobat primește 403 cu `status: "pending"` sau `"revoked"`.

- Dispozitiv: `POST /hub/register`, `POST /hub/sync`, `GET /hub/status`, `GET /hub/workspaces`, `GET /hub/workspace?key=`, `GET /hub/project?key=`, `GET /hub/panel-data`, `GET /hub/session?job=`, `POST /hub/claims/claim|release|touch|wait`.
- Administrator: `GET /hub/admin/devices`, `POST /hub/admin/devices/approve|revoke|forget`, `POST /hub/admin/enrollment`.

Cererile cu antet `Origin` străin sunt refuzate cu 403. Panoul web este singura interfață din browser; tabla se vede și în Roblox Studio, pe PC-urile developerilor.

## Ce raportezi la final

1. URL-ul HTTPS al hub-ului și al panoului (`https://domeniu/panel`).
2. Rezultatul verificărilor de acceptanță și varianta de proxy folosită.
3. Orice linie scoasă din unitatea systemd.
4. Canalul prin care ai transmis codul de administrator — **fără codul însuși în raport**.
5. Explicația pentru proprietar: fiecare PC de developer apare în panou ca dispozitiv „în așteptare” după ce developerul instalează pluginul și deschide Roblox Studio; adminul îl aprobă o singură dată din fila **Dispozitive** a panoului sau cu `sudo -u studio-harness python3 /var/lib/studio-harness/app/team_hub.py --state-dir /var/lib/studio-harness --port 34880 --approve-pending`. Developerii nu au niciun token de configurat.
