# Verificare Studio Harness 1.0.0

Documentul este **scheletul verificării 1.0**: listează ce trebuie rulat, cu ce comenzi și ce se așteaptă de la fiecare verificare. Coloanele și secțiunile marcate „_de completat în faza 8_” se completează cu rezultate **observate**, nu presupuse; nicio linie nu se declară trecută fără să fi fost rulată în sesiunea respectivă.

Versiunile 1.0.0 (`plugin.json`, `studio_bridge.VERSION`, `team_hub.VERSION`, antetul `App` din `Main.luau`) sunt aliniate și verificate de `test_updater.test_repo_versions_are_aligned`; loader-ul rămâne `1.1.0` (`-- Studio Harness Loader 1.1.0`, peste `publish_release.MIN_LOADER_VERSION`), iar `HubView.APP_VERSION` este tot `1.0.0`. **Verificat în faza 9, 14 septembrie 2026**: `plugin.json` `1.0.0` · `studio_bridge.VERSION = "1.0.0"` · `team_hub.VERSION = "1.0.0"` · `Main.luau` `-- Studio Harness App 1.0.0` · `StudioHarness.server.luau` `-- Studio Harness Loader 1.1.0` · `HubView.luau` `local APP_VERSION = "1.0.0"`; `README.md` și `STUDIO_HUB_CONTRACT.md` descriu aceleași valori.

## 1. Cum se rulează

Din rădăcina repo-ului, în PowerShell (consola Windows este cp1252, deci `PYTHONIOENCODING=utf-8` este obligatoriu):

```powershell
$env:PYTHONIOENCODING = "utf-8"
python -m unittest discover -s tests -v          # suita completă
python -m unittest discover -s tests -p "test_team_hub.py"   # un singur modul
python scripts/build_studio_plugin.py            # dist/StudioHarness.rbxmx + verify()
```

Reguli valabile pentru orice rulare: testele nu pornesc CLI-uri, browser sau inferență, nu ating rețeaua externă (fetcher-e și `request` injectabile, servere pe loopback cu port 0), nu scriu în starea reală (`STUDIO_HARNESS_STATE_DIR` și `HARNESS_TEST_TMP` indică directoare temporare) și **nu folosesc porturile 34871 și 34880**, unde rulează daemon-ul și hub-ul real de pe acest PC. Fișierele temporare ale verificărilor stau în `%TEMP%\studio-harness-1.0\`.

## 2. Teste Python

22 de fișiere de test, **558 de metode `test_*`** (numărate la rulare; `tests/fake_mcp.py` este un ajutor, nu un modul de test).

| Modul | Teste | Ce acoperă | Rezultat |
| --- | ---: | --- | --- |
| `test_local_state.py` | 26 | directorul de stare, `local-token`, `device-token` (64 hex, regenerat dacă e invalid), `device_id_for`, `hub-admin-token`, `config.json`, validarea și sursa lui `hub_url` | **26/26** (1 sărit: permisiunile POSIX nu există pe Windows) |
| `test_project_map.py` | 30 | `workspace_key` / `workspace_meta`, clasificarea pe grupe funcționale, limitele hărții | **30/30** |
| `test_claims.py` | 21 | modelul claims-urilor: normalizare, strămoși/descendenți, expirare, `wait_free` | **21/21** |
| `test_team_hub.py` | 55 | hub-ul 1.0 cu ceas injectat și server pe port efemer: înregistrare pending/approved/revoked, înrolare `approve`/`open`, sync pe workspace, claims în conflict, jurnal cu `seq`/`time`, avatar cu fetcher injectat, rutele de admin, `/releases/`, prefixul `/roblox/harness` | **55/55** |
| `test_hub_state.py` | 14 | `hub-state.json` versiunea 2: salvare atomică, încărcare, refuzul stărilor 0.8, păstrarea lui `hub_id`, restaurarea claims-urilor și a sesiunilor | **14/14** |
| `test_team_client.py` | 29 | `HubClient` contra unui hub fals pe loopback: stările `connecting`/`pending`/`approved`/`offline`/`revoked`/`disabled`, backoff, cursoare, oglinda locală, `HubError` | **29/29** |
| `test_studio_bridge.py` | 56 | daemon-ul: `/v1/status` (fără `team`, cu `identity`/`workspace`/`hub`), `/v1/identity`, `/v1/panel/open`, `/v1/board`, proxy-ul `/v1/hub/*`, claims locale vs. hub, actualizarea | **56/56** |
| `test_session_queue.py` | 26 | coada FIFO Mod S, aprobările per apel, deduplicarea `client_request_id` | **26/26** |
| `test_terminal_sessions.py` | 29 | sesiunile Mod T: creare, token de job, evenimente, închidere, eliberarea claims-urilor | **29/29** |
| `test_project_api.py` | 16 | `/v1/project/chunks`, `/v1/project`, `hub_project` (sumar și grupă), proiectul unui alt workspace prin `fetch_project` | **16/16** |
| `test_bridge_proxy.py` | 4 | proxy-ul MCP per job (`bridge_mcp.py`) | **4/4** |
| `test_mcp_client.py` | 18 | transportul MCP către Studio | **18/18** |
| `test_providers.py` | 38 | adaptoarele CLI headless (Claude Code, Codex), izolarea configurației, erorile de tool | **38/38** |
| `test_harness_mcp.py` | 15 | shim-ul stdio `studio_hub`: `initialize` 1.0.0, `compatible_version` (≥ 1.0 și 0.x ≥ 0.4, sufixe ignorate), pornirea daemon-ului, descrierile `hub_*` care numesc workspace-ul, statusul 1.0 fără `team` | **15/15** |
| `test_harness_hook.py` | 9 | hook-urile Claude Code și `notify` pentru Codex | **9/9** |
| `test_harness.py` | 25 | CLI-ul de diagnostic `roblox_harness.py` | **25/25** |
| `test_deploy.py` | 17 | `/healthz` și prefixul fără antete, `/hub/status` 401 cu `status: "unknown"`, `/team/*` → 404, înregistrarea și trecerea în offline scrise în jurnal fără token, fișierele copiate de `install.sh` derivate din importurile lui `team_hub.py` (inclusiv `project_map.py`), `Start-Hub.cmd` / `Start-Daemon.cmd` / `.gitignore`, Dockerfile-urile cu `STUDIO_HARNESS_ADMIN_TOKEN`, panoul cu `BASE_PATH`, CLI-ul hub-ului ca subproces cu `--show-admin-code`, `--approve-pending`, `--open-enrollment` | **17/17** |
| `test_plugin_package.py` | 5 | `dist/StudioHarness.rbxmx`: structura XML, modulele, `StringValue`-ul `LocalToken` | **5/5** |
| `test_studio_app.py` | 29 | pachetul `studio_app`, hot swap-ul modulelor, instalarea cu tokenul injectat în `.rbxmx` | **29/29** |
| `test_updater.py` | 44 | manifest, SHA256, `apply_bundle`, backup-uri, oglindirea canalului, alinierea versiunilor din repo | **44/44** |
| `test_publish_release.py` | 17 | conținutul pachetelor `plugin`/`hub`/`studio_app`, `scan_secrets`, fișierele excluse (fără `Setup-Team.cmd`, `Start-Team-Hub.cmd`, tokenuri) | **17/17** |
| `test_publish_github.py` | 16 | publicarea prin git: bump, commit, push, canalul upstream | **16/16** |

Verificări explicite care **nu** mai trebuie să existe în 1.0 (dacă apar, este o regresie): `Setup-Team.cmd`, `setup-team.ps1`, `Start-Team-Hub.cmd`, `team.json`, `team-token`, `STUDIO_HARNESS_TEAM_TOKEN`, `--show-token`, antetul `X-Studio-Harness-Team`, rutele `/team/*` (acum 404). Smoke-ul cu doi daemon-i nu mai folosește `team.json`: fiecare daemon are propriul director de stare, deci propriul `device-token`.

**Rezultatul suitei complete (faza 9, 14 septembrie 2026):** `python -m unittest discover -s tests` → **`Ran 539 tests` · `OK (skipped=1)` · 37 s** (Windows 11, Python 3.10, `PYTHONIOENCODING=utf-8`, din rădăcina repo-ului). Singurul test sărit este `test_local_state.TokenFileTests.test_posix_token_files_are_owner_only` („permisiunile POSIX nu există pe Windows”). Nicio eroare, niciun eșec.

Cinci eșecuri găsite la prima rulare a suitei complete au fost reparate în această fază, toate în teste rămase în urma implementării: `test_project_api` cerea CSP-ul panoului fără `frame-ancestors`/`base-uri`/`form-action` (implementarea și `test_team_hub` le au deja), trimitea 205 intrări de jurnal într-un singur sync deși `MAX_SYNC_JOURNAL` este 32, și aștepta `panel-data.me` fără cheia `workspace` (contractul §9.2 o cere); `test_publish_github.LuauRepo` presupunea că `FakeRepo` nu scrie surse Luau, deși în 1.0 le scrie, deci `loader=None`/`main=None` nu mai însemnau „fără surse”.

**Stabilitate sub sarcină.** Suita a fost rulată de peste 30 de ori, dintre care 20 cu opt procese Python care ocupau procesorul (rulările încetinesc de la ~35 s la 50–95 s). Sub sarcină au apărut două curse în `test_studio_bridge.HubLoopbackTests`, reparate tot aici — amândouă presupuneau că o stare ajunsă la hub este deja și în client:

- `test_registration_sync_and_status_follow_identity_and_workspace` aștepta doar ca hub-ul să fi *primit* un `/hub/sync`, apoi cerea `hub["last_sync"]` ca `float`; clientul scrie `last_sync` abia după ce procesează răspunsul. Așteptarea include acum starea clientului.
- `test_claims_go_to_the_hub_with_the_job_workspace_and_the_board_mirrors_the_workspace` cerea evenimentele sesiunii colegului imediat după ce rândul sesiunii apărea în tablă; daemon-ul le cere prin `want` abia la ciclul de sync următor, deci și ele se așteaptă acum.

Ambele erau probleme de test, nu de produs: sub sarcină, un singur ciclu de sync de 2 s poate cădea după asertare. După reparare: **6 rulări complete consecutive sub sarcină, toate `OK`**, plus 12 rulări ale clasei `HubLoopbackTests` singure, tot sub sarcină, toate `OK`.

## 3. Luau: compilare și spec-uri

13 fișiere `.luau` (loader-ul, cele 8 module și cele 4 spec-uri) trebuie să compileze cu binarele oficiale (`%LOCALAPPDATA%\StudioHarness\tools\luau\luau-compile.exe`, release luau-lang/luau):

```text
luau-compile --binary -O2 studio-plugin/StudioHarness.server.luau studio-plugin/modules/*.luau studio-plugin/tests/*.spec.luau
```

**Rezultat (faza 8 A, confirmat din nou în faza 9, 14 septembrie 2026):** toate cele 13 fișiere compilează, cod de ieșire 0, fără avertismente.

| Spec | Teste | Ce acoperă | Rezultat |
| --- | ---: | --- | --- |
| `SessionStore.spec.luau` | 60 | starea pură a aplicației: identitate, workspace, hub și prezenți, `mine` pe sesiuni și pe claims, ferestre studio/terminal/remote, aprobări (`inspectable`), `window.started`, jurnal, vizualizarea altui workspace, păstrarea identității locale la un status fără identitate, compatibilitatea cu handoff-ul 0.8 | **60/60** |
| `ProjectScanner.spec.luau` | 14 | inventarul: rădăcini, flags, plafon și `truncated`, chunk-uri cu `game_id`/`creator_id`/`creator_type`, helperii puri `place`/`identityPayload`/`chunkPayload` | **14/14** |
| `Theme.spec.luau` | 28 | paleta, contrastele WCAG pe ambele moduri, `resolveMode`, `elapsed`, `clock`, `initials`, `shortId`, `avatarUrl`, `fit`, `countLabel`, `sessionState`, `messageRole`, `placeLine`, `hubLine` | **28/28** |
| `Loader.spec.luau` | 13 | deciziile pure ale loader-ului 1.1.0: `shouldSwap`, `validateBundle`, `sameSources`, `buildFolder`, tokenul livrat prin `LocalToken` | **13/13** |

Spec-ul se rulează înfășurând modulul și spec-ul într-un singur fișier temporar (fără `require` de fișiere): `local Module = (function() … end)()`, apoi `spec(Module)` întoarce numărul de teste trecute, tipărit cu `luau.exe`. Pentru loader, același tipar funcționează pentru că scriptul întoarce tabelul `Loader` când globalul `plugin` lipsește (în afara Studio). Generatoare: `%TEMP%\studio-harness-1.0\phase8a\run_specs.py` (faza 8 A) și `%TEMP%\studio-harness-1.0\phase9\run_specs.py` (faza 9, rulare de confirmare pe codul final: `SessionStore 60`, `ProjectScanner 14`, `Theme 28`, `Loader 13`, cod de ieșire 0).

View-urile nu au spec-uri în repo, dar au harness-uri de runtime (Roblox fals + `Theme.luau` + `SessionStore.luau` + view-ul, într-un singur fișier rulat în `luau.exe`), scrise în fazele 6 A și 6 B și rerulate în faza 9 pe codul final: **HubView 143/143** (generator `%TEMP%\studio-harness-1.0\phase6a\build_spec.py`; 137 la scrierea lor în faza 6 A, verificările au crescut de atunci), **SessionView + ApprovalView 46/46** și **serializatorul de argumente al aprobărilor 17/17** (generator `%TEMP%\studio-harness-1.0\6B\build_smoke.py`). `ApprovalView` 1.0 nu mai folosește `HttpService` (brief §9): are propriul serializator JSON indentat, cu chei sortate.

`BridgeController.luau` și `Main.luau` nu au spec-uri pure (ating servicii Roblox): se verifică prin compilare și prin smoke-ul cu pluginul instalat (secțiunea 5.1) — smoke-ul fazei 8 A a rulat **fără** plugin, deci nu le acoperea.

## 4. Smoke live (faza 8 A)

**Rulat pe 14 septembrie 2026**, Windows 11, Python 3.10, din rădăcina repo-ului, **fără pluginul Studio** (identitatea a fost trimisă manual prin `POST /v1/identity`). Hub pe `127.0.0.1:41880`, doi daemoni pe `41871` și `41872` — toate trei porturile au fost verificate libere înainte de pornire, iar **porturile 34871 și 34880 nu au fost atinse**. Directoarele de stare și de runtime sunt temporare, în `%TEMP%\studio-harness-1.0\phase8a\` (`hub`, `daemon1`, `daemon2`, `rt1`, `rt2`), ca repo-ul și starea reală să rămână neatinse.

```powershell
$env:PYTHONIOENCODING = "utf-8"
$T = "$env:TEMP\studio-harness-1.0\phase8a"
python -u scripts/team_hub.py --listen 127.0.0.1 --port 41880 --state-dir $T\hub --no-auto-update
# în alte console, câte un daemon cu director de stare propriu (deci token de dispozitiv propriu):
$env:STUDIO_HARNESS_HUB_URL     = "http://127.0.0.1:41880"
$env:STUDIO_HARNESS_UPDATE_URL  = "http://127.0.0.1:41880/releases/manifest.json"   # canalul rămâne local
$env:STUDIO_HARNESS_AUTO_UPDATE = "0"
python -u scripts/studio_bridge.py --port 41871 --state-dir $T\daemon1 --runtime-dir $T\rt1
python -u scripts/studio_bridge.py --port 41872 --state-dir $T\daemon2 --runtime-dir $T\rt2
```

Cererile au fost făcute cu `urllib` (antetele `X-Studio-Harness-Device`, `X-Studio-Harness-Admin`, `X-Studio-Harness-Token`, `X-Studio-Harness-Job`), iar panoul a fost deschis în Chrome: extensia „Claude in Chrome” nu era conectată, așa că s-a folosit Chrome headless prin CDP, cu `Emulation.setDeviceMetricsOverride` pentru lățimile 1280 și 400 px. Toate procesele pornite au fost oprite la final.

### 4.1 Pașii definiției lui „gata” (brief §12.2)

| Pas | Așteptat | Rezultat |
| --- | --- | --- |
| Hub pornit (`team_hub.py --listen 127.0.0.1 --port 41880 --state-dir <temp> --no-auto-update`) | jurnal cu versiunea, adresa, calea codului de administrator și modul de înrolare, fără cod | **Trecut.** `Studio Harness hub 1.0.0 ascultă pe http://127.0.0.1:41880 (stare: …)` · `Codul de administrator: …\hub\hub-admin-token (pornește cu --show-admin-code ca să îl afișezi)` · `înrolare: approve (dispozitivele noi așteaptă aprobarea adminului din panou sau --approve-pending)` · `actualizare automată: inactivă`. `GET /healthz` → `200 {"ok": true, "version": "1.0.0"}`, antet `Server: StudioHarnessHub/1.0`. |
| Daemon pornit cu `STUDIO_HARNESS_HUB_URL` către hub | stdout fără tokenuri, doar căile fișierelor; `hub.status` trece în `pending` | **Trecut.** stdout: `Mașină: OMBRA · dispozitiv 9d2b9935bf53b6f6`, `Cod local de asociere: …\daemon1\local-token (injectat automat în pluginul instalat)`, `Token de dispozitiv: …\daemon1\device-token`, `Hub: http://127.0.0.1:41880 (sursa: env); panou: http://127.0.0.1:41880/panel`. `/v1/status` → `hub.status: "pending"`, `device_id` completat, `error: null`, `features` cu `identity`/`workspaces`/`panel` și **fără** `team`. |
| `GET /hub/admin/devices` cu codul de administrator | dispozitivul apare `pending` cu `device_id`-ul afișat de daemon | **Trecut.** `{"ok": true, "devices": [{"device_id": "9d2b9935bf53b6f6", "machine": "OMBRA", "version": "1.0.0", "status": "pending", "workspace": null, …}]}` — fără `token_hash`. Jurnalul hub-ului: `dispozitiv nou în așteptare: … (9d2b9935bf53b6f6) (daemon 1.0.0)`. |
| Aprobare (panou sau `--approve-pending`) | daemon-ul trece în `approved` la următorul ciclu, `/v1/status` arată `hub.status: "approved"` | **Trecut.** Primul dispozitiv: `POST /hub/admin/devices/approve {"device_id": …}` cu antetul admin → `{"ok": true, "device": {…, "status": "approved", "approved_by": "admin"}}`; `/v1/status` arată `approved` după 11 s (reîncercarea `register` la 15 s). Al doilea, prin CLI: `python scripts/team_hub.py --state-dir <temp>\hub --port 41880 --approve-pending` → `1 dispozitive aprobate prin hub-ul în execuție (port 41880)`, cod de ieșire **0**. |
| `POST /v1/identity` cu un joc fictiv | `/v1/status` arată `identity` și `workspace`; hub-ul are workspace-ul în `/hub/workspaces` | **Trecut.** Corp `{"user_id": 12345, "name": "ellob", "place_id": 90210001, "game_id": 777000111, "place_name": "Smoke Ball", "creator_id": 555, "creator_type": "User"}` → `{"ok": true, "workspace": {"key": "game:777000111", …}, "identity": {…, "avatar": "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48"}}`. `/v1/status`: `workspace` complet, `members` cu `me: true`, `workspaces: [{"key": "game:777000111", "members_online": 1, "mine": true}]`, `developer: "ellob"`, `panel_url: "http://127.0.0.1:41880/panel"`. `GET /hub/workspaces` → `{"ok": true, "workspaces": [{…, "members_online": [{…, "online": true}], "sessions_active": 0, "claims": 0, "project": null}]}`. Jurnal hub: `workspace nou: Smoke Ball (game:777000111)`. |
| Sesiune de terminal creată | apare în `/v1/board` și în `/hub/panel-data` ca sesiune a workspace-ului | **Trecut.** `POST /v1/terminal/sessions {"provider": "claude", "cwd": …, "name": "Smoke 8A"}` → `{"ok": true, "job_id": …, "bridge_id": …, "developer": "ellob", "workspace": "game:777000111"}`. `/v1/board` o arată cu `remote: false`, `mine: true`, `workspace`, `state: "running"`. `/hub/panel-data` o arată în `selected.sessions` lângă sesiunea celuilalt daemon (`developer: "ana"`). |
| `hub_claim` pe o cale | `scope: "hub"`; claim-ul apare în workspace, nu în altul; conflictul pe aceeași cale este refuzat cu deținătorul | **Trecut.** Prin proxy-ul MCP (`POST /agent/<job>/call`, antet de job): `hub_claim {"paths": ["Workspace.Map.Zone3", "ServerScriptService.Main"]}` → `{"ok": true, "claimed": [...], "scope": "hub"}`. Al doilea daemon, în **același** workspace, pe aceeași cale: `isError: true`, `{"ok": false, "conflicts": [{"path": "Workspace.Map.Zone3", "holder": "<job-ul primului>", "developer": "ellob", "held_path": "Workspace.Map.Zone3", "since": …}], "hint": "Așteaptă cu hub_wait sau alege altă zonă."}`. După ce al doilea daemon a trecut în `game:888000222` („Smoke Arena”) prin `/v1/identity` și a deschis acolo o sesiune nouă, **aceeași cale** a fost revendicată fără conflict: `{"ok": true, "claimed": ["Workspace.Map.Zone3"], "scope": "hub"}`. `GET /hub/workspaces`: `claims: 2` în „Smoke Ball”, `claims: 1` în „Smoke Arena”. `hub_release` fără argumente a eliberat toate cele trei căi. |
| Hub oprit în timpul lucrului | daemon-ul trece în `offline`, claims-urile devin locale cu `notice` explicit; la revenire se reconciliază | **Trecut.** După oprirea forțată a hub-ului: `/v1/status` → `{"status": "offline", "error": "Hub-ul nu răspunde."}`; `hub_claim` → `{"ok": true, "claimed": ["ReplicatedStorage.Shared"], "scope": "local", "notice": "Hub-ul nu este disponibil (stare: offline): claims-urile sunt locale, colegii nu le văd."}`; `hub_board.hub.notice` are același text; `/v1/board` arată rândul local cu `workspace` și `mine: true`; proxy-ul `/v1/hub/workspaces` → `503 {"ok": false, "error": "Hub-ul nu este disponibil (stare: offline)."}`. La repornirea hub-ului pe același director de stare: `stare încărcată din hub-state.json: 2 dispozitive, 2 workspace-uri, 3 sesiuni, 3 claims, 0 intrări de jurnal; hub_id păstrat`; daemon-ul revine în `approved` în ~5,5 s, iar claim-ul local `ReplicatedStorage.Shared` apare în `GET /hub/workspace?key=game:777000111` lângă celelalte două — reconciliere reușită. |
| Panoul deschis cu `#device=` | login automat, workspace-ul, prezenții, sesiunea și jurnalul vizibile; `?demo=1` merge fără server | **Trecut.** `http://127.0.0.1:41880/panel#device=<cod>` → `location.href` devine `…/panel` (fragment șters cu `history.replaceState`), `localStorage["studioHarness.device"]` are 64 de caractere, se randează shell-ul: titlu „Smoke Ball”, `place 90210001 · al tău`, filele Activitate/Proiect/Dispozitive, „Prezenți 1” (`tu · 1 prezent` + starea goală „Ești singurul prezent.”), „Sesiuni live 2” (carduri cu chip de provider, chip „Terminal”, avatar + proprietar, stare „În lucru”, „de N min”), „Claims 2” cu `expiră în 6 min`, „Jurnal 0” cu „Nicio modificare înregistrată încă.”, bara laterală cu ambele workspace-uri și subsolul `hub e3073f · v1.0.0`. `?demo=1` se încarcă fără nicio cerere către server (titlu „Studio Harness — Panou (date de exemplu)”, banner „Date de exemplu. Nimic de aici nu vine de la un hub real.”, 3 workspace-uri, 5 sesiuni cu toate stările, avatare desenate ca inițiale). Capturi în `%TEMP%\studio-harness-1.0\phase8a\`: `cdp-panel-1280.png`, `cdp-panel-400.png`, `cdp-demo-1280.png`, `cdp-demo-400.png`. |
| Panoul la 400 px | fără scroll orizontal | **Trecut.** La 400 px: `innerWidth == document.documentElement.scrollWidth == document.body.scrollWidth == 400`, iar parcurgerea tuturor elementelor din `body` nu a găsit **niciun** element cu marginea dreaptă peste `innerWidth`. Bara laterală devine `<select>` („Smoke Ball · 1 prezent · 2 sesiuni”), rândul de claim mută „expiră în …” pe a doua linie. La 1280 px: `scrollWidth == innerWidth == 1262`. |
| Panoul cu cod de administrator | fila Dispozitive cu `pending`, `Aprobă`/`Revocă`, modul de înrolare | **Trecut.** După `localStorage.clear()` panoul revine la login; codul de administrator lipit în câmp → shell cu badge-ul `admin` în bara de sus. Fila **Dispozitive**: „Înrolare” cu control segmentat `Cu aprobare` / `Deschisă` și hintul „Cu aprobare: dispozitivele noi așteaptă un click aici.”; „În așteptare 0 — Niciun dispozitiv în așteptare.”; „Toate dispozitivele 2” cu `ana · OMBRA` / `995876c1 · 1.0.0 · Smoke Arena · acum` și `ellob · OMBRA` / `9d2b9935 · 1.0.0 · Smoke Ball · acum`, fiecare cu chipul `Aprobat` și butonul `Revocă`. Captură: `cdp-admin-devices.png`. |
| Auto-update: `/releases/manifest.json` servit | canalul este servit de hub și citit de daemon fără să se descarce nimic | **Trecut.** `GET /releases/manifest.json` → `200 application/json`, `Cache-Control: no-cache`, 886 octeți, `version: 0.8.0`; `GET /releases/roblox-studio-harness-0.8.0.zip.sha256` → `200 text/plain`, `Cache-Control: public, max-age=3600`; `GET /releases/../hub-state.json` și `GET /releases/nope.json` → `404`. Daemon-ul, cu canalul îndreptat spre hub-ul local, a citit manifestul și a raportat `update: {"current": "1.0.0", "available": "0.8.0", "state": "idle", "message": ""}` — nu a descărcat nimic, pentru că versiunea din canal nu este mai nouă. |
| Daemon contra Roblox Studio real | `tools/list` cu toolurile Roblox plus `hub_board`/`hub_claim`/`hub_release`/`hub_wait`/`hub_project`, `scope` obligatoriu la `execute_luau`, `multi_edit` fără claim refuzat cu calea numită | **Trecut.** `GET /agent/<job>/tools` → 31 de tooluri: cele 26 ale Studio-ului plus cele 5 `hub_*`. `execute_luau.inputSchema.required == ["code", "datamodel_type", "scope"]`; nici `execute_luau`, nici `multi_edit` nu mai au `studio_id` în schemă. `multi_edit` pe o cale nerevendicată → `isError: true`, text `Fără claim pe ServerScriptService.SmokeNonexistent8A. Revendic-o mai întâi cu hub_claim.` (apelul nu a ajuns la Studio; nimic nu a fost modificat în scenă). |
| Pluginul în Studio real | conectare automată fără cod tastat, antet cu avatar și nume Roblox, workspace-ul jocului deschis, sesiuni, aprobări, temă dark/light | **Neverificat în faza 8 A**: smoke-ul rulează fără plugin, pe porturi temporare. Rămâne pentru faza 9, după instalarea locală a pluginului și repornirea daemon-ului real. |

### 4.2 Verificări de protocol făcute în aceeași sesiune

| Verificare | Rezultat observat |
| --- | --- |
| `GET /hub/status` fără antete | `401 {"ok": false, "error": "Dispozitiv necunoscut; înregistrează-te.", "status": "unknown"}` |
| `GET /hub/status` cu dispozitiv `pending` | `200` cu `device.status: "pending"`, `admin: false` |
| `GET /hub/status` doar cu cod de administrator | `200 {"ok": true, "admin": true, "device": null, "workspaces": 2, "members_online": 2}` |
| `POST /hub/sync` doar cu cod de administrator | `400 {"ok": false, "error": "Ruta cere antetul dispozitivului."}` |
| `GET /hub/workspace` fără `key` / cu cheie necunoscută | `400 Parametrul key lipsește.` / `404 Workspace-ul nu există.` |
| `GET /hub/panel-data` ca dispozitiv | `me.admin: false`, **fără** cheia `pending_devices` |
| `GET /hub/panel-data` ca admin | `me = {"device_id": null, "status": "admin", "roblox_name": "admin", "admin": true}`, `pending_devices: 0` |
| `GET /hub/panel-data?workspace=<necunoscut>` | `200` cu `selected: null` (nu 404) |
| `GET /team/status` (rută 0.8) | `404 {"ok": false, "error": "Rută inexistentă."}` |
| `GET /hub/avatar?user=<id>&size=48` | `200 image/png`, `Cache-Control: public, max-age=3600`, PNG 48×48 valid (proxied de la Roblox); a doua cerere răspunde din cache, sub 10 ms |
| `GET /hub/avatar?user=0` și `?user=abc` | `400 Parametrul user trebuie să fie un id Roblox (întreg pozitiv).` |
| `GET /panel` | `200 text/html`, `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache` |
| `POST /v1/panel/open` cu hub `offline` | `200 {"ok": true, "url": "http://127.0.0.1:41880/panel"}` — URL-ul întors **nu** conține tokenul (el ajunge doar în fragmentul dat browserului) |
| `hub-state.json` după repornire | `version: 2`, `hub_version: "1.0.0"`, `saved`, `journal_seq`, dispozitivele doar cu `token_hash` (64 hex); niciun token în clar |
| Tokenuri în jurnale | Niciunul dintre cele cinci secrete (două `device-token`, două `local-token`, `hub-admin-token`) nu apare în `hub.log`, `daemon1.log` sau `daemon2.log` |

### 4.3 Constatări din smoke

1. **Traceback-uri `socketserver` în jurnalul hub-ului** când o conexiune este închisă brusc de client: observat live, `ConnectionResetError: [WinError 10054] …` cu stivă completă, între două linii normale de jurnal. `HubHandler` prinde `BrokenPipeError`/`ConnectionResetError` în `_handle`, dar resetul apare mai devreme, în `handle_one_request`, unde `BaseServer.handle_error` tipărește stiva. Aceleași condiții apar normal în producție (tab de panou închis în timpul polling-ului la 2 s, `POST /hub/claims/wait` abandonat, daemon oprit) și ar umple jurnalul systemd. Se rezolvă cu un `handle_error` propriu pe `HubServer` și pe `BridgeServer`.
2. **Linia de jurnal a unui dispozitiv fără identitate Roblox** citește ciudat: `dispozitiv nou în așteptare: OMBRA @ OMBRA (9d2b9935bf53b6f6) (daemon 1.0.0)` — numele cade pe `machine`, deci apare de două ori. Cosmetic.
3. **Avatarul unui `user_id` inexistent nu ajunge niciodată la inițiale**: Roblox întoarce `200` cu un PNG-placeholder (48×48, grayscale), deci hub-ul îl proxiază corect cu `200`, iar panoul îl desenează ca atare; ruta `204` → inițiale se declanșează doar la eroare de rețea sau format. Comportament corect față de contract, notat pentru că arată altfel decât sugerează brief-ul §6.
4. `/v1/status` întoarce pe rândurile din `members` și câmpul `online` (pe lângă `me`), pe care contractul §4.3 nu îl listează. Inofensiv, dar implementarea și contractul diferă.

## 5. Pachet, publicare, instalare

Primele trei rânduri scriu în `dist/`, `releases/` și în `Roblox\Plugins\`, deci aparțin fazei 9; faza 8 A nu le-a rulat, ca să nu modifice fișiere care nu îi aparțineau. **Rulate în faza 9, 14 septembrie 2026**, pe acest PC.

| Verificare | Așteptat | Rezultat |
| --- | --- | --- |
| `python scripts/build_studio_plugin.py` | `dist/StudioHarness.rbxmx` determinist, cu `StringValue`-ul `LocalToken` = placeholder (`verify()` raportează `local_token: placeholder`) | **Trecut.** `Plugin Roblox Studio construit: StudioHarness.rbxmx (loader 1.1.0, 8 module, LocalToken: placeholder)`, cod de ieșire 0. Fișierul din `dist/` conține `STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER` de 3 ori, `LocalToken` de 4 ori, antetele `Studio Harness Loader 1.1.0.` și `Studio Harness App 1.0.0.`, iar tokenul local **nu** apare deloc. |
| `python scripts/publish_release.py --dry-run` | pachetele `plugin`, `hub`, `studio_app` + manifest; `scan_secrets` fără constatări; niciun `Setup-Team.cmd`, `Start-Team-Hub.cmd`, `team.json` sau fișier de token în pachete | **Trecut.** `Plugin Studio reconstruit… (loader 1.1.0)` · `Versiune: 1.0.0` · `Loader: 1.1.0; revizie module 450522e355be4ddd` · **`Scanare secrete: nimic găsit.`** · `Dry-run: fără git push și fără upload.`, cod de ieșire 0. `releases/` conține `manifest.json` (`version 1.0.0`, `loader_version 1.1.0`, `bundle_revision 450522e355be4ddd`, trei intrări `files`), cele trei `.zip` și cele trei `.sha256`. Conținut: **plugin 53 de fișiere** (scripturile, `panel/index.html`, `deploy/ubuntu/*`, `studio-plugin/**`, `dist/StudioHarness.rbxmx`, `Start-Hub.cmd`, `Start-Daemon.cmd`, `Publish.cmd`, documentele), **hub 14** (inclusiv `scripts/project_map.py`, cerut de importurile lui `team_hub.py`), **studio_app 8** (cele opt module `.luau`). Niciun `Setup-Team.cmd`, `setup-team.ps1`, `Start-Team-Hub.cmd`, `team.json`, `local-token`, `device-token` sau `hub-admin-token`; `.rbxmx`-ul din pachet are placeholder-ul, nu tokenul. |
| `Install-Studio-Plugin.cmd` | `.rbxmx` instalat cu tokenul local injectat (`LocalTokenInjected: true` în rezumatul JSON, fără valoarea tokenului), modulele în `Roblox\Plugins\StudioHarness\app\`, backup în `plugin-backups` | **Trecut.** Rezumat JSON: `Installed: true`, `HashVerified: true`, `LocalTokenInjected: true`, `LocalTokenCreated: false` (tokenul exista deja), `AppModules: 8`, `Backup` și `AppBackup` în `%LOCALAPPDATA%\StudioHarness\plugin-backups\`, `StudioRestartRequired: true` — **fără valoarea tokenului**. Fișierul instalat (`%LOCALAPPDATA%\Roblox\Plugins\StudioHarness.rbxmx`) conține tokenul local de 3 ori și **zero** apariții ale placeholder-ului, cu antetele `Loader 1.1.0` / `App 1.0.0`; `dist/StudioHarness.rbxmx` a rămas cu placeholder-ul (3) și fără token. Cele opt module sunt în `Roblox\Plugins\StudioHarness\app\`. Lansatorul a fost rulat și după normalizarea la LF: `cmd /c .\Install-Studio-Plugin.cmd` → cod de ieșire 0 (ramura `goto failed` a fost verificată separat într-un `.cmd` LF-only). |
| Auto-update cu un canal local | daemon-ul descarcă, verifică SHA256, aplică cu backup și repornește; hub-ul aplică pachetul `hub` și se relansează (pe Linux prin `execv` cu `--inherit-socket`) | **Parțial (faza 8 A).** Canalul local a fost servit de hub și citit de daemon: `GET /releases/manifest.json` → `200`, `Cache-Control: no-cache`; daemon-ul pornit cu `STUDIO_HARNESS_UPDATE_URL` spre acel manifest raportează `update: {"current": "1.0.0", "available": "0.8.0", "state": "idle"}` și **nu** descarcă nimic (versiunea din canal nu este mai nouă). Descărcarea, verificarea SHA256, aplicarea cu backup și relansarea **nu** au fost declanșate live nici în faza 9 (canalul public nu servește încă un pachet mai nou de 1.0.0): rămân acoperite de `test_updater` (44) și `test_studio_app` (29), iar `execv` cu `--inherit-socket` este POSIX și nu poate fi verificat pe Windows. |
| Convenția de fișiere (brief §9: LF, UTF-8, `.ps1` cu BOM) | tot repo-ul LF; `.gitattributes` fixează normalizarea la `git add`, ca `deploy/ubuntu/install.sh` să nu ajungă pe Linux cu `^M` pe linia shebang | **Trecut (faza 9).** Auditul a găsit 84 de fișiere text LF și doar două CRLF (`Install-Studio-Plugin.cmd`, `Publish.cmd`), normalizate acum la LF; cele trei `.ps1` au BOM și LF. S-a adăugat `.gitattributes` (`* text=auto eol=lf`, `*.rbxmx -text` ca sha256-ul din manifest să rămână valabil după clonare, plus tipurile binare). `deploy/ubuntu/install.sh` este LF și în repo, și în pachetul `hub`. |

**Atenție la publicarea reală.** `--dry-run` a fost rulat fără `--github`, iar `update-channel.json` nu are încă `github_repo`, deci `files.*.url` din `releases/manifest.json` au rămas pe baza implicită cu `OWNER` (`https://huggingface.co/spaces/OWNER/studio-harness/resolve/main/releases/…`). Publicarea adevărată trebuie făcută **o dată** cu `python scripts/publish_release.py --github <owner>/<repo> --bump patch` (salvează `github_repo` în canal); de atunci `Publish.cmd` (`--github --bump patch`) refolosește repo-ul. Pachetele și `.sha256`-urile din `releases/` sunt cele ale dry-run-ului și se regenerează la publicarea reală.

### 5.1 Smoke live cu pluginul instalat și daemon-ul real (faza 9)

Singura verificare care are voie să folosească portul **34871**: daemon-ul de lucru al acestui PC. Portul 34880 (hub-ul local) nu a fost atins.

```powershell
Get-NetTCPConnection -LocalPort 34871 -State Listen            # nimic nu asculta, deci n-a fost nimic de oprit
Start-Process python -ArgumentList "-u","scripts\studio_bridge.py" -WindowStyle Hidden   # din rădăcina repo-ului
$t = (Get-Content "$env:LOCALAPPDATA\StudioHarness\local-token" -Raw).Trim()
Invoke-RestMethod http://127.0.0.1:34871/v1/status -Headers @{ "X-Studio-Harness-Token" = $t }
```

| Verificare | Rezultat observat |
| --- | --- |
| Daemon pornit | ascultă pe `127.0.0.1:34871` (doar loopback), un singur proces |
| `GET /v1/status` → versiune | `version: "1.0.0"`, `plugin_bundle: {version: "1.0.0", revision: "450522e355be4ddd", source: "app", modules: 8}` — aceeași revizie ca `bundle_revision` din `releases/manifest.json` |
| `features` | `identity`, `workspaces`, `panel` prezente și `true`; **fără** `team` |
| Pluginul din Studio | `plugin_connected: true`, `/v1/board` arată `studios: [{ name: "Ball (placeId: 129160346456700)" }]` — loader-ul 1.1.0 s-a conectat singur, cu `LocalToken` injectat la instalare, **fără cod tastat** |
| Identitate Roblox | `identity: {user_id: 4324991580, name: "ArchangelS0L", avatar: "rbxthumb://type=AvatarHeadShot&id=4324991580&w=48&h=48"}`, `developer: "ArchangelS0L"`, `machine: "OMBRA"` — venită din `POST /v1/identity`, nu din configurație |
| Workspace detectat | `workspace: {key: "game:10766226591", game_id: 10766226591, place_id: 129160346456700, name: "Place2", creator_id: 554257950, creator_type: "Group"}` — `workspace_key` a ales forma `game:<id>` |
| Harta proiectului | `project: {snapshot_id: "<GUID>", count: 20000, place_name: "Place2"}` — pluginul a trimis inventarul prin `/v1/project/chunks` până la plafonul `MAX_PROJECT_NODES` |
| `hub` spre hub-ul public | `{url: "https://lostcube.pro/roblox/harness", status: "offline", hub_id: null, device_id: "87aea6745a4f3138", error: "Hub-ul a răspuns HTTP 405.", last_sync: null, enrollment: null}` — **așteptat**: hub-ul public nu rulează încă la acea adresă, iar daemon-ul rămâne în `offline` cu backoff, fără să blocheze lucrul |
| `panel_url` | `https://lostcube.pro/roblox/harness/panel` (derivat din `hub_url`, fără token în URL) |
| Proxy `/v1/hub/workspaces` cu hub `offline` | `503` — conform §4.7 (401/403/5xx ale hub-ului devin 503, ca pluginul să nu se deconecteze) |
| `update` | `{current: "1.0.0", available: null, state: "idle", restart_required: false}` — canalul public nu servește încă un manifest mai nou |
| Mesajele de pornire | doar căile fișierelor (`local-token`, `device-token`), `Mașină: … · dispozitiv <device_id>` și `Hub: <url> (sursa: …)` — **niciun token** |

Rămâne neverificat live, pentru că hub-ul public nu rulează: înregistrarea dispozitivului, aprobarea din panou, sync-ul pe workspace și claims-urile partajate între PC-uri. Toate au fost verificate în faza 8 A contra unui hub local pe port temporar (secțiunea 4).

## 6. Revizii (faza 8 B și 8 C)

- **Securitate** (read-only): tokenuri absente din log-uri, stdout, UI și pachete; autorizarea rutelor `/hub/*` și `/v1/*`; `Origin`/`Host`; CSP-ul panoului; limitele de corp și de coadă. Constatări: _de completat în faza 8_.
- **UX și design** (read-only, față de `design/DESIGN.md`): paleta și componentele în plugin și în panou, stările goale, textele de stare, responsive ≤ 400 px, contrast și focus. Constatări: _de completat în faza 8_.

## 7. Ce NU este verificat live

Din smoke-ul fazei 8 A au rămas în afara verificării:

- **Pluginul Studio**: smoke-ul fazei 8 A a rulat fără plugin (identitatea a fost trimisă manual prin `POST /v1/identity`). Faza 9 a instalat pluginul local și a verificat (secțiunea 5.1) conectarea automată cu `LocalToken`, identitatea Roblox, workspace-ul jocului deschis și harta proiectului trimisă din Studio — deci `StudioHarness.server.luau`, `BridgeController.luau`, `Main.luau` și `ProjectScanner.luau` au și acoperire live. Rămân verificate doar prin compilare și prin harness-urile din `luau.exe`: randarea celor trei view-uri în dock (antetul cu avatar, cardurile de sesiune, cardul de aprobare), tema dark/light urmărind `settings().Studio.Theme` și hot swap-ul la livrarea unui bundle nou.
- **Modul S (`POST /v1/chat`) și rularea reală a unui CLI**: niciun provider nu a fost pornit; s-au folosit doar sesiuni `terminal` create prin HTTP. Fluxul de evenimente al unei sesiuni (`/hub/session?job=&after=`) și jurnalul workspace-ului au rămas goale, pentru că niciun tool modificator nu a ajuns la Studio (apelul `multi_edit` a fost oprit corect de gardul de claims).
- **Harta proiectului**: `POST /v1/project/chunks` nu a fost alimentat (fără plugin), deci `want_project`, `PROJECT_REFRESH_SECONDS`, `/hub/project` și fila **Proiect** din panou au fost văzute doar cu `?demo=1`.
- **Timpii lungi**, verificați doar cu ceas injectat în teste: expirarea claims-urilor la 600 s, `hub_wait` până la 300 s (60 s la hub), reap-ul dispozitivelor offline la 60 s, retenția sesiunilor 1800 s, `PROJECT_REFRESH_SECONDS` 300 s, cache-ul de avatar 3600 s.
- **Aplicarea unei actualizări**: canalul a fost servit și citit (`/releases/manifest.json`), dar nu s-a descărcat și nu s-a aplicat niciun pachet; `execv` cu `--inherit-socket` este POSIX și nu poate fi verificat pe Windows.
- **Instalare pe un Ubuntu real** (`install.sh`, unit systemd, Caddy/nginx cu prefix `/roblox/harness`), **Docker/HF Space**, **push GitHub real** (`Publish.cmd`).
- **Două PC-uri reale în LAN** și **Team Create** cu editări simultane: conflictul dintre dispozitive a fost verificat cu doi daemoni pe aceeași mașină, în directoare de stare separate.
- **Revocarea și uitarea unui dispozitiv din panou** (`/hub/admin/devices/revoke`, `/forget`) și **înrolarea `open`**: rutele există și fila Dispozitive le afișează, dar nu au fost declanșate ca să nu se strice starea celorlalți pași ai smoke-ului.
- **Extensia „Claude in Chrome”**: nu era conectată la această sesiune, deci panoul a fost verificat prin Chrome headless și CDP (aceleași motoare de randare și aceleași lățimi, dar fără interacțiune de mouse reală).

## 8. Surse pentru protocoale

- https://create.roblox.com/docs/studio/mcp
- https://create.roblox.com/docs/reference/engine/datatypes/DockWidgetPluginGuiInfo
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/plugins
- https://learn.chatgpt.com/docs/app-server
- https://learn.chatgpt.com/docs/config-file/config-reference

## 9. Verificare finală după runda de corecții (14 septembrie 2026)

Cele 34 de constatări ale fazei 8 (13 din smoke, 16 din revizia de securitate, 5 din revizia UX) au fost aplicate într-o rundă separată, pentru că agentul care trebuia să le aplice fusese sărit. Agenții de corecție au găsit majoritatea deja rezolvate la cauză, au completat testele care lipseau și au respins cinci propuneri, cu motive scrise în rapoarte. Rezultatele de mai jos sunt măsurate pe codul final publicat, nu preluate din rapoarte.

| Verificare | Comandă | Rezultat |
| --- | --- | --- |
| Suita Python | `python -m unittest discover -s tests -p "test_*.py"` | `Ran 558 tests` · `OK (skipped=1)` · 33,5 s |
| Compilare Luau | `luau-compile --binary -O2` pe cele 13 fișiere | cod de ieșire 0, fără avertismente |
| Spec-uri Luau | generator din `%TEMP%\studio-harness-1.0\phase9-luau
un_specs.py` | SessionStore 60 · ProjectScanner 14 · Theme 28 · Loader 13 |
| Construirea pluginului | `python scripts/build_studio_plugin.py` | loader 1.1.0, 8 module, `LocalToken: placeholder` |
| Publicare (probă) | `python scripts/publish_release.py --dry-run` | `Scanare secrete: nimic găsit.`, trei pachete, versiune 1.0.0 |
| Repo curat de secrete | căutarea token-ului local și a celui de dispozitiv în tot arborele | zero potriviri; `.rbxmx` din pachet conține doar placeholder-ul |

Publicare reală: commit-ul inițial și pachetele 1.0.0 sunt pe `https://github.com/Ombra-Studios/roblox-studio-harness` (ramura `main`), iar manifestul upstream răspunde la `https://raw.githubusercontent.com/Ombra-Studios/roblox-studio-harness/main/releases/manifest.json` cu versiunea `1.0.0` și loader `1.1.0`.

Instalare locală pe PC-ul de dezvoltare: pluginul a fost reconstruit și instalat în `%LOCALAPPDATA%\Roblox\Plugins` cu token-ul local injectat (`LocalTokenInjected: true`, hash verificat, copie de siguranță păstrată), iar daemon-ul a fost repornit pe portul 34871 și raportează versiunea `1.0.0` cu funcțiile `identity`, `workspaces` și `panel` active. Hub-ul public răspunde deocamdată `HTTP 405`, pentru că `lostcube.pro/roblox/harness` încă nu rulează hub-ul; daemon-ul stă corect în starea `offline` și reîncearcă.

## 10. Proba în Studio real (14 septembrie 2026)

La prima pornire a Studio-ului cu pluginul 1.0 instalat, pluginul **nu se conecta** și nu scria nicio eroare. Cauza: ambele instalatoare (`scripts/install-studio-plugin.ps1` și `updater.inject_local_token`) înlocuiau placeholder-ul în **tot** fișierul, deci și în constantele `TOKEN_PLACEHOLDER` din sursa loader-ului și a lui `BridgeController`. Codul livrat devenea astfel egal cu „placeholderul”, iar `validToken` îl respingea: pluginul își respingea propriul cod și rămânea deconectat, tăcut.

Corecția: substituția atinge doar valoarea `StringValue`-ului `LocalToken` (o expresie care cere numele `LocalToken` imediat înaintea valorii), în ambele instalatoare; două sloturi într-un pachet sunt refuzate. Testele care verificau o înlocuire globală au fost rescrise pe invariantul corect — tokenul apare exact o dată, iar literalul rămâne în surse — și fixture-urile au acum forma pachetului real, cu sursă și slot.

| Verificare | Rezultat |
| --- | --- |
| Fișierul instalat | tokenul o singură dată, în valoarea `LocalToken`; ambele constante intacte |
| Suita Python | `Ran 558 tests` · `OK (skipped=1)` |
| Studio repornit (17:29) cu pluginul reinstalat (17:27) | `plugin_connected: true`, fără niciun cod tastat |
| Identitate citită din Studio | `ArchangelS0L` (userId 4324991580), avatar `rbxthumb://…` |
| Workspace detectat | `game:10766226591` · place 129160346456700 · creator Group 554257950 |
| Consola Studio | nicio eroare, niciun avertisment de la plugin |
| Provideri văzuți de daemon | Claude Code și Codex, ambii disponibili |

Smoke live al sistemului complet, pe porturi libere și stare temporară (hub + doi daemoni, fără Studio): 20 de verificări, toate trecute — înrolare în așteptare, aprobare cu codul de administrator, trecerea daemonilor în `approved`, identitate Roblox și detectarea a două jocuri diferite, sesiune din terminal ajunsă în workspace-ul corect, claim refuzat în același joc și acordat în altul, datele panoului, revocare propagată până în daemon. Generator: `%TEMP%\studio-harness-1.0\smoke-final\smoke.py`.

## 11. Instalatorul `.exe` (14 septembrie 2026)

`installer/StudioHarnessSetup.cs` se compilează cu `csc.exe` din .NET Framework 4 (`scripts/build-installer.ps1`) într-un executabil de 19 456 de octeți, fără dependențe și fără runtime de instalat. Face cele trei instalări dintr-o singură rulare: pluginul Roblox Studio, pluginul Claude Code (magazin local plus instalare la scop de utilizator) și configurația Codex.

| Verificare | Rezultat |
| --- | --- |
| `--help` | listează toate opțiunile, cod de ieșire 0 |
| `--dry-run` pe repo | parcurge cei trei pași, nu scrie nimic |
| Rulare reală lângă repo | toate trei instalate, cod de ieșire 0, plugin cu tokenul injectat |
| Rulare dintr-un folder izolat, doar cu executabilul | descarcă pachetul din canalul public, verifică suma (`03371a06…`, 393 KB), despachetează în `%LOCALAPPDATA%\StudioHarness\pachet` |
| Opțiune necunoscută | mesaj în română, cod de ieșire 2 |
| Teste automate | `tests/test_installer.py`, 10 teste: compilează sursa, rulează executabilul pe un pachet fals și verifică garanțiile (HTTPS impus, TLS 1.2, sumă verificată, plafon de mărime, refuzul căilor din afara folderului, zero tokenuri în sursă) |

Tot în această rundă a fost reparat un test fragil: `test_deploy.test_installer_is_valid_bash` alegea `C:\Windows\System32ash.exe` (lansatorul WSL) când suita pornea din PowerShell, iar acela iese cu 1 și fără mesaj când nu există nicio distribuție instalată. Testul caută acum primul `bash` care chiar rulează și se sare singur dacă nu există niciunul. Suita completă trece acum identic din Git Bash și din PowerShell: **571 de teste**.

