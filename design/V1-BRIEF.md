# Studio Harness 1.0 — brief de overhaul (sursa de adevăr pentru toți agenții)

Repo: `C:\Users\ellob\Desktop\New folder (6)\roblox-studio-harness`. Tot ce nu este acoperit aici se decide după `STUDIO_HUB_CONTRACT.md` (rescris în faza 1) și `design/DESIGN.md` (faza 1). Când brief-ul și codul existent se contrazic, brief-ul câștigă. Limba: română cu diacritice în UI, comentarii, documente și mesaje; identificatorii de cod rămân în engleză.

## 0. Ce este și ce se schimbă

Studio Harness = plugin Roblox Studio (Luau, loader + aplicație cu hot swap) + daemon local Python (`scripts/studio_bridge.py`, `127.0.0.1:34871`) care rulează Claude Code / Codex din terminalul developerului + hub central Python (`scripts/team_hub.py`, găzduit la `https://lostcube.pro/roblox/harness/`) + panou web (`panel/index.html`). Echipa: 5–6 developeri care lucrează cu A.I. în Studio, pe mai multe jocuri.

Versiunea 0.8 cere „echipă”: `team.json` cu token de echipă per developer, „mod solo / mod echipă”, buton „Conectează/Deconectează daemon-ul”, „Scenă implicită”, listă de membri după nume tastat. Este prea complicat. Versiunea **1.0.0** schimbă:

1. **Fără echipe, fără `team.json`, fără token de echipă.** Daemon-ul se conectează singur la hub-ul central (`DEFAULT_HUB_URL = "https://lostcube.pro/roblox/harness"`). Developerul nu configurează nimic.
2. **Identitatea = contul Roblox.** Pluginul citește contul de developer din Studio (`StudioService:GetUserId()`, numele prin `Players:GetNameFromUserIdAsync`, avatar `rbxthumb://type=AvatarHeadShot&id=<userId>&w=48&h=48`) și îl trimite daemon-ului; hub-ul și panoul ne identifică între noi după contul Roblox (nume + avatar).
3. **Workspace = jocul.** Hub-ul detectează automat în ce jocuri este folosit pluginul (din `game.GameId` / `game.PlaceId` / nume / creator trimise de plugin) și grupează totul pe workspace: developeri prezenți, sesiuni, claims, jurnal, harta proiectului. În panou alegi workspace-ul dintr-o listă; în plugin workspace-ul curent este jocul deschis (cu posibilitatea de a privi și celelalte).
4. **Acces automat, dar controlat.** Fiecare PC are un token de dispozitiv generat local; hub-ul îl ține în „așteptare” până îl aprobă adminul cu un click în panou (sau hub-ul rulează cu înrolare deschisă). Developerul nu vede niciun token; adminul are un singur cod de administrator, generat pe server.
5. **Design nou peste tot** (plugin + panou): sistem de design comun (`design/DESIGN.md`), componente, stări goale, teme dark/light (pluginul urmează tema Studio). Simplu, curat, lizibil; nu terminal verde pe negru.
6. **Agenții lucrează la perfecție**: claims pe workspace (două jocuri diferite nu se blochează reciproc), harta proiectului pe grupe funcționale, tooluri `hub_*` care știu workspace-ul.
7. **Împachetare 1.0.0**: versiuni sincronizate, `Publish.cmd` → GitHub, auto-update fără repornire (ca în 0.8), token-ul local injectat automat în pluginul instalat (fără „cod de asociere” tastat).

Rămân neschimbate (și nu trebuie rescrise fără motiv): joburile `studio` (Mod S) și `terminal` (Mod T), proxy-ul MCP scoped (`bridge_mcp.py`, `harness_mcp.py`), providerii (`providers.py`), hot swap-ul pluginului (`/v1/plugin/bundle`, loader + `app/`), updater-ul, publicarea, harta proiectului (`project_map.py`), deploy-ul Ubuntu (adaptat), prefixul `/roblox/harness` (`strip_base`), rutele publice `/healthz`, `/panel`, `/releases/<f>`.

## 1. Principii

- Zero configurare pentru developer: instalezi pluginul + pluginul Claude Code/Codex, deschizi Studio, apari în hub. Singurul pas uman este aprobarea dispozitivului de către admin (o dată per PC).
- Niciun secret în repo, în pachete sau în UI: token-urile stau doar în `%LOCALAPPDATA%\StudioHarness\` (client) și în directorul de stare al hub-ului (server). Panoul ține codul în `localStorage`.
- Hub-ul nu are încredere în ce spune clientul despre identitate mai mult decât e nevoie: identitatea Roblox este informativă (cine e la tastatură), autorizarea vine din token-ul de dispozitiv aprobat.
- Tot ce e „live” se împrospătează singur (polling la 2 s în panou, sync la 2 s daemon↔hub, 1 s plugin↔daemon), fără butoane de refresh.
- Fiecare stare are un mesaj clar în română: conectare, așteptare aprobare, offline, revocat, la zi, actualizare în curs.

## 2. Identitate, dispozitive, acces

### 2.1 Fișiere locale (daemon, `local_state.state_dir()` = `%LOCALAPPDATA%\StudioHarness\`)

| Fișier | Rol |
|---|---|
| `local-token` | token UI plugin↔daemon (există; 32 hex+). Se injectează în pluginul instalat (vezi §5.1). |
| `device-token` | **nou**, 64 hex generat de `local_state.ensure_device_token(dir)`; identifică PC-ul la hub. |
| `config.json` | **nou, opțional**: `{"hub_url": "https://…"}` pentru self-hosting; altfel `DEFAULT_HUB_URL`. Env `STUDIO_HARNESS_HUB_URL` are prioritate. `--no-hub` (flag daemon) = offline/solo (folosit în teste). |
| `team.json` | **eliminat**. Dacă există, daemon-ul îl ignoră și scrie o singură dată în log că nu mai e folosit. |

`local_state.py` pierde `ensure_team_token`, `read_team`, `developer_name` (numele vine de la Roblox; fallback la `machine` = `platform.node()` când pluginul nu a trimis încă identitatea).

### 2.2 Identitatea trimisă de plugin

Pluginul (BridgeController) trimite `POST /v1/identity` la pornire, la fiecare 30 s și când se schimbă `game.PlaceId`/`game.GameId`/`game.Name`:

```json
{"user_id": 12345, "name": "ellob", "place_id": 129160346456700, "game_id": 987654, "place_name": "Ball",
 "creator_id": 555, "creator_type": "User"}
```

`user_id` = `StudioService:GetUserId()` (0 dacă nu e disponibil → identitate necunoscută, numele = „Studio”). Numele prin `pcall(Players.GetNameFromUserIdAsync)`; fallback `"user_" .. userId`. Avatarul nu se trimite (se derivă din `user_id`).

### 2.3 Cheia de workspace

`workspace_key(game_id, place_id)` în `scripts/project_map.py`: `"game:<game_id>"` dacă `game_id > 0`, altfel `"place:<place_id>"` dacă `place_id > 0`, altfel `"local"` (fișier nepublicat). Meta workspace: `{key, game_id, place_id, name, creator_id, creator_type}`. Numele afișat = ultimul `place_name` raportat.

### 2.4 Înregistrarea dispozitivului la hub

- Antet `X-Studio-Harness-Device: <device-token>` pe toate rutele `/hub/*` de membru. Hub-ul stochează doar `sha256(token)`; `device_id = sha256[:16]`; `member_id == device_id`.
- Stări: `pending` (nou), `approved`, `revoked`. Înrolare: `approve` (implicit) sau `open` (aprobare automată; `--open-enrollment` / `STUDIO_HARNESS_OPEN_ENROLLMENT=1` / setare din panou).
- Codul de administrator: fișier `hub-admin-token` în directorul de stare (înlocuiește `team-token`; `ensure_token_file`), env `STUDIO_HARNESS_ADMIN_TOKEN` (înlocuiește `STUDIO_HARNESS_TEAM_TOKEN`; Dockerfile HF), `--show-admin-code`. Antet `X-Studio-Harness-Admin: <cod>`; adminul poate apela orice rută de membru și rutele `/hub/admin/*`. CLI one-shot: `team_hub.py --state-dir … --approve-pending` (aprobă toate dispozitivele în așteptare și iese).
- Daemon-ul pornește ca `connecting`, apoi `pending` (reîncearcă înregistrarea la 15 s), `approved` (sync la 2 s), `offline` (hub-ul nu răspunde; reîncearcă cu backoff 2→30 s), `revoked` (reîncearcă la 60 s), `disabled` (`--no-hub` sau hub_url invalid).

### 2.5 Login în panou

- Butonul „Deschide panoul” din plugin → `POST /v1/panel/open` la daemon → daemon-ul deschide browserul implicit (`webbrowser.open`) la `hub_url + "/panel#device=" + device_token`. Panoul citește fragmentul, îl salvează în `localStorage["studioHarness.device"]`, îl șterge din URL (`history.replaceState`) și continuă.
- Alternativ, în ecranul de login se poate lipi un cod: dispozitiv (64 hex) sau cod de admin (orice altceva, ≥16 caractere); panoul încearcă întâi ca dispozitiv, apoi ca admin, și reține tipul.
- Un dispozitiv `pending` vede în panou doar ecranul „Așteaptă aprobarea adminului” cu `device_id`, numele Roblox și mașina.

## 3. Hub 1.0 (`scripts/team_hub.py`, numele fișierului rămâne pentru compatibilitatea actualizărilor; clasa devine `Hub`, `TeamHub = Hub` ca alias)

### 3.1 Stare persistentă `hub-state.json` (versiunea 2)

```json
{"version": 2, "hub_id": "…", "enrollment": "approve",
 "devices": {"<device_id>": {"device_id": "…", "token_hash": "…", "roblox_user_id": 12345, "roblox_name": "ellob", "machine": "PC-ELLOB",
              "bridge_id": "…", "version": "1.0.0", "status": "approved", "first_seen": 0, "last_seen": 0, "approved_at": 0, "approved_by": "admin",
              "workspace": "game:987654"}},
 "workspaces": {"game:987654": {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User",
                 "first_seen": 0, "last_seen": 0, "project": {"digest": "…", "snapshot_id": "…", "count": 138, "groups": {}, "reported_by": "<device_id>", "at": 0}}},
 "sessions": {"<job_id>": {"device_id": "…", "workspace": "game:987654", "meta": {}, "events": [], "closed_at": null}},
 "claims": {"game:987654": []},
 "journal": [{"at": 0, "workspace": "game:987654", "device_id": "…", "developer": "ellob", "roblox_user_id": 12345, "job_id": "…", "tool": "…", "paths": [], "summary": "…"}]}
```

`claims` conține, per workspace, rândurile `ClaimTable.export()`. O stare cu `version` < 2 se ignoră (log) și hub-ul pornește curat. `hub_id` se păstrează dacă există. Un `ClaimTable` per workspace (`claims.py` rămâne neschimbat ca model). Membrii „online” = dispozitive aprobate cu `last_seen` în ultimele 15 s; „prezent în workspace” = ultimul `workspace` raportat prin sync. Sesiunile închise se păstrează 30 min, jurnalul ultimele 1000 de intrări (ca acum).

### 3.2 Rute (după `strip_base`; JSON; erorile `{"ok": false, "error": "…"}`)

Publice: `GET /healthz`, `GET /` (302 → panel, ca acum), `GET /panel`, `GET /releases/<f>`.

Membru (antet dispozitiv aprobat sau antet admin):

| Rută | Corp / răspuns |
|---|---|
| `POST /hub/register` | corp `{roblox: {user_id, name} sau null, machine, bridge_id, version, workspace: meta sau null}` → 200 `{ok, status: "approved", device_id, hub_id, enrollment}`; 202 `{ok: true, status: "pending", device_id, hub_id}`; 403 `{ok: false, status: "revoked"}`. Singura rută care acceptă un dispozitiv necunoscut/pending. |
| `POST /hub/sync` | ca `/team/sync` din 0.8 (sesiuni + evenimente incrementale, `claims_touch`, proiect cu digest) **plus** `workspace: meta sau null`, `roblox` (dacă s-a schimbat). Răspuns: `{ok, now, hub_id, device: {status}, workspace: key, members: [prezenți în workspace], sessions: [sesiunile altora din workspace, meta + evenimente noi], claims: [ale workspace-ului], journal: [ultimele 50 ale workspace-ului], workspaces: [sumar toate]}`. Păstrează semantica de cursor/evenimente a implementării 0.8 (citește codul înainte de a rescrie). |
| `GET /hub/status` | `{ok, version, hub_id, enrollment, device: {device_id, status, roblox_user_id, roblox_name}, workspaces: n, members_online: n}` |
| `GET /hub/workspaces` | `[{key, name, game_id, place_id, creator_id, creator_type, last_seen, members_online: [{device_id, roblox_user_id, roblox_name, machine, last_seen}], sessions_active, claims, project: {count, digest, groups: {grupă: count}} sau null}]` |
| `GET /hub/workspace?key=` | `{workspace, members, sessions: [meta], claims, journal: [ultimele 200], project: sumar}` |
| `GET /hub/project?key=` | harta completă a proiectului (grupele cu intrări) |
| `GET /hub/panel-data?workspace=<key>` | tot ce afișează panoul într-un apel: `{me: {device_id, status, roblox_user_id, roblox_name, admin: bool}, hub: {hub_id, version, enrollment, now}, workspaces: [sumar], selected: <răspunsul /hub/workspace> sau null, pending_devices: n (doar admin)}` |
| `GET /hub/session?job=` | `{meta, events}` (pentru panou, fluxul unei sesiuni) |
| `GET /hub/avatar?user=<id>&size=48` | PNG proxied de la `https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=<id>&size=<s>x<s>&format=Png&isCircular=false` (JSON `data[0].imageUrl`, apoi imaginea), `size` în {48, 60, 100}, cache în memorie 1 h, timeout 5 s, maxim 512 KB; la eșec 204 (panoul desenează inițialele). Fetcher injectabil pentru teste (fără rețea). |
| `POST /hub/claims/claim`, `/hub/claims/release`, `/hub/claims/touch`, `/hub/claims/wait` | ca în 0.8, corpul include `workspace: key` (implicit workspace-ul curent al dispozitivului). Conflictele doar în același workspace. |

Admin (antet admin obligatoriu):

| Rută | Efect |
|---|---|
| `GET /hub/admin/devices` | toate dispozitivele (fără `token_hash`), sortate: pending, approved, revoked |
| `POST /hub/admin/devices/approve` `{device_id}` | → approved |
| `POST /hub/admin/devices/revoke` `{device_id}` | → revoked; sesiunile și claims-urile lui se eliberează |
| `POST /hub/admin/devices/forget` `{device_id}` | șterge dispozitivul (poate reveni ca pending) |
| `POST /hub/admin/enrollment` `{mode: "approve" sau "open"}` | schimbă înrolarea (persistată) |

Antete/CSP/Origin ca în 0.8 (`_check_origin`, `X-Frame-Options: DENY`, `Content-Security-Policy` cu `img-src 'self' data:`, `connect-src 'self'`). Rutele `/team/*` dispar (404).

### 3.3 CLI hub

`--listen`, `--port`, `--state-dir`, `--no-auto-update`, `--inherit-socket` (ca acum) + `--show-admin-code`, `--approve-pending`, `--open-enrollment`. Log-urile nu conțin niciodată token-uri.

## 4. Daemon 1.0 (`scripts/studio_bridge.py`, `VERSION = "1.0.0"`)

- Pornire: token UI, token dispozitiv, `hub_url` (env → config.json → `DEFAULT_HUB_URL`), `HubClient` (în `scripts/team_client.py`; numele fișierului rămâne, clasa devine `HubClient`, `TeamClient = HubClient` alias). Fără `team.json`.
- `GET /v1/status` (token UI) → adaugă `identity: {user_id, name, avatar} sau null`, `workspace: meta sau null`, `hub: {url, status, hub_id, device_id, error, last_sync, enrollment}`, `panel_url` (fără token), `members: [prezenți în workspace-ul curent]`, `workspaces: [sumar]`; elimină `team`. `features` adaugă `identity`, `workspaces`, `panel`.
- `POST /v1/identity` (§2.2) → `{ok, workspace: meta}`; schimbarea workspace-ului sau a contului declanșează un sync imediat.
- `POST /v1/panel/open` → deschide browserul (§2.5) → `{ok, url}`; 409 dacă hub-ul e `disabled`; URL-ul întors nu conține token-ul.
- `GET /v1/hub/workspaces`, `GET /v1/hub/workspace?key=` → proxy read-only spre hub (pentru plugin), 503 dacă hub-ul nu e `approved`.
- `GET /v1/board` → include `workspace`, `members`, `identity`.
- Joburi: fiecare job primește `workspace` (cheia curentă la creare); claims-urile merg la hub cu `workspace`; `hub_board`/`hub_project` arată workspace-ul; când hub-ul nu e `approved`, claims-urile sunt locale (ca „solo” în 0.8) și toolurile spun explicit că hub-ul nu e disponibil.
- Studio țintă: se alege automat Studio-ul al cărui nume coincide cu `place_name` din identitate (prin `list_roblox_studios`); `/v1/default-studio` rămâne ca suprascriere manuală („Avansat” în plugin).
- Ordinea actualizărilor: hub-ul se actualizează primul (din upstream); daemon-ii se actualizează de la hub (`/releases/`). Daemon-ul 1.0 tratează 404 pe `/hub/*` ca `offline` cu mesajul „Hub-ul rulează o versiune mai veche”.

## 5. Pluginul Studio 1.0

### 5.1 Loader (`studio-plugin/StudioHarness.server.luau`, `VERSION = "1.1.0"`)

- Token-ul UI vine automat: `build_studio_plugin.py` pune în rbxmx un `StringValue` numit `LocalToken` (copil al scriptului loader) cu `Value = "STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER"`; `install-studio-plugin.ps1` și `updater.install_studio_plugin` înlocuiesc placeholder-ul cu conținutul lui `local-token` (creat dacă lipsește) când scriu fișierul în `Roblox\Plugins\`. Loader-ul citește `script:FindFirstChild("LocalToken")`: valoare validă (≥8, fără spații/control, diferită de placeholder) → o salvează în settings și o folosește; altfel rămâne câmpul manual din UI (fallback).
- Restul (hot swap din `/v1/plugin/bundle`, handoff, verificarea reviziei) rămâne.

### 5.2 Aplicația (`studio-plugin/modules/`, `-- Studio Harness App 1.0.0` în `Main.luau`)

Store (`SessionStore.luau`), câmpuri noi/înlocuite (toate opționale la deserializare, compatibile cu snapshot-uri 0.8):

```lua
store.identity   = { userId = 12345, name = "ellob", avatar = "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48" } -- sau nil
store.workspace  = { key = "game:987654", name = "Ball", placeId = 1291603, gameId = 987654, creatorId = 555, creatorType = "User" } -- sau nil
store.hub        = { status = "approved", url = "https://…", hubId = "…", deviceId = "…", error = nil, panelUrl = "https://…/panel", enrollment = "approve" }
store.members    = { { deviceId = "…", userId = 1, name = "ana", machine = "PC-ANA", lastSeen = 0, me = false }, … } -- prezenți în workspace-ul curent
store.workspaces = { { key = "…", name = "…", membersOnline = 2, sessionsActive = 1, claims = 0, mine = true }, … }
store.viewWorkspace = nil -- cheia workspace-ului privit (nil = cel curent)
-- sesiunile (store.windows) primesc owner = { userId, name, deviceId } și workspace = key; store.team dispare
```

`BridgeController.luau`: identitatea (§2.2) prin `StudioService`/`Players` (singurul modul, împreună cu loader-ul, care folosește `HttpService`), `POST /v1/identity`, `POST /v1/panel/open`, mapare `/v1/status` → store, conectare automată cu token-ul primit de la loader (fără buton „Conectează”; reconectare cu backoff), auto-hide al câmpului de cod când token-ul e valid.

`ProjectScanner.luau`: adaugă în chunk-uri `game_id`, `creator_id`, `creator_type` (pe lângă `place_id`, `place_name`).

### 5.3 Interfața (HubView, SessionView, ApprovalView, Theme) — vezi `design/DESIGN.md`

Dock widget „Studio Harness”, lățime minimă 320, scroll vertical, de sus în jos:

1. **Antet**: avatar (ImageLabel rotunjit, 28 px) + nume Roblox + punct de stare hub (verde approved / chihlimbar pending / roșu offline / gri connecting) + „v1.0.0”; dreapta: buton „Panou” (deschide browserul).
2. **Banner** doar când e cazul: „Așteaptă aprobarea adminului · dispozitiv ab12cd34” / „Hub offline: …” / „Actualizare aplicată” (dispare în 5 s).
3. **Workspace**: numele jocului, `place 1291603 · creator`, rând de avatare (24 px) cu numele celor prezenți („tu” pentru sine), contor; un buton mic „Alte workspace-uri ▾” deschide o listă pentru a privi alt workspace (read-only: prezenți, sesiuni).
4. **Acțiune principală**: control segmentat `Claude Code | Codex` + buton primar „Sesiune nouă” pe toată lățimea; sub el un rând mic cu disponibilitatea CLI-urilor.
5. **Sesiuni**: carduri (bară colorată de stare, titlu/prompt scurtat, chip provider, chip tip Studio/Terminal, avatar+nume proprietar, timp scurs); grupate „Ale tale” / „În workspace”; click → SessionView. Stare goală prietenoasă.
6. **Aprobare** (când o sesiune cere): card cu toolul, argumentele scurtate, butoane „Permite” / „Refuză”.
7. **Claims**: listă compactă (cale, deținător, timp) cu „Eliberează” pe ale tale.
8. **Jurnal**: ultimele 10 modificări din workspace.
9. **Subsol**: „Daemon · 127.0.0.1:34871 · la zi” + secțiune pliabilă „Avansat”: cod de asociere (doar dacă loader-ul nu a livrat token-ul), Studio țintă (suprascriere), buton „Deconectează daemon-ul”.

`Theme.luau` devine sistemul de design: paletă (dark/light după `settings().Studio.Theme`, cu `ThemeChanged`), tipografie (`Enum.Font.GothamMedium` pentru UI, `Enum.Font.Code` pentru id-uri), spațiere 4/8/12/16/24, raze 6/10, componente: `Theme.card`, `Theme.chip`, `Theme.dot`, `Theme.avatar`, `Theme.button(kind = "primary"|"secondary"|"ghost"|"danger")`, `Theme.segmented`, `Theme.row`, `Theme.empty`, `Theme.banner`, `Theme.section`, plus utilitarele existente (`new`, `connect`, `click`, `elapsed`, `list`, `padding`, `stroke`). `RichText = false` peste tot. Textele nu se trunchiază brutal: `TextTruncate = AtEnd` sau `store.prefix`.

SessionView: aceeași paletă; antet cu titlu, chip provider/stare, proprietar; flux de mesaje cu bule diferențiate (utilizator / asistent / tool / eroare / claim), zona de prompt cu buton „Trimite” și „Oprește”; ApprovalView aliniat.

## 6. Panoul web 1.0 (`panel/index.html`, un singur fișier, fără CDN, CSP neschimbat)

- **Login**: card centrat: „Deschide panoul din pluginul Studio Harness (butonul Panou)”; câmp „sau lipește codul” (dispozitiv sau admin); `#device=` din fragment (§2.5). Eroare clară la cod refuzat.
- **Pending**: ecran „Dispozitivul tău așteaptă aprobarea” cu id, nume Roblox, mașină; se reîncearcă la 5 s.
- **Shell**: bară de sus (logo text „Studio Harness”, selectorul de temă, eu: avatar + nume + „admin” dacă e cazul, badge „N în așteptare” pentru admin); bară laterală cu workspace-urile (nume, avatarele celor prezenți, contor sesiuni/claims; pe ecran îngust devine un select); conținut cu file: **Activitate** (prezenți, sesiuni live cu fluxul de evenimente la click, claims, jurnal), **Proiect** (harta pe grupe funcționale, căutare, detalii pe grupă), **Dispozitive** (admin: pending cu „Aprobă”/„Revocă”, lista tuturor; non-admin: doar dispozitivul propriu). Modul de înrolare (admin).
- Avatare prin `GET /hub/avatar?user=` (same-origin) cu fallback la inițiale desenate (SVG data: URI). Polling 2 s pentru panel-data, 30 s pentru proiect (sau la schimbarea digest-ului). `?demo=1` rămâne (date de exemplu, fără server).
- URL-urile relativ la `BASE_PATH` (prefixul paginii), ca în 0.8.
- Responsive ≤ 400 px, dark/light (`prefers-color-scheme` + comutator salvat în `localStorage`), fără scroll orizontal, accesibil (contrast ≥ 4.5:1, focus vizibil, `aria-*` pe file și butoane).

## 7. Sistem de design (`design/DESIGN.md`, scris în faza 1; valorile de mai jos sunt punctul de plecare)

- Paletă dark: fundal `#0B0F14`, suprafață `#121821`, suprafață ridicată `#1A2230`, contur `#26303F`, text `#E6EDF3`, text secundar `#8B98A9`; light: fundal `#F6F8FA`, suprafață `#FFFFFF`, ridicată `#F0F3F7`, contur `#D6DCE5`, text `#0F172A`, secundar `#5B6675`.
- Accente: primar `#4ADE80` (verde, moștenit din identitatea produsului) cu text pe accent `#06210F`; info `#60A5FA`; atenție `#FBBF24`; pericol `#F87171`; Claude `#D97757`; Codex `#10A37F`. Stări sesiune: draft gri, în coadă info, în lucru primar, cere aprobare atenție, finalizat primar mai șters, eroare pericol, oprit gri.
- Tipografie: web `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` + `ui-monospace, "Cascadia Mono", Consolas, monospace` pentru id-uri/căi; Studio `GothamMedium`/`Gotham` + `Code`. Scară 12/13/14/16/20.
- Spațiere 4/8/12/16/24; raze 6 (chip), 10 (card), 999 (avatar/dot); umbre discrete doar în light.
- Componente comune (nume identice în CSS și Luau): card, chip, dot, avatar, button(primary/secondary/ghost/danger), segmented, row, empty, banner, section, tab.
- Micro-interacțiuni: tranziții 120–160 ms, hover/focus vizibile, fără animații decorative.

## 8. Actualizare, publicare, deploy

- Versiuni: `scripts/studio_bridge.py` `VERSION`, `scripts/team_hub.py` `VERSION`, `.claude-plugin/plugin.json`, `Main.luau` (`-- Studio Harness App 1.0.0`) → **1.0.0**; loader **1.1.0**; `harness_mcp.compatible_version` acceptă ≥ 1.0 (și tot ≥ 0.4 dacă nu e nevoie să rupem).
- `publish_release.py`: pachetele nu mai includ `Setup-Team.cmd`/`setup-team.ps1` (șterse din repo); `scan_secrets` verifică și `device-token`/`hub-admin-token`; `Publish.cmd` neschimbat (`--github --bump patch`); `update-channel.json` neschimbat.
- `updater.install_studio_plugin(...)` primește `local_token` și înlocuiește placeholder-ul (§5.1); `install-studio-plugin.ps1` la fel (citește/creează `local-token`).
- Deploy Ubuntu (`deploy/ubuntu/`): `install.sh` creează `hub-admin-token` (nu `team-token`), afișează calea și explică aprobarea din panou; unit-ul systemd neschimbat; Caddy/nginx cu prefix ca în 0.8; `README.md` + `AI-BRIEF.md` rescrise pentru 1.0 (fără echipă/token per developer; înrolare; cod admin). Root `Dockerfile` (HF Space): `STUDIO_HARNESS_ADMIN_TOKEN`. `Start-Team-Hub.cmd` → `Start-Hub.cmd`.

## 9. Convenții obligatorii

- Fișiere LF, UTF-8 (`.ps1` cu BOM). Python 3.10+, fără dependențe externe. Luau compatibil Roblox Studio (fără `require` de fișiere; modulele se primesc ca în 0.8), `RichText = false`.
- `HttpService` doar în `BridgeController.luau` și în loader. `SessionStore.luau` nu atinge servicii Roblox (testabil cu `luau.exe`).
- Testele Python nu scriu în `%LOCALAPPDATA%` real, nu ating rețeaua externă (fetcher-e injectabile, servere pe loopback cu port 0), nu pornesc procese pe porturile 34871/34880 (daemon-ul și Studio-ul real rulează pe acest PC). Fără `sleep`-uri lungi; suita completă trebuie să rămână sub ~60 s.
- Mesaje de eroare și log-uri fără token-uri. Fără `print` de debugging lăsat în cod.
- Când schimbi o rută/un câmp, actualizează contractul, testele și documentele care îl menționează în aceeași fază.

## 10. Faze, agenți și proprietatea fișierelor

Regula de aur: **un fișier are un singur proprietar într-o fază**. Nu edita fișiere care nu îți sunt alocate; dacă ai nevoie de o schimbare în alt fișier, descrie-o exact în raportul tău (secțiunea `needs`), iar faza următoare o preia. În timpul fazelor de implementare rulează doar testele modulelor tale (`python -m unittest discover -s tests -p "test_x.py"`), nu suita completă (alți agenți editează în paralel). Șterge fișierele pe care brief-ul le declară eliminate doar dacă îți sunt alocate.

| Faza | Agent | Fișiere deținute |
|---|---|---|
| 1 Contract & design | A | `STUDIO_HUB_CONTRACT.md` (rescris complet ca 1.0), `STUDIO_BRIDGE_CONTRACT.md` (aliniat) |
| | B | `design/DESIGN.md` (sistem de design + wireframe-uri ASCII plugin/panou + specificația API `Theme.luau` și a claselor CSS) |
| | C | `scripts/local_state.py`, `scripts/project_map.py` (`workspace_key`), `tests/test_project_map.py`, `tests/test_local_state.py` (nou; mută testele `state_dir` din `test_deploy.py` doar dacă e simplu — altfel lasă-le) |
| 2 Hub | A | `scripts/team_hub.py`, `tests/test_team_hub.py`, `tests/test_hub_state.py` |
| | B | `scripts/team_client.py`, `tests/test_team_client.py` (nou; cu un hub fals pe loopback) |
| | C | `scripts/updater.py`, `scripts/publish_release.py`, `scripts/build_studio_plugin.py`, `scripts/install-studio-plugin.ps1`, `tests/test_updater.py`, `tests/test_publish_release.py`, `tests/test_publish_github.py`, `tests/test_studio_app.py` |
| 3 Daemon, tooluri, deploy | A | `scripts/studio_bridge.py`, `tests/test_studio_bridge.py`, `tests/test_session_queue.py`, `tests/test_terminal_sessions.py`, `tests/test_project_api.py` |
| | B | `scripts/harness_mcp.py`, `scripts/harness_hook.py`, `scripts/bridge_mcp.py`, `skills/studio/SKILL.md`, `.mcp.json`, `hooks/hooks.json`, `tests/test_harness_mcp.py`, `tests/test_harness_hook.py`, `tests/test_harness.py` |
| | C | `deploy/ubuntu/*`, `Dockerfile`, `Start-Hub.cmd` (nou), `Start-Team-Hub.cmd` (șters), `Start-Daemon.cmd`, `Setup-Team.cmd` + `scripts/setup-team.ps1` (șterse), `.gitignore`, `tests/test_deploy.py` |
| 4 Plugin core & panou | A | `studio-plugin/StudioHarness.server.luau`, `studio-plugin/modules/BridgeController.luau`, `studio-plugin/tests/Loader.spec.luau` |
| | B | `studio-plugin/modules/SessionStore.luau`, `studio-plugin/modules/ProjectScanner.luau`, `studio-plugin/tests/SessionStore.spec.luau`, `studio-plugin/tests/ProjectScanner.spec.luau` |
| | C | `panel/index.html` (rescris: login, pending, shell, Activitate, Dispozitive; Proiect poate fi schelet) |
| 5 Plugin temă & panou proiect | A | `studio-plugin/modules/Theme.luau`, `studio-plugin/modules/Main.luau`, `studio-plugin/tests/Theme.spec.luau` (nou, funcții pure) |
| | B | `panel/index.html` (fila Proiect completă, fluxul sesiunii, detalii, stări goale) |
| | C | `README.md`, `VERIFICATION.md` (schelet 1.0), `deploy/ubuntu/README.md`, `deploy/ubuntu/AI-BRIEF.md` |
| 6 Plugin views & panou polish | A | `studio-plugin/modules/HubView.luau` |
| | B | `studio-plugin/modules/SessionView.luau`, `studio-plugin/modules/ApprovalView.luau` |
| | C | `panel/index.html` (polish: responsive, light/dark, accesibilitate, micro-interacțiuni) |
| 7 Integrare | A | orice fișier Python + teste: suita completă verde |
| | B | orice fișier Luau: compilare + spec-uri verzi; `python scripts/build_studio_plugin.py` |
| | C | documente + contract sincronizate cu codul real (citește codul, corectează documentele) |
| 8 Verificare live | A | smoke live (hub pe port liber + daemon pe port liber + panou în Chrome dacă e disponibil) — raport, fără editări în afara `VERIFICATION.md` |
| | B | revizie de securitate (read-only) — raport de constatări |
| | C | revizie UX/design plugin + panou (read-only, citește DESIGN.md) — raport de constatări |
| | D (după A–C) | aplică toate constatările confirmate, oriunde |
| 9 Release | A | bump 1.0.0 peste tot, `publish_release.py --dry-run`, instalare locală a pluginului, repornirea daemon-ului local, suita completă |
| 10 Git | orchestrator | `git init`, commit, repo GitHub, push, `Publish.cmd` |

## 11. Unelte și mediu

- Python: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -p "test_x.py"` din rădăcina repo-ului. Consola Windows este cp1252: setează mereu `PYTHONIOENCODING=utf-8`.
- Luau: `%LOCALAPPDATA%\StudioHarness\tools\luau\luau-compile.exe <fișiere>` (compilare) și `luau.exe` (rulare); spec-urile se rulează înfășurând modulul + spec-ul într-un singur fișier temporar, ca în `VERIFICATION.md` (vezi și cum o face `scripts/build_studio_plugin.py --verify`, dacă există). Fără `require` de fișiere.
- PowerShell 5.1: validează `.ps1` cu `[System.Management.Automation.PSParser]::Tokenize`. Bash: `bash -n install.sh`.
- Porturi pentru smoke: alege porturi libere > 40000; nu atinge 34871 (daemon-ul real) și 34880.
- Chrome: toolurile `mcp__claude-in-chrome__*` (încarcă-le cu ToolSearch) pot deschide panoul servit local și pot face capturi de ecran pentru verificarea vizuală.
- Fișiere temporare: `%TEMP%\studio-harness-1.0\<faza>\`.

## 12. Definiția lui „gata” (1.0.0)

1. Suita Python completă verde; toate fișierele Luau compilează; spec-urile Luau trec; `build_studio_plugin.py` produce `dist/StudioHarness.rbxmx` cu `LocalToken`.
2. Smoke live: hub + daemon (`--state-dir` temporar, fără plugin) → înregistrare pending → aprobare cu cod admin → sync approved → workspace din `/v1/identity` → sesiune terminal creată → claims pe workspace → panoul se încarcă cu `#device=` și arată workspace-ul, membrii, sesiunea; `?demo=1` funcționează.
3. `publish_release.py --dry-run` trece scanarea de secrete; pachetele nu conțin `Setup-Team`, `team.json`, token-uri.
4. Documentele descriu exact comportamentul 1.0 (fără „echipă”, fără `team.json`).
5. Pluginul instalat local, daemon-ul local repornit pe 1.0.0, Studio-ul afișează noua interfață (utilizatorul repornește Studio o dată pentru loader-ul 1.1.0).
