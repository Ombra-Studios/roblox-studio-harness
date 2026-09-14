# Contractul Studio Harness — 1.0.0

Documentul de referință pentru hub (`scripts/team_hub.py`), daemon (`scripts/studio_bridge.py`), clientul hub (`scripts/team_client.py`), pluginul Studio (`studio-plugin/`) și panoul web (`panel/index.html`). Sursa de decizie este `design/V1-BRIEF.md`; acest contract detaliază câmpuri, tipuri, valori implicite, limite și coduri, ca implementările să nu se contrazică. Sistemul de design (paletă, componente, wireframe-uri) este în `design/DESIGN.md`. Joburile Mod S (Studio) și formatul evenimentelor au baza în `STUDIO_BRIDGE_CONTRACT.md`; tot ce este descris aici are prioritate când cele două diferă.

Limba: texte UI, mesaje de eroare, comentarii și documente în română cu diacritice; identificatorii de cod, numele câmpurilor JSON și numele rutelor în engleză.

## 0. Ce este 1.0 și ce rămâne din 0.8

Studio Harness 1.0 = plugin Roblox Studio (Luau: loader cu hot swap + aplicație) + daemon local Python (`127.0.0.1:34871`) care rulează Claude Code / Codex din terminalul developerului sau din Studio + hub central Python (implicit `https://lostcube.pro/roblox/harness`) + panou web servit de hub.

Schimbări față de 0.8:

1. **Fără echipe.** Nu există `team.json`, token de echipă, „mod solo / mod echipă”, `Setup-Team.cmd`, `Start-Team-Hub.cmd`, rutele `/team/*`. Daemon-ul se conectează singur la `DEFAULT_HUB_URL`.
2. **Identitatea = contul Roblox** (nume + avatar), raportată de plugin, informativă. **Autorizarea = tokenul de dispozitiv** aprobat de admin în panou (sau înrolare deschisă).
3. **Workspace = jocul.** Sesiunile, claims-urile, jurnalul, membrii prezenți și harta proiectului sunt grupate pe cheia workspace-ului (§2).
4. **Panoul** are login prin dispozitiv sau cod de admin, ecran „în așteptare”, selector de workspace, file Activitate / Proiect / Dispozitive.
5. **Pluginul** primește tokenul UI automat din `.rbxmx`-ul instalat (`LocalToken`), se conectează singur și urmează tema Studio.

Rămân neschimbate ca semantică (reformulate aici): joburile `studio` și `terminal`, proxy-ul MCP scoped (`/agent/JOB/*`), evenimentele și cursoarele, aprobările per apel, providerii, impunerea claims-urilor în `/agent/JOB/call`, hot swap-ul pluginului (`/v1/plugin/bundle`), updater-ul și publicarea, harta proiectului (`project_map.py`), prefixul `/roblox/harness` (`strip_base`), rutele publice `/healthz`, `/panel`, `/releases/<f>`, deploy-ul Ubuntu (adaptat la codul de admin).

Versiuni 1.0.0: `scripts/studio_bridge.py` `VERSION`, `scripts/team_hub.py` `VERSION`, `.claude-plugin/plugin.json`, `Main.luau` (`-- Studio Harness App 1.0.0`). Loader-ul: `-- Studio Harness Loader 1.1.0`. Antetele HTTP `Server` sunt exact `StudioHarness/1.0` (daemon) și `StudioHarnessHub/1.0` (hub) — fără versiunea interpretului Python.

## 1. Identități și secrete

### 1.1 Fișiere locale ale daemon-ului

`local_state.state_dir()` = `%LOCALAPPDATA%\StudioHarness\` (Windows) sau `$XDG_STATE_HOME/studio-harness` (POSIX); `STUDIO_HARNESS_STATE_DIR` suprascrie.

| Fișier | Conținut | Cine îl folosește |
|---|---|---|
| `local-token` | tokenul UI plugin↔daemon: `secrets.token_urlsafe(32)` (43 de caractere, `[A-Za-z0-9_-]{16,512}`), creat o singură dată (`ensure_local_token`) | plugin (`X-Studio-Harness-Token`), shim MCP, hook-uri; injectat în pluginul instalat (§8.1) |
| `device-token` | tokenul de dispozitiv: `secrets.token_hex(32)` (64 hex), creat o singură dată (`local_state.ensure_device_token(dir)`); un fișier cu conținut invalid se regenerează (= dispozitiv nou, care va fi din nou `pending`) | daemon → hub (`X-Studio-Harness-Device`); daemon → browser (`#device=`, §9.1) |
| `config.json` | opțional: `{"hub_url": "https://…"}` pentru self-hosting; alt tip sau JSON invalid → `{}` | daemon |
| `daemon.log` | jurnalul daemon-ului pornit detașat de shim | diagnostic |
| `installed-loader.txt`, `backups/`, `plugin-backups/`, `cli-sessions/` | ca în 0.8 | updater, hook-uri |
| `team.json` | **eliminat**; dacă există, este ignorat, iar daemon-ul scrie o singură dată în log „team.json nu mai este folosit din 1.0: fișierul este ignorat (identitatea vine de la contul Roblox, accesul din tokenul de dispozitiv).” | — |

`local_state.py` expune `state_dir`, `ensure_token_file`, `ensure_local_token`, `read_local_token`, `ensure_device_token`,
`read_device_token`, `device_id_for`, `ensure_admin_token`, `read_config` (dicționar sau `{}`), `normalize_hub_url`,
`resolve_hub_url`, `hub_url`, plus constantele `DEFAULT_HUB_URL`, `HUB_URL_ENV`, `TOKEN_PATTERN`, `DEVICE_TOKEN_PATTERN`;
nu mai expune `ensure_team_token`, `read_team`, `developer_name`.

**Adresa hub-ului** (`resolve_hub_url(dir) -> (url | None, sursă)`, sursa ∈ `env | config | default`): prima valoare nevidă
dintre env `STUDIO_HARNESS_HUB_URL` și `config.json["hub_url"]`, altfel `DEFAULT_HUB_URL`. Validarea (`normalize_hub_url`):
schemă `https://` către orice gazdă sau `http://` **doar** către `127.0.0.1`/`localhost` (hub de test pe același PC),
fără utilizator:parolă, fără query sau fragment, fără spații ori caractere de control, ≤ 512 de caractere, bara finală
eliminată, schema și gazda normalizate. O valoare **setată** dar respinsă dă `None` (daemon-ul intră în `disabled`,
cu `error = "hub_url invalid (sursa: env|config)"`), nu implicitul public.

Niciun token nu apare în log-uri, în stdout-ul daemon-ului (care ajunge în `daemon.log`), în mesaje de eroare, în UI sau în documente. Daemon-ul afișează la pornire doar calea fișierelor.

### 1.2 Tokenul UI, tokenul de job, `bridge_id`

- **Token UI** (`X-Studio-Harness-Token`): toate rutele `/v1/*` cu excepția celor de sesiune din terminal cu token de job. Invalid → 401 `Codul local de asociere este invalid.`
- **Token de job** (`X-Studio-Harness-Job`): per job, `secrets.token_urlsafe(32)`, pentru `/agent/JOB/tools`, `/agent/JOB/call`, `/v1/terminal/sessions/JOB/events`, `/v1/terminal/sessions/JOB/close`. Nu ajunge niciodată la hub.
- **`bridge_id`**: per proces daemon (`uuid4().hex`). Pluginul invalidează referințele remote când se schimbă.
- Daemon-ul acceptă doar cereri cu `Host: 127.0.0.1:<port>` și fără antet `Origin` (403 altfel); `OPTIONS` → 403.

### 1.3 Tokenul de dispozitiv, `device_id`, stări

- Antet `X-Studio-Harness-Device: <64 hex>` pe rutele `/hub/*` de membru. Hub-ul stochează doar `token_hash = sha256(token).hexdigest()`; `device_id = token_hash[:16]`; `member_id == device_id` (terminologia 0.8 „membru” = dispozitiv aprobat).
- Stări: `pending` (înregistrat, neaprobat), `approved`, `revoked`. Înrolare (`enrollment`): `approve` (implicit; dispozitivele noi sunt `pending`) sau `open` (aprobare automată, `approved_by: "open"`).
- Un dispozitiv necunoscut este creat **numai** de `POST /hub/register`. Un dispozitiv `pending`/`revoked` poate apela numai `POST /hub/register` și `GET /hub/status` (care îi spun starea); orice altă rută de membru → 403 cu `status`.

### 1.4 Codul de administrator

- Fișier `hub-admin-token` în directorul de stare al hub-ului (`ensure_token_file`, `token_urlsafe(32)`), suprascris de env `STUDIO_HARNESS_ADMIN_TOKEN` (Dockerfile HF Space); afișat la pornire doar cu `--show-admin-code`, altfel se afișează calea fișierului. Nu există cod de admin în repo, pachete sau panou (panoul îl ține în `localStorage`).
- Antet `X-Studio-Harness-Admin: <cod>`. Un cod valid autorizează toate rutele `/hub/*` (membru + admin). Rutele care acționează „ca dispozitiv” (`/hub/register`, `/hub/sync`, `/hub/claims/*`) cer și antetul de dispozitiv; fără el → 400 `Ruta cere antetul dispozitivului.`
- Cod invalid (antet prezent, dar diferit) → 401 `Codul de administrator este invalid.` Comparațiile folosesc `hmac.compare_digest`.
- Codul generat de hub are 43 de caractere. Când vine din env (`STUDIO_HARNESS_ADMIN_TOKEN`) și are sub `RECOMMENDED_ADMIN_CODE` = 32 de caractere, hub-ul scrie la pornire „atenție: codul de administrator din STUDIO_HARNESS_ADMIN_TOKEN are sub 32 de caractere; folosește unul generat aleatoriu” — avertisment, nu refuz; codul însuși nu apare niciodată în log fără `--show-admin-code`.
- Când ambele antete sunt prezente și valide, cererea are contextul dispozitivului (`me`) și privilegii de admin (`admin: true`), indiferent de starea dispozitivului.

### 1.5 Identitatea Roblox (informativă)

Pluginul citește `StudioService:GetUserId()`; numele prin `pcall(Players.GetNameFromUserIdAsync)` (fallback `"user_" .. userId`); avatarul se derivă din `user_id`: `rbxthumb://type=AvatarHeadShot&id=<userId>&w=48&h=48` în Studio, `GET /hub/avatar?user=<id>` în panou. `user_id = 0` = identitate necunoscută: pluginul afișează numele „Studio”, daemon-ul trimite hub-ului `roblox: null`, iar hub-ul și panoul afișează numele mașinii.

Hub-ul reține identitatea Roblox pe dispozitiv (`roblox_user_id`, `roblox_name`), o afișează și o pune în sesiuni, claims și jurnal ca `developer`. Nu o verifică (contul Roblox nu este o credențială aici); autorizarea vine exclusiv din tokenul de dispozitiv.

### 1.6 Mașina

`machine` = `COMPUTERNAME` sau `platform.node()` (max 64 de caractere), trimisă la înregistrare și folosită ca nume afișat când nu există identitate Roblox.

## 2. Workspace-uri

### 2.1 Cheia

`project_map.workspace_key(game_id, place_id) -> str`:

- `"game:<game_id>"` dacă `game_id > 0`;
- altfel `"place:<place_id>"` dacă `place_id > 0`;
- altfel `"local"` (fișier nepublicat; toate fișierele nepublicate ale tuturor developerilor împart această cheie).

`None` se tratează ca 0 și un `float` cu valoare întreagă (JSON-ul venit din Luau) este acceptat; `bool`, text, alt tip,
o valoare negativă sau una peste `MAX_ID = 2^53 − 1` ridică `ValueError` (daemon-ul și hub-ul îl transformă în 400).

### 2.2 Meta

```json
{"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"}
```

`project_map.workspace_meta(body)` construiește meta dintr-un corp `/v1/identity` sau dintr-un meta deja calculat și este
singura sursă a cheii: **cheia se recalculează din id-uri**, deci una trimisă de client se ignoră (hub-ul revalidează cu
`workspace_meta` tot ce primește). `name` = `place_name` (sau `name`, când corpul este deja un meta), curățat de caractere de
control și scurtat la **120** de caractere; gol → cheia, la afișare. `creator_type` se normalizează la `"User" | "Group" |
"necunoscut"` (`project_map.CREATOR_UNKNOWN`), niciodată `null`. Câmpurile lipsă devin 0 și `""`; tipurile greșite ridică
`ValueError`. Hub-ul completează `first_seen`, `last_seen` și `project` (§3.2). Numele afișat al workspace-ului este `name`.

### 2.3 Prezența

Un dispozitiv este „prezent” într-un workspace dacă ultimul `workspace` raportat prin `register`/`sync` are acea cheie și este online (`last_seen` în ultimele **15 s**). `workspace: null` = prezent nicăieri (Studio închis). Sesiunile poartă cheia workspace-ului din momentul creării (§6.1) și rămân în acel workspace și după ce dispozitivul schimbă jocul.

## 3. Hub 1.0 (`scripts/team_hub.py`)

Clasa `Hub` (`TeamHub = Hub`, alias). Numele fișierului rămâne `team_hub.py` pentru continuitatea canalului de actualizare. Un singur proces; toate mutațiile sub `self.lock` (RLock). Fără dependențe externe. Ascultă implicit pe `127.0.0.1:34880`: expunerea în rețea se cere explicit (`--listen 0.0.0.0`) și este însoțită de un avertisment la pornire (§3.7).

### 3.1 Generalități HTTP

- Rutele se rezolvă după `strip_base(path)`: `BASE_PATH = "/roblox/harness"` este acceptat și eliminat (`/roblox/harness/hub/status` → `/hub/status`), ca rutele să meargă identic direct, prin proxy care scoate prefixul (Caddy `handle_path`, nginx `proxy_pass …/`) sau prin proxy care nu îl scoate.
- JSON UTF-8; cererile POST cer `Content-Type: application/json` (415 altfel), corp ≤ 4 MB (413), obiect JSON (400). Răspunsurile de eroare: `{"ok": false, "error": "<mesaj în română>", ...câmpuri suplimentare}`.
- Antete de răspuns JSON: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. Panoul: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` (ultimele trei directive nu moștenesc din `default-src`, iar `frame-ancestors` este protecția anti-încadrare respectată de browserele moderne), `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache`.
- `Origin` (`_check_origin`): acceptat numai când `netloc` coincide cu `Host`; altfel 403 `Accesul din pagini web nu este permis.`; `OPTIONS` → 403. Daemon-ul nu trimite `Origin`.
- Autorizare (ordinea verificărilor, după rutele publice): (1) antetul admin, dacă există, trebuie să fie valid (401); (2) antetul dispozitiv, dacă există, trebuie să aibă 64 hex (401 `Dispozitiv necunoscut; înregistrează-te.`, `status: "unknown"`) și să fie cunoscut, cu excepția `/hub/register`; (3) rutele `/hub/admin/*` cer admin (403 `Este necesar codul de administrator.`); (4) rutele de membru cer dispozitiv `approved` sau admin: `pending` → 403 `{"ok": false, "error": "Dispozitivul așteaptă aprobarea adminului.", "status": "pending", "device_id": "…"}`; `revoked` → 403 `{"ok": false, "error": "Dispozitivul a fost revocat de admin.", "status": "revoked", "device_id": "…"}`; niciun antet → 401 `status: "unknown"`. (5) rutele care acționează ca dispozitiv (`DEVICE_ROUTES` = `/hub/register`, `/hub/sync`, `/hub/claims/*`) cer antetul dispozitivului: fără niciun antet → 401 `status: "unknown"`; cu un cod de admin valid, dar fără antet de dispozitiv → 400 `Ruta cere antetul dispozitivului.`
- **Orice 401 pentru un dispozitiv necunoscut sau cu antet invalid conține `status: "unknown"`** — clientul (`team_client.HubClient`) tratează un 401 **fără** `status` ca pe un hub 0.8 și intră în `offline` cu „Hub-ul rulează o versiune mai veche.”
- Coduri: 200 ok; 202 înregistrare în așteptare; 204 avatar indisponibil; 302 rădăcină → panou; 400 corp/câmp invalid; 401 identitate necunoscută/cod invalid; 403 origin, pending, revoked, admin necesar; 404 rută inexistentă (inclusiv toate `/team/*`), dispozitiv/workspace/sesiune inexistente; 409 conflict de claim; 413 corp prea mare; 415 content-type; 500 `Eroare internă a hub-ului.` (fără detalii).
- Log-uri pe stdout, fără token-uri, fără conținutul sesiunilor: înregistrări, aprobări, revocări, offline, proiect primit, erori interne (doar tipul excepției). O deconectare bruscă a clientului (`BrokenPipeError`, `ConnectionResetError`, `ConnectionAbortedError`, `TimeoutError`) nu este o eroare a hub-ului: `HubServer.handle_error` o ignoră, în loc să scrie un traceback complet care ar ascunde liniile utile. Linia unui dispozitiv (`_label`) folosește numele Roblox când există, altfel numele mașinii — niciodată amândouă, duplicate.

### 3.2 Starea persistentă `hub-state.json` (versiunea 2)

Fișier în `--state-dir` (implicit `state_dir()`), scris atomic (`.part` + `os.replace`) la fiecare 30 s, la oprire și înainte de actualizare; ≤ 64 MB la încărcare. Pe POSIX atât fișierul final, cât și `.part` primesc permisiuni **0600** (`local_state.restrict_permissions`): un hub pornit manual pe un server partajat nu trebuie să lase `token_hash`-urile lizibile sub umask-ul implicit 022 (pe Windows apelul nu are efect).

```json
{"version": 2, "hub_version": "1.0.0", "saved": 1726000000.0, "hub_id": "<32 hex>", "enrollment": "approve", "journal_seq": 812,
 "devices": {"<device_id>": {"device_id": "<16 hex>", "token_hash": "<64 hex>", "roblox_user_id": 12345, "roblox_name": "ellob",
             "machine": "PC-ELLOB", "bridge_id": "<32 hex>", "version": "1.0.0", "status": "approved",
             "first_seen": 1726000000.0, "last_seen": 1726000300.0, "approved_at": 1726000010.0, "approved_by": "admin",
             "workspace": "game:987654"}},
 "workspaces": {"game:987654": {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555,
                "creator_type": "User", "first_seen": 1726000000.0, "last_seen": 1726000300.0,
                "project": {"digest": "snap-…", "snapshot_id": "snap-…", "count": 138, "truncated": false, "groups": {"graphics": 12},
                            "reported_by": "<device_id>", "at": 1726000200.0}}},
 "sessions": {"<job_id>": {"device_id": "<device_id>", "workspace": "game:987654", "meta": {}, "events": [], "closed_at": null}},
 "claims": {"game:987654": [{"path": "Workspace.Map.Zone3", "job_id": "<job_id>", "developer": "ellob", "since": 0, "reason": "…", "last_touch": 0, "device_id": "<device_id>"}]},
 "journal": [{"seq": 812, "time": 1726000250.0, "workspace": "game:987654", "device_id": "<device_id>", "developer": "ellob",
              "roblox_user_id": 12345, "machine": "PC-ELLOB", "job_id": "<job_id>", "provider": "claude", "tool": "multi_edit",
              "paths": ["ServerScriptService.Main"], "summary": "multi_edit · ServerScriptService.Main (2 editări)"}]}
```

Reguli:

- `version != 2` (inclusiv fișierele 0.8 cu `format: 1`) → starea se ignoră cu un mesaj în log, hub-ul pornește curat; `hub_id` se păstrează dacă fișierul vechi are unul valid (32 hex), ca daemon-urile să nu piardă cursoarele.
- `devices`: `token_hash` nu părăsește niciodată hub-ul; câmpurile numerice lipsă devin 0; `roblox_user_id` 0 și `roblox_name` gol = identitate necunoscută. Dispozitivele cu `status` necunoscut se ignoră.
- `claims`: câte un `ClaimTable` per workspace, restaurat cu `ClaimTable.restore(rows)` (rândurile expirate după `last_touch` + 600 s se pierd). `claims.py` rămâne modelul (nemodificat). La salvare fiecare rând primește și `device_id` (dispozitivul jobului, din `job_devices`), ca după repornire claims-urile să rămână ale aceluiași dispozitiv; câmpul nu face parte din `ClaimTable.export()` și nu apare în răspunsurile HTTP.
- `sessions`: meta + ultimele 256 de evenimente (în memorie se rețin 1024 per sesiune). Sesiunile fără dispozitiv cunoscut se ignoră. Sesiunile închise (`closed_at` setat) se rețin **30 min** (`SESSION_RETENTION = 1800`).
- `journal`: ultimele **1000** de intrări (`MAX_JOURNAL = 1000`); `journal_seq` = max(`journal_seq`, seq-urile din listă). Rândurile jurnalului au `time` (secunde Unix) și `seq` (global, monoton); brief-ul le numește `at` — contractul fixează `time`, pentru continuitate cu jurnalul daemon-ului.
- `workspaces[*].project` este doar sumarul; la repornire hub-ul cere din nou proiectul complet (`want_project`) primului dispozitiv prezent care raportează digest-ul.
- Membrii `online` = dispozitive `approved` cu `last_seen` ≤ 15 s. Un dispozitiv **aprobat** fără sync de peste **60 s** (`OFFLINE_SECONDS`) este „reaped” (dispozitivele `pending`/`revoked` nu se reap-uiesc, nu au nimic de eliberat): sesiunile lui neterminale devin `lost` (`closed_at = now`) și claims-urile lui sunt eliberate în toate workspace-urile; la revenire, sesiunile retrimise în sync devin din nou active.

### 3.3 Rute publice (fără antet)

| Rută | Răspuns |
|---|---|
| `GET /healthz` | `{"ok": true, "version": "1.0.0"}` |
| `GET /` | 302 → `panel` (relativ) sau `/roblox/harness/panel` (absolut) pentru `/roblox/harness` fără bară finală |
| `GET /panel`, `GET /panel/` | `panel/index.html` citit de pe disc la fiecare cerere (lângă `team_hub.py` sau în rădăcina repo-ului), cu antetele din §3.1; 404 dacă lipsește |
| `GET /releases/<f>` | canalul de actualizare: `manifest.json` (`no-cache`), `.zip`, `.sha256`, `.txt` (`public, max-age=3600`); nume `[A-Za-z0-9._-]{1,120}`, fără `..` |
| `GET /hub/avatar?user=<id>&size=48` | PNG proxied de la `https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=<id>&size=<s>x<s>&format=Png&isCircular=false` (JSON `data[0].imageUrl`, apoi imaginea); `size` ∈ {48, 60, 100} (implicit 48), `user` întreg > 0 (400 altfel); cache în memorie 1 h per (user, size), timeout 5 s per cerere, imagine ≤ 512 KB; la orice eșec 204 fără corp și `Cache-Control: no-store` (panoul desenează inițialele), eșecul fiind reținut **60 s** (`AVATAR_RETRY_SECONDS`) ca să nu insistăm; răspunsul 200 are `Cache-Control: public, max-age=3600` și `X-Content-Type-Options: nosniff`. Fetcher injectabil (`Hub(avatar_fetcher=…)`) pentru teste fără rețea. Ruta este publică pentru că `<img src>` nu poate trimite antete și imaginile sunt oricum publice pe Roblox; nu expune nimic despre hub. Fiind publică, hub-ul servește **doar conturile raportate de dispozitivele lui** (un `user` necunoscut primește 204 fără nicio cerere ieșită), cache-ul are un plafon real (`AVATAR_CACHE_ENTRIES`), iar cererile ieșite merg doar către gazdele din `AVATAR_HOSTS` (`thumbnails.roblox.com` și `tr.rbxcdn.com`), **fără a urma redirectări** (un 302 înseamnă 204). |

### 3.4 Rute de membru (dispozitiv `approved` sau admin)

Toate răspund `{"ok": true, ...}`. Câmpurile marcate „meta” folosesc §2.2.

#### `POST /hub/register`

Singura rută care acceptă un dispozitiv necunoscut (îl creează) sau `pending`/`revoked` (îi actualizează datele și îi spune starea). Corp:

```json
{"roblox": {"user_id": 12345, "name": "ellob"}, "machine": "PC-ELLOB", "bridge_id": "<32 hex>", "version": "1.0.0",
 "workspace": {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"}}
```

`roblox` = obiect sau `null` (`user_id` întreg ≥ 0, `name` ≤ 64 de caractere); `machine` obligatoriu (≤ 64); `bridge_id` obligatoriu (≤ 64); `version` ≤ 32; `workspace` = meta sau `null`.

| Situație | Cod | Răspuns |
|---|---|---|
| aprobat (sau înrolare `open`) | 200 | `{"ok": true, "status": "approved", "device_id": "…", "hub_id": "…", "enrollment": "approve"}` |
| nou sau încă în așteptare | 202 | `{"ok": true, "status": "pending", "device_id": "…", "hub_id": "…", "enrollment": "approve"}` |
| revocat | 403 | `{"ok": false, "status": "revoked", "device_id": "…", "error": "Dispozitivul a fost revocat de admin."}` |
| prea multe dispozitive în așteptare | 429 | `{"ok": false, "error": "Prea multe dispozitive în așteptare; contactează adminul."}` |

Lista de așteptare are plafon (`MAX_PENDING_DEVICES`): ruta este singura deschisă necunoscuților, deci un dispozitiv `pending` neatins de peste 24 h (`PENDING_IDLE_SECONDS`) face loc unuia nou, iar dacă nu există niciunul înregistrarea este refuzată cu 429. Dispozitivele aprobate nu ocupă loc în ea.

Efecte: `first_seen` la creare, `last_seen = now`, `bridge_id`, `version`, `roblox`, `machine`, `workspace` (și meta workspace-ului în `workspaces`, cu `first_seen`/`last_seen`). Un `bridge_id` nou al aceluiași dispozitiv (daemon repornit) nu creează un membru nou; sesiunile vechi ale dispozitivului rămân până sunt retrimise sau expiră.

#### `POST /hub/sync`

Corp (toate câmpurile opționale în afară de `workspace`):

```json
{"workspace": {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"},
 "roblox": {"user_id": 12345, "name": "ellob"},
 "sessions": [{"job_id": "<32 hex>", "kind": "terminal", "provider": "claude", "developer": "ellob", "name": "Claude Code · Ball",
               "state": "running", "studio_id": "studio-…", "cwd": "C:\\Proiecte\\Ball", "started": 1726000000.0,
               "last_activity": 1726000100.0, "pending_approval": false, "claims": ["Workspace.Map.Zone3"],
               "workspace": "game:987654", "events": [{"seq": 13, "type": "text", "text": "…"}]}],
 "want": {"<job_id al altui dispozitiv>": 12},
 "journal": [{"time": 1726000250.0, "job_id": "…", "provider": "claude", "tool": "multi_edit", "paths": ["ServerScriptService.Main"],
              "summary": "…", "workspace": "game:987654"}],
 "journal_after": 800, "touch": ["<job_id>"], "project_digest": "snap-…",
 "project": {"snapshot_id": "snap-…", "place_id": 1291603, "place_name": "Ball", "studio_id": "…", "count": 138, "taken": 0,
             "truncated": false, "groups": {}, "nodes": []}}
```

- `workspace`: meta sau `null` (obligatoriu ca cheie; `null` = prezent nicăieri). `roblox`: doar când s-a schimbat (obiect sau `null`); actualizează dispozitivul.
- `sessions`: ≤ 64 rânduri, doar sesiunile proprii neterminale plus cele `terminal` încheiate în ultimele 10 min; `events` = doar evenimentele noi de la ultimul sync reușit (cursor per job în client). Hub-ul acceptă un rând numai dacă `job_id` nu aparține altui dispozitiv; rescrie `developer` cu `roblox_name` (sau `machine`), adaugă `device_id`, `roblox_user_id`, `machine`, `remote: true`, `workspace` (cel din rând, altfel workspace-ul curent al dispozitivului, altfel `"local"`), și adaugă evenimentele cu `seq` > ultimul cunoscut (max 1024 reținute). Stare terminală → `closed_at = now` (o dată); stare neterminală → `closed_at = null`.
- `want`: `{job_id: after}` pentru sesiunile altora (≤ 64); răspunsul aduce evenimentele cu `seq > after`.
- `journal`: intrări locale noi; hub-ul le numerotează (`seq` global), adaugă `device_id`, `developer`, `roblox_user_id`, `machine` și `workspace` (din rând, altfel din sesiune, altfel curent).
- `journal_after`: cursorul clientului. `touch`: joburile ale căror claims se reînnoiesc (`ClaimTable.touch`).
- `project_digest`: `snapshot_id`-ul proiectului local (sau `null`); `project`: proiectul complet, trimis o singură dată după `want_project: true`, validat structural ca în 0.8 (`snapshot_id` ≤ 128, `nodes` ≤ 20 000 de liste cu 5 elemente, `groups` obiect); un proiect invalid este ignorat și digest-ul lui nu mai este cerut. Proiectul se atașează workspace-ului **curent** al dispozitivului.
- `want_project` este `true` când: hub-ul nu are proiectul workspace-ului, are doar sumarul salvat (după repornire), sau digest-ul anunțat diferă de cel memorat **și** raportorul memorat este chiar acest dispozitiv (rescanare proprie) ori au trecut ≥ `PROJECT_REFRESH_SECONDS` (300 s) de la ultima primire. Fiecare daemon are alt `snapshot_id` (GUID per scanare), deci fără această fereastră două PC-uri din același joc și-ar retrimite harta la fiecare sync.

Răspuns:

```json
{"ok": true, "now": 1726000301.0, "hub_id": "<32 hex>", "device": {"status": "approved"}, "workspace": "game:987654",
 "members": [{"device_id": "…", "roblox_user_id": 777, "roblox_name": "ana", "machine": "PC-ANA", "last_seen": 1726000300.0, "online": true}],
 "sessions": [{"job_id": "…", "kind": "studio", "provider": "codex", "developer": "ana", "name": "…", "state": "running", "studio_id": "…",
               "cwd": null, "started": 0, "last_activity": 0, "pending_approval": false, "claims": [], "workspace": "game:987654",
               "device_id": "…", "roblox_user_id": 777, "machine": "PC-ANA", "remote": true}],
 "events": {"<job_id>": [{"seq": 13, "type": "text", "text": "…"}]},
 "claims": [{"path": "Workspace.Map.Zone3", "job_id": "…", "developer": "ellob", "since": 0, "reason": "…", "expires": 0, "workspace": "game:987654"}],
 "journal": [{"seq": 812, "time": 0, "workspace": "game:987654", "device_id": "…", "developer": "ellob", "roblox_user_id": 12345,
              "machine": "PC-ELLOB", "job_id": "…", "provider": "claude", "tool": "multi_edit", "paths": [], "summary": "…"}],
 "journal_seq": 812, "want_project": false,
 "workspaces": [{"key": "game:987654", "name": "Ball", "game_id": 987654, "place_id": 1291603, "creator_id": 555, "creator_type": "User",
                 "last_seen": 0, "members_online": [{"device_id": "…", "roblox_user_id": 777, "roblox_name": "ana", "machine": "PC-ANA", "last_seen": 0, "online": true}],
                 "sessions_active": 1, "claims": 1, "project": {"count": 138, "digest": "snap-…", "groups": {"graphics": 12}}}]}
```

- `members`: dispozitivele prezente în workspace-ul curent (inclusiv apelantul); `[]` când `workspace` este `null`.
- `sessions`: sesiunile **altor** dispozitive din workspace-ul curent (neterminale + închise în ultimele 30 min), sortate după `started`.
- `events`: doar pentru joburile din `want` care aparțin altora și sunt în workspace-ul curent.
- `claims`: toate claims-urile workspace-ului curent (`ClaimTable.snapshot()` + `workspace`).
- `journal`: intrările workspace-ului curent cu `seq > journal_after`, **cele mai recente 50**; `journal_seq` = ultimul seq global (clientul îl adoptă drept cursor și nu recuperează golurile; `/hub/workspace` oferă ultimele 200).
- `workspaces`: sumarul tuturor workspace-urilor (§3.4 `/hub/workspaces`).
- Un `hub_id` diferit de cel cunoscut de client înseamnă hub repornit fără stare: clientul se reînregistrează și retrimite evenimentele de la cursor 0.

#### `GET /hub/status`

Accesibil și dispozitivelor `pending`/`revoked` (își află starea). Răspuns:

```json
{"ok": true, "version": "1.0.0", "hub_id": "…", "enrollment": "approve", "now": 0, "admin": false,
 "device": {"device_id": "…", "status": "pending", "roblox_user_id": 12345, "roblox_name": "ellob", "machine": "PC-ELLOB"},
 "workspaces": 3, "members_online": 2}
```

`device` este `null` când cererea vine doar cu cod de admin; `admin: true` când codul de admin este valid.

#### Validarea a ce se stochează din sync

Hub-ul nu păstrează structuri arbitrare trimise de un dispozitiv:

- meta sesiunii: doar câmpurile din contract; textele (`job_id`, `kind`, `provider`, `developer`, `name`, `state`, `studio_id`, `cwd`) se taie la `MAX_META_TEXT` = 200, `started`/`last_activity` trebuie să fie numere (altfel `null`), `pending_approval` devine boolean, `claims` este o listă de cel mult `MAX_META_CLAIMS` = 32 de căi (≤ 512 caractere); restul câmpurilor se ignoră;
- evenimente: fiecare eveniment serializat trebuie să încapă în `MAX_EVENT_BYTES` = 64 000 de caractere; unul mai mare este înlocuit cu o notă scurtă („Evenimentul depășește limita hub-ului și a fost omis.”) care păstrează `seq`-ul, ca să nu apară goluri în cursorul clientului. Numărul de evenimente pe cerere nu este plafonat separat: `MAX_BODY` (4 MB) mărginește cererea, iar inelul de `MAX_EVENTS` per sesiune mărginește memoria;
- jurnal: cel mult `MAX_SYNC_JOURNAL` = 32 de intrări pe sync; `tool` ≤ 80, `summary` ≤ 300, `job_id` ≤ 128, `provider` ≤ 40, `paths` = listă de cel mult 32 de căi (≤ 512); `paths` este **mereu** o listă (goală când lipsește), celelalte câmpuri lipsă rămân `null`;
- sesiuni: un dispozitiv poate ține cel mult `MAX_DEVICE_SESSIONS` = 64 de sesiuni; o sesiune nouă peste plafon elimină cea mai veche sesiune închisă a lui, iar dacă nu există niciuna cererea primește 429 `Prea multe sesiuni pentru acest dispozitiv; închide una înainte de a deschide alta.`;
- workspace-uri: peste `MAX_WORKSPACES` = 512 se uită cele inactive (fără sesiuni, claims sau dispozitive), cele mai vechi întâi;
- `NaN`/`Infinity` în corpul JSON → 400 `JSON invalid.` (altfel starea nu ar mai putea fi salvată).

#### `GET /hub/workspaces`

`{"ok": true, "workspaces": [<sumar>]}` — rândurile de sumar de mai sus (`members_online` este o **listă** de rânduri de prezență, fiecare cu `online: true`; `sessions_active` = sesiuni neterminale; `claims` = număr; `project` = `{count, digest, groups}` sau `null`), sortate după `last_seen` descrescător, apoi după nume și cheie.

#### `GET /hub/workspace?key=<cheie>`

```json
{"ok": true, "workspace": {"key": "…", "game_id": 0, "place_id": 0, "name": "…", "creator_id": 0, "creator_type": "necunoscut", "first_seen": 0, "last_seen": 0},
 "members": [<prezenți>], "sessions": [<meta ale tuturor dispozitivelor din workspace>], "claims": [<rânduri>],
 "journal": [<ultimele 200 ale workspace-ului>], "project": {"count": 138, "digest": "…", "groups": {}, "reported_by": "…", "at": 0, "truncated": false} | null}
```

`key` lipsă → 400; necunoscut → 404 `Workspace-ul nu există.`

#### `GET /hub/project?key=<cheie>`

`{"ok": true, "project": {<proiectul complet: snapshot_id, place_id, place_name, studio_id, count, taken, truncated, groups (cu entries), nodes, reported_by, developer, machine, received>}}`; `project: null` când hub-ul are doar sumarul (după repornire) sau nimic. Răspunsul poate avea câțiva MB; panoul îl cere la 30 s sau la schimbarea `digest`-ului.

#### `GET /hub/panel-data?workspace=<cheie>`

Tot ce afișează panoul într-un apel (la 2 s):

```json
{"ok": true,
 "me": {"device_id": "…", "status": "approved", "roblox_user_id": 12345, "roblox_name": "ellob", "machine": "PC-ELLOB",
        "workspace": "game:987654", "admin": false},
 "hub": {"hub_id": "…", "version": "1.0.0", "enrollment": "approve", "now": 0},
 "workspaces": [<sumar>], "selected": {<răspunsul /hub/workspace fără "ok">} | null, "pending_devices": 1}
```

`workspace` lipsă → workspace-ul curent al dispozitivului apelant (sau `null`); necunoscut → `selected: null` (nu 404). `pending_devices` apare doar pentru admin. Doar cod de admin: `me = {"device_id": null, "status": "admin", "roblox_user_id": 0, "roblox_name": "admin", "machine": "", "workspace": null, "admin": true}`.

`me.workspace` este cheia ultimului workspace raportat de dispozitiv (sau `null`): panoul îl folosește ca să marcheze workspace-ul propriu și când dispozitivul nu mai este în prezență (prezența ține doar `ONLINE_SECONDS`).

#### `GET /hub/session?job=<job_id>&after=<seq>`

`{"ok": true, "meta": {<meta sesiune>}, "events": [<seq > after>], "last_seq": 40}`; `after` implicit 0; sesiune necunoscută → 404 `Sesiunea nu există.` Fluxul unei sesiuni în panou (poll la 2 s cât timp este deschis).

#### Claims

Toate cer antetul dispozitivului, chiar și cu cod de admin (§1.4). `workspace` este opțional, dar implicitul diferă:

- `claim` și `wait` lucrează într-un singur workspace: cel din corp, altfel cel **curent** al dispozitivului; fără niciunul → 400 `Dispozitivul nu are un workspace curent.`
- `release` și `touch` fără `workspace` acoperă **toate** workspace-urile jobului (un job poate avea claims rămase într-un workspace pe care dispozitivul l-a părăsit); cu `workspace` acționează doar acolo.

Un `job_id` raportat deja de alt dispozitiv → 403 `Sesiunea aparține altui dispozitiv.` (`claim`, `release`, `touch`). Conflictele se verifică doar în același workspace; `@play:<studio_id>` și `@all` sunt tot per workspace.

| Rută | Corp | Răspuns |
|---|---|---|
| `POST /hub/claims/claim` | `{"job_id", "paths": [1–32 texte], "reason"?: "≤200", "workspace"?}` | `{"ok": true, "claimed": [căi], "held": [căi]}` sau 409 `{"ok": false, "error": "Conflict de claim.", "conflicts": [{"path", "holder": "<job_id>", "developer", "held_path", "since"}]}` |
| `POST /hub/claims/release` | `{"job_id", "paths"?: [texte], "workspace"?}` | `{"ok": true, "released": [căi], "held": [căi]}` |
| `POST /hub/claims/touch` | `{"job_id", "workspace"?}` | `{"ok": true, "held": [căi]}` |
| `POST /hub/claims/wait` | `{"path", "timeout_seconds": ≤ 60, "workspace"?}` | `{"ok": true, "free": bool}` (ține conexiunea până la 60 s) |

`developer` al claim-ului = `roblox_name` (sau `machine`) al dispozitivului; `job_id` trebuie să fie un text ≤ 128. Atomic (toate sau nimic) sub lock-ul hub-ului; expiră la 600 s fără `touch`.

#### Limitarea încercărilor de autorizare

Singurul secret ghicibil este codul de administrator, deci doar cererile care prezintă antetul `X-Studio-Harness-Admin` sunt
contorizate: după `AUTH_FAIL_LIMIT` = 10 coduri greșite de la aceeași adresă într-o fereastră glisantă de `AUTH_FAIL_WINDOW` = 60 s,
orice cerere cu antet de admin de la acea adresă primește 429 `Prea multe încercări de autorizare; încearcă din nou peste un minut.`
Cererile de dispozitiv nu sunt afectate (în spatele unui proxy toți clienții împart aceeași adresă, iar un token de dispozitiv de
64 hex nu se ghicește; un 401 `unknown` este normal după o repornire fără stare). Jurnalul scrie o singură linie, fără niciun
fragment de cod.

### 3.5 Rute de administrator (antet admin obligatoriu)

| Rută | Corp | Răspuns / efect |
|---|---|---|
| `GET /hub/admin/devices` | — | `{"ok": true, "devices": [{"device_id", "roblox_user_id", "roblox_name", "machine", "bridge_id", "version", "status", "first_seen", "last_seen", "approved_at", "approved_by", "workspace", "online"}]}` fără `token_hash`, sortate `pending`, `approved`, `revoked`, apoi după `roblox_name`/`machine` |
| `POST /hub/admin/devices/approve` | `{"device_id"}` | → `approved` (`approved_at = now`, `approved_by = "admin"`); `{"ok": true, "device": {…}}`; deja aprobat → același răspuns |
| `POST /hub/admin/devices/revoke` | `{"device_id"}` | → `revoked`; sesiunile lui dispar din tablă, claims-urile lui se eliberează; `{"ok": true, "device": {…}}` |
| `POST /hub/admin/devices/forget` | `{"device_id"}` | șterge dispozitivul, sesiunile, claims-urile și prezența; `{"ok": true}`; poate reveni ca `pending` la următorul `register` |
| `POST /hub/admin/enrollment` | `{"mode": "approve" \| "open"}` | schimbă înrolarea (persistată); `{"ok": true, "enrollment": "open"}` |

`device_id` necunoscut → 404 `Dispozitivul nu există.`; `mode` invalid → 400.

### 3.6 Limite și constante

| Constantă | Valoare |
|---|---|
| `ONLINE_SECONDS` | 15 |
| `OFFLINE_SECONDS` (reap) | 60 |
| `SESSION_RETENTION` | 1800 s |
| `MAX_EVENTS` / `SAVED_EVENTS` | 1024 / 256 per sesiune |
| `MAX_JOURNAL` / `SYNC_JOURNAL` / `WORKSPACE_JOURNAL` | 1000 / 50 / 200 |
| `MAX_SYNC_SESSIONS` | 64 |
| `MAX_WAIT` | 60 s |
| `MAX_BODY` | 4 MB |
| `MAX_PROJECT_NODES` | 20 000 |
| `SAVE_INTERVAL` | 30 s |
| `PROJECT_REFRESH_SECONDS` | 300 s (harta cerută altui dispozitiv decât raportorul) |
| `AVATAR_RETRY_SECONDS` | 60 s (un eșec al avatarului nu se reia mai devreme) |
| `AVATAR_CACHE_ENTRIES` | 256 (plafon real: peste el se elimină intrările cele mai apropiate de expirare) |
| `MAX_PENDING_DEVICES` / `PENDING_IDLE_SECONDS` | 64 / 24 h |
| `MAX_DEVICE_SESSIONS` | 64 per dispozitiv |
| `MAX_EVENT_BYTES` | 64 000 de caractere per eveniment |
| `MAX_SYNC_JOURNAL` / `MAX_META_TEXT` / `MAX_META_CLAIMS` | 32 / 200 / 32 |
| `MAX_WORKSPACES` | 512 (cele inactive se uită) |
| `AUTH_FAIL_LIMIT` / `AUTH_FAIL_WINDOW` | 10 coduri de admin greșite / 60 s → 429 |
| `RECOMMENDED_ADMIN_CODE` | 32 (sub atât, un cod din env primește un avertisment la pornire) |
| `CLAIM_IDLE_SECONDS` | 600 (din `claims.py`) |
| avatar: cache 3600 s, timeout 5 s, ≤ 512 KB | |

### 3.7 CLI

`python scripts/team_hub.py [--listen ADRESĂ] [--port 34880] [--state-dir DIR] [--no-auto-update] [--inherit-socket FD] [--show-admin-code] [--open-enrollment] [--approve-pending]`

- `--listen`: implicit **`127.0.0.1`**. Hub-ul vorbește HTTP simplu, deci expunerea în rețea se cere explicit (`--listen 0.0.0.0`); orice adresă în afara buclei locale (`127.0.0.1`, `::1`, `localhost`) scrie la pornire linia „atenție: hub-ul ascultă în rețea pe HTTP simplu (<adresă>); folosește-l doar în spatele unui proxy HTTPS”. Deploy-ul rămâne explicit în ambele sensuri: unitul systemd pornește cu `--listen 127.0.0.1` (HTTPS îl dă Caddy/nginx; `install.sh --public` îl schimbă în `0.0.0.0`), iar containerele — `Dockerfile`-ul din rădăcină (HF Space) și `deploy/ubuntu/Dockerfile` — cu `--listen 0.0.0.0`, pentru că altfel portul publicat nu ar ajunge la proces; expunerea lor se controlează din `-p 127.0.0.1:34880:34880` sau din proxy-ul platformei.
- `--show-admin-code`: afișează codul o dată la pornire (altfel doar calea `hub-admin-token`).
- `--open-enrollment`: pornește cu `enrollment = "open"` (persistat; panoul îl poate schimba ulterior).
- `--approve-pending`: one-shot. Încearcă întâi hub-ul în execuție (`POST http://127.0.0.1:<port>/hub/admin/devices/approve` pentru fiecare dispozitiv din `GET /hub/admin/devices`, cu codul din fișier/env); dacă hub-ul nu răspunde deloc, modifică direct `hub-state.json` (`approved_by: "cli"`) — în acest caz hub-ul trebuie să fie oprit. Dacă hub-ul **răspunde** dar refuză (cod de administrator greșit, HTTP 401/403), starea nu se atinge: comanda scrie motivul și iese cu **1**. La fel când hub-ul (sau un proxy intermediar) răspunde cu un corp care nu este JSON — răspuns trunchiat, pagină de eroare a proxy-ului: `HubError` 502 „Hub-ul în execuție a răspuns neinteligibil; nu editez starea sub un hub activ.”, fără nicio editare a stării (un proces activ ar suprascrie-o la următoarea salvare). Altfel afișează numărul aprobat și iese cu 0.
- `--team-token`, `--show-token`, `STUDIO_HARNESS_TEAM_TOKEN` dispar.
- Log la pornire: versiune, adresă, director de stare, calea codului de admin, modul de înrolare, actualizare automată activă/inactivă, plus avertismentul de mai sus când adresa nu este de buclă locală. Fără token-uri.

### 3.8 Actualizare fără întrerupere

Ca în 0.8: verificare la fiecare oră din `update-channel.json` (`upstream_manifest_url`, altfel `manifest_url`); când există o versiune nouă cu `auto`, hub-ul oglindește pachetele în `releases/` (`updater.mirror`, rescriind URL-urile spre `manifest_url` public), aplică pachetul `hub` (`apply_bundle`, `flatten="scripts/"`), salvează starea și pe POSIX se relansează cu `os.execv` și socketul moștenit (`--inherit-socket`), pe Windows se închide pentru a fi repornit de systemd/docker/Popen. `panel/index.html` este citit la fiecare cerere, deci panoul se actualizează instantaneu.

Despachetarea (`updater._members`) elimină folderul de top al arhivei și refuză orice intrare care ar ieși din directorul de staging: verificarea se face pe **toate** segmentele, nu doar pe primul (după eliminarea folderului de top, un `pachet-1.0/C:/rău.py` ar deveni pe Windows o cale absolută), iar calea rezultată este validată încă o dată cu ambele gramatici de căi. Fișierele lăsate la rădăcina arhivei, în afara folderului de top, sunt ignorate.

## 4. Daemon 1.0 (`scripts/studio_bridge.py`)

### 4.1 Pornire și configurare

`python scripts/studio_bridge.py [--port 34871] [--runtime-dir DIR] [--state-dir DIR] [--no-hub]`

1. `token = ensure_local_token(state)`; `device_token = ensure_device_token(state)`.
2. `hub_url`: `local_state.resolve_hub_url(state)` — env `STUDIO_HARNESS_HUB_URL` → `config.json["hub_url"]` → `DEFAULT_HUB_URL = "https://lostcube.pro/roblox/harness"`; `hub_source` ∈ `env | config | default` (sau `explicit` / `no-hub` când `Bridge` primește direct `hub_url`). Se acceptă `https://` către orice gazdă și `http://` doar către `127.0.0.1`/`localhost`; bara finală se elimină (§1.1). `--no-hub` sau o valoare setată și respinsă → `hub.status = "disabled"`, cu `error = "hub_url invalid (sursa: env|config)"` pentru a doua situație și `"Hub dezactivat (daemon pornit cu --no-hub)."` pentru prima.
3. `HubClient` (§5) pornește într-un fir separat; claims-urile sunt delegate hub-ului când `status == "approved"`, altfel unei `ClaimTable` locale (§6.3).
4. Stdout la pornire: versiune, URL local, calea `local-token` și `device-token` (nu conținutul), URL-ul hub-ului, „Pluginul Studio se conectează automat cu tokenul instalat”.
5. `team.json` este ignorat: dacă fișierul există, daemon-ul scrie **o singură dată** în log „team.json nu mai este folosit din 1.0: fișierul este ignorat (identitatea vine de la contul Roblox, accesul din tokenul de dispozitiv).” Fără `developer_name`: `developer` = numele Roblox din identitate (`user_id > 0`), altfel `machine`.
6. Constructorul: `Bridge(providers, runtime_dir=None, native=None, token=None, developer=None, state_directory=None, *, hub_url=UNSET, device_token=None, hub_client_factory=None, open_url=None, logger=None)`. `hub_url=UNSET` (implicit) rezolvă adresa ca la punctul 2; `hub_url=None` = `--no-hub`; un text = `explicit`. `hub_client_factory(bridge, hub_url, device_token, machine, disabled_error=…)` și `open_url` (implicit `webbrowser.open`) sunt injectabile pentru teste — testele nu ating hub-ul real.

Stările hub-ului (`hub.status`): `connecting` (prima înregistrare în curs) → `pending` (202; reîncearcă `register` la 15 s) → `approved` (sync la 2 s) / `offline` (rețea, 5xx sau 404 pe `/hub/*`; backoff 2 → 4 → 8 → 16 → 30 s) / `revoked` (403 revoked; reîncearcă la 60 s) / `disabled`. La 404 `error = "Hub-ul rulează o versiune mai veche."`; la 401 `unknown` clientul se reînregistrează imediat.

### 4.2 HTTP local (`http://127.0.0.1:34871`)

Autentificare: token UI, cu excepția `/agent/JOB/*` și `/v1/terminal/sessions/JOB/{events,close}` (token de job). Rutele apelate exclusiv de plugin (`/v1/board`, `/v1/jobs/poll`, `/v1/chat`, `/v1/jobs/*`, `/v1/default-studio`, `/v1/project/chunks`, `/v1/plugin/bundle`, `/v1/identity`, `/v1/panel/open`) marchează pluginul ca fiind conectat (`plugin_connected` = un apel în ultimele 10 s).

| Metodă și rută | Antet | Rol |
|---|---|---|
| `GET /v1/status` | UI | stare completă (§4.3) |
| `POST /v1/identity` | UI | identitatea Roblox și jocul deschis (§4.4) |
| `POST /v1/panel/open` | UI | deschide panoul în browser (§4.5) |
| `GET /v1/board` | UI | tabla (§4.6) |
| `GET /v1/hub/workspaces`, `GET /v1/hub/workspace?key=` | UI | proxy read-only spre hub (§4.7) |
| `GET /v1/studios` | UI | `{"ok": true, "studios": [{"id", "name"}], "connected": bool}` |
| `POST /v1/default-studio` | UI | `{"studio_id"}` → `{"ok": true}`; suprascriere manuală („Avansat”); 409 dacă nu este conectată |
| `POST /v1/chat` | UI | job `studio` (Mod S), ca în `STUDIO_BRIDGE_CONTRACT.md`; răspuns `{"ok", "job_id", "session_id", "state", "workspace"}` |
| `POST /v1/jobs/poll` | UI | ≤ 16 joburi, cursor per job; servește și sesiunile remote din oglindă (read-only) |
| `GET /v1/jobs/JOB/events?after=N` | UI | compatibil 0.3 |
| `POST /v1/jobs/JOB/approve` | UI | `{"approval_id", "allow"}`; 409 aprobare inactivă; 403 pe job remote |
| `POST /v1/jobs/JOB/cancel` | UI | `{}` (acceptă și `[]`); eliberează claims; 403 pe job remote |
| `POST /v1/jobs/JOB/release` | UI | `{"paths"?}` → `{"ok": true, "released": [căi]}`; 403 pe job remote |
| `POST /v1/terminal/sessions` | UI | `{"provider", "cwd"?, "host_pid"?, "cli_session_id"?, "name"?}` → `{"ok", "job_id", "job_token", "bridge_id", "studio_id", "developer", "workspace"}`; același `host_pid` activ → aceeași sesiune; ≤ 64 sesiuni deschise (429) |
| `POST /v1/terminal/sessions/JOB/events` | job | `{"type": "prompt"\|"text"\|"status", "text"}` (≤ 256 KB, fragmentat la 64 KB) → `{"ok": true}`; 409 dacă sesiunea nu mai este activă |
| `POST /v1/terminal/sessions/JOB/close` | job | → `completed`, claims eliberate |
| `POST /v1/project/chunks` | UI | inventarul în chunk-uri (§8.5) |
| `GET /v1/project` | UI | `{"ok": true, "project": {...} \| null}` |
| `GET /v1/plugin/bundle` | UI | `{"ok", "version", "revision", "entry": "Main", "source": "app"\|"repo", "modules": {"Nume": "sursă"}}`; 404 fără module |
| `GET /agent/JOB/tools`, `POST /agent/JOB/call` | job | proxy MCP (§7) |
| `POST /v1/provider/check`, `POST /v1/provider/login` | — | 404 (nu există login în plugin) |

Erori: `{"ok": false, "error": "…"}` cu 400/401/403/404/409/410/413/415/429/503/500 ca în 0.8; `RuntimeError`/`OSError` din MCP-ul oficial → 503 cu mesaj.

### 4.3 `GET /v1/status`

```json
{"ok": true, "version": "1.0.0", "bridge_id": "<32 hex>",
 "features": {"queued_sessions": true, "batch_events": true, "terminal_sessions": true, "claims": true, "board": true,
              "project": true, "plugin_bundle": true, "identity": true, "workspaces": true, "panel": true},
 "providers": {"claude": {"available": true}, "codex": {"available": false}},
 "active_job_id": null, "queued_count": 0, "default_studio_id": null, "plugin_connected": true,
 "developer": "ellob", "machine": "PC-ELLOB",
 "identity": {"user_id": 12345, "name": "ellob", "avatar": "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48"},
 "workspace": {"key": "game:987654", "game_id": 987654, "place_id": 1291603, "name": "Ball", "creator_id": 555, "creator_type": "User"},
 "hub": {"url": "https://lostcube.pro/roblox/harness", "status": "approved", "hub_id": "<32 hex>", "device_id": "<16 hex>",
         "error": null, "last_sync": 1726000300.0, "enrollment": "approve"},
 "panel_url": "https://lostcube.pro/roblox/harness/panel",
 "members": [{"device_id": "…", "roblox_user_id": 12345, "roblox_name": "ellob", "machine": "PC-ELLOB", "last_seen": 0, "online": true, "me": true}],
 "workspaces": [{"key": "game:987654", "name": "Ball", "members_online": 2, "sessions_active": 1, "claims": 0, "mine": true}],
 "update": {"current": "1.0.0", "available": null, "state": "idle", "message": "", "checked": null, "restart_required": false},
 "project": {"snapshot_id": "snap-…", "count": 138, "taken": 0, "place_name": "Ball"},
 "plugin_bundle": {"version": "1.0.0", "revision": "<16 hex>", "source": "app", "modules": 8}}
```

- `features.team` și `team` dispar. Pluginul este compatibil când `queued_sessions`, `batch_events`, `terminal_sessions`, `claims`, `board`, `identity`, `workspaces`, `panel` sunt `true`.
- `identity`: `null` până la primul `POST /v1/identity`; `user_id = 0` → `{"user_id": 0, "name": "Studio", "avatar": null}`.
- `workspace`: meta sau `null`. `members`: rândurile de prezență ale hub-ului (§3.4, deci cu `online: true`), plus `me` pentru dispozitivul propriu; `[]` fără hub aprobat. `workspaces`: sumar condensat pentru plugin (`members_online` este aici un **număr**; `mine` = cheia curentă).
- `hub.device_id` este `device_id`-ul hub-ului (16 hex), nu tokenul. `hub.error`: text în română sau `null`. `hub.last_sync`: momentul ultimului sync reușit sau `null`.
- `panel_url` = `hub_url + "/panel"` (fără token) sau `null` când hub-ul este `disabled`.
- `update`, `project`, `plugin_bundle`: ca în 0.8.

### 4.4 `POST /v1/identity`

Corp (toate câmpurile cu valori implicite: întregi 0, texte goale):

```json
{"user_id": 12345, "name": "ellob", "place_id": 1291603, "game_id": 987654, "place_name": "Ball", "creator_id": 555, "creator_type": "User"}
```

Validare: `user_id`, `place_id`, `game_id`, `creator_id` întregi ≥ 0 (400 altfel); `name` ≤ 64; `place_name` ≤ 200; `creator_type` ∈ `"User" | "Group" | null`. Răspuns `{"ok": true, "workspace": <meta>, "identity": <identity>}`. Efecte: `identity` și `workspace` din status se actualizează; dacă s-a schimbat contul sau cheia workspace-ului, `HubClient.wake()` → sync imediat; joburile fără workspace primesc cheia curentă (§6.1); Studio-ul țintă se reevaluează (§4.9). Pluginul trimite la pornire, la 30 s și la schimbarea `game.PlaceId` / `game.GameId` / `game.Name`.

### 4.5 `POST /v1/panel/open`

Corp `{}`. Daemon-ul deschide browserul implicit (`webbrowser.open`) la `hub_url + "/panel#device=" + device_token` și răspunde `{"ok": true, "url": "<hub_url>/panel"}` (fără token). 409 `Hub-ul este dezactivat pe acest PC.` când `hub.status == "disabled"`. Funcționează și în `pending`/`offline` (panoul afișează starea corespunzătoare). Deschiderea browserului este injectabilă (`Bridge(open_url=…)`) pentru teste.

### 4.6 `GET /v1/board`

```json
{"ok": true, "bridge_id": "…", "studios": [{"id", "name"}], "connected": true, "default_studio_id": null,
 "developer": "ellob", "identity": {…}, "workspace": {…} | null, "hub": {…ca în status…},
 "members": [{…, "me": true}], "workspaces": [{…condensat…}],
 "sessions": [{"job_id", "kind", "provider", "developer", "name", "state", "studio_id", "cwd", "started", "last_activity",
               "claims": [], "pending_approval": false, "workspace": "game:987654", "device_id": "…", "roblox_user_id": 12345,
               "machine": "PC-ELLOB", "remote": false, "mine": true}],
 "claims": [{"path", "job_id", "developer", "since", "reason", "expires", "workspace", "mine": true}],
 "journal": [<ultimele 40 ale workspace-ului curent>]}
```

- `sessions` = joburile proprii neterminale + sesiunile `terminal` proprii încheiate în ultimele 10 min + sesiunile altora din workspace-ul curent (`remote: true`, `mine: false`). Pentru joburile proprii `device_id` = `hub.device_id` (sau `null` fără hub).
- `claims` = claims-urile workspace-ului curent (din oglinda hub-ului) sau, când hub-ul nu este `approved`, rândurile tabelei locale; rândurile locale primesc `workspace` = cheia workspace-ului jobului (sau `null` când jobul nu are încă una). `mine` = `job_id` aparține unui job propriu.
- `journal` = oglinda hub-ului pentru workspace-ul curent (ultimele 40) sau jurnalul local când hub-ul nu este aprobat.
- Un poll la ~2 s din plugin; `studios` folosește cache-ul când MCP-ul oficial este ocupat.

### 4.7 Proxy read-only spre hub

`GET /v1/hub/workspaces` → răspunsul complet al hub-ului (`{"ok": true, "workspaces": [...]}`); `GET /v1/hub/workspace?key=` → răspunsul `GET /hub/workspace?key=` (fără `key` → 400 `Parametrul key lipsește.`). 503 `Hub-ul nu este disponibil (stare: <status>).` când `hub.status != "approved"` (clientul ridică `HubError` 503 fără să atingă rețeaua).

Codurile hub-ului se propagă **doar** pentru 400, 404, 409, 413 și 415 (`PROXY_PASSTHROUGH`); 401, 403 și 5xx devin 503, altfel pluginul ar interpreta refuzul hub-ului ca refuz al daemon-ului și s-ar deconecta. Rezultatele se memorează 2 s (cel mult 64 de intrări) și cache-ul se golește la fiecare schimbare de stare a hub-ului. Folosite de plugin pentru „Alte workspace-uri”.

### 4.8 Joburi, stări, evenimente

Ca în 0.8, plus `workspace` pe fiecare job:

- Feluri: `studio` (Mod S, `POST /v1/chat`, coadă FIFO, ≤ 32 neterminale, aprobări per apel modificator în Studio) și `terminal` (Mod T, `POST /v1/terminal/sessions`, `running` cât rulează CLI-ul, `completed` la închidere, `cancelled` din Studio; aprobare în Studio doar pentru `GENERATION_TOOLS`).
- Stări: `queued`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`; hub-ul adaugă `lost` pentru sesiunile dispozitivelor offline.
- Evenimente `{seq, type, text?, ...}`: `text`, `status` (`tool?`, `approval_id?`, `approval_active?`), `tool` (`tool`), `approval` (`tool`, `approval_id`, `arguments`), `error`, `done` (`state`), `prompt` (Mod T: textul developerului), `claim` (`action: "claimed"|"released"|"denied"|"expired"`, `paths`, `holder?`). Text ≤ 64 000 de caractere per eveniment; 1024 evenimente reținute per job; cursor `after` strict crescător; un cursor mai vechi decât memoria primește un `status` explicativ.
- `client_request_id` (Mod S): deduplicare ca în 0.3/0.8 (64 joburi detaliate, 4096 chitanțe, 409/410/429).
- Istoric: ≤ 64 joburi; sesiunile `terminal` încheiate sunt vizibile 10 min.
- Providerii: `providers.status()` → doar `available`; CLI-ul rulează cu configurația normală a utilizatorului, izolat (`--strict-mcp-config`, `--setting-sources ""`, `--disable-slash-commands`, `disableAllHooks`); o eroare de tool produce `status` și agentul continuă; `failed` doar la `permission_denied`, rezultat final cu eroare sau închiderea procesului; `native_id` se salvează și când tura eșuează.

### 4.9 Studio-ul țintă

Pentru sesiunile `terminal` (Mod S fixează `studio_id` la creare), la fiecare apel:

1. `default_studio_id` (suprascriere manuală prin `/v1/default-studio`), dacă este conectat;
2. altfel instanța al cărei `name` coincide cu `workspace.name` (`place_name` din identitate), dacă este exact una;
3. altfel singura instanță conectată;
4. altfel 409 `Nicio instanță Studio conectată; activează MCP-ul în Studio.` sau `Mai multe instanțe Studio deschise; alege Studio-ul țintă în Avansat.`

Agentul nu poate alege instanța prin argumente (`studio_id` este eliminat din scheme și din argumente).

### 4.10 Actualizare

Verificare la 20 s după pornire și apoi la 6 h din `update-channel.json` (`manifest_url`, canalul hub-ului); aplicare doar fără joburi active: pachetul `plugin` peste rădăcina pluginului (backup în `state/backups`), modulele `studio_app` în `Roblox\Plugins\StudioHarness\app\` (Studio le încarcă fără repornire), `.rbxmx` rescris (cu tokenul injectat, §8.1) doar când `loader_version` diferă de `installed-loader.txt`; apoi daemon-ul repornește (1–2 s), iar loader-ul din Studio reconectează singur. Nu se actualizează într-un checkout git.

## 5. Clientul hub (`scripts/team_client.py`)

Clasa `HubClient(threading.Thread)` (`TeamClient = HubClient`, alias). Constructor:

```python
HubClient(bridge, hub_url, device_token, machine, interval=2.0, idle_interval=5.0, request=None, autostart=True, *,
          pending_interval=15.0, revoked_interval=60.0, backoff_start=2.0, backoff_max=30.0,
          disabled_error=None, clock=time.time)
```

`hub_url=None` (sau gol) → starea `disabled` din construcție, fără fir și fără nicio cerere (`--no-hub`, `hub_url` invalid);
`disabled_error` dă textul lui `error` (implicit „Hub dezactivat (daemon pornit cu --no-hub).”). `autostart=False` lasă firul
oprit, pentru teste care apelează `step()` sau `sync_once()` manual. `request(method, path, body=None, timeout=10)` este
injectabil; funcția liberă `http_request(url, device_token, method, path, body=None, timeout=10)` este implementarea reală.
**Tokenul de dispozitiv nu este atribut al clientului** — trăiește doar în închiderea transportului, deci nu apare în
`status()`, în rânduri sau în excepții; `device_id` se derivă local (`local_state.device_id_for`) și este cunoscut înainte de
primul răspuns al hub-ului. Antet `X-Studio-Harness-Device`; `Content-Type: application/json`; timeouts: `register` 10 s,
`sync` 15 s (30 s cu `project`), claims 10 s, `wait` 75 s, `fetch_*` 15 s (30 s pentru proiect).

Transportul **nu urmează redirectări** (`_NoRedirect` peste `HTTPRedirectHandler`, folosit de `_OPENER`): `urllib` ar copia antetele
cererii — deci tokenul dispozitivului — către gazda nouă, inclusiv peste `http://`, așa că un proxy greșit configurat sau un portal
captiv l-ar primi, eventual în clar. Hub-ul real nu redirectează `/hub/*`, deci un 30x ajunge la apelant ca `HubError` cu codul lui.
Un răspuns 2xx care nu este un obiect JSON dă `HubError` 502 „Răspuns invalid de la hub.”

Ciclul:

1. `register()` cu `{roblox, machine, bridge_id, version, workspace}` din `bridge.identity_payload()`. 200 → `approved`; 202 → `pending` (reîncearcă la `pending_interval`); 403 revoked → `revoked` (`revoked_interval`); 404/5xx/rețea → `offline` (backoff `backoff_start` → `backoff_max`; 404 cu mesajul „Hub-ul rulează o versiune mai veche.”). Un **401 la `register`** înseamnă hub incompatibil: `offline`, cu „Hub-ul rulează o versiune mai veche.” când răspunsul nu are `status` (hub 0.8) și cu mesajul hub-ului când îl are. Un 401 în orice altă fază (sync, claims) → `connecting` și reînregistrare imediată.
2. În `approved`: `sync_once()` la `interval` (2 s) cât timp `bridge.plugin_connected()` sau există sesiuni `terminal` deschise, altfel `idle_interval` (5 s); `wake()` forțează un ciclu imediat (identitate/workspace schimbate, jurnal nou, proiect nou, sesiune nouă a unui coleg).
3. Corpul sync-ului: `bridge.hub_payload(pushed)` (sesiuni proprii + evenimente noi după cursoarele `pushed`), `want` (cursoarele sesiunilor remote), `journal` (coada locală), `journal_after`, `touch`, `project_digest`, `project` (o dată, după `want_project`), `workspace` (**obligatoriu** — lipsa cheii dă 400), `roblox` (când s-a schimbat). La schimbarea cheii workspace-ului, `journal_after` se resetează la 0, ca hub-ul să trimită ultimele 50 de intrări ale **noului** workspace, nu doar ce e mai nou decât cursorul vechi. La eroare, jurnalul și touch-urile revin în coadă.
4. Răspuns → oglinda locală: `remote_sessions`, `remote_events` (≤ 1024 per job), `claim_rows` + `held` (per job), `journal` (≤ 500, cursor `journal_seq`), `members`, `workspaces`, `want_project`, `hub_id`, `device_status`. `hub_id` diferit → reînregistrare, cursoare 0. 403 `pending`/`revoked` la sync → starea corespunzătoare.

Ce cere clientul de la `bridge`: `bridge_id`, `version`, `plugin_connected()` și `hub_payload(pushed)` (obligatorii) plus, opțional,
`identity_payload()`, `project_digest()`, `project_payload()`, `job_workspace(job_id)` și `hub_state_changed(previous, current)`
(apelat în afara lock-ului la fiecare schimbare de stare; daemon-ul reconciliază acolo claims-urile locale la revenirea în `approved`).

Interfața folosită de daemon:

- `status()` → exact `{"url", "status", "hub_id", "device_id", "error", "last_sync", "enrollment"}` (câmpul `hub` din §4.3).
- `set_identity(roblox, workspace)` — identitatea și workspace-ul curent; o schimbare cheamă `wake()`.
- `wake()` (metodă, nu `Event`), `stop()`, `step()` (un singur ciclu; întoarce secundele până la următorul — folosit de teste), `run()`.
- `remote_rows()`, `member_rows()` (cu `me`), `workspace_rows()` (formatul hub-ului, `members_online` **listă**, plus `mine`), `journal_rows(limit, workspace=None)`, `record(entry)`, `is_remote(job_id)`, `remote_snapshot(job_id, after)`, `workspace_key()`, `register()`, `sync_once()`.
- `fetch_workspaces()`, `fetch_workspace(key)`, `fetch_project(key)` (memorat după `digest`; servit și când hub-ul tocmai a căzut, cât timp digest-ul nu s-a schimbat) — apeluri sincrone care întorc răspunsul **complet** al hub-ului (`{"ok": true, ...}`) și ridică `HubError` cu codul HTTP; când starea nu este `approved`, 503 cu textul `unavailable(state)` = „Hub-ul nu este disponibil (stare: <status>).”, fără să atingă rețeaua.
- Claims (aceeași interfață ca `ClaimTable`, plus keyword-ul `workspace` = cheia jobului): `claim(job_id, developer, paths, reason="", *, workspace=None)`, `release(job_id, paths=None, *, workspace=None)`, `touch(job_id, *, workspace=None)`, `held_by`, `covers`, `related_to`, `holder(path, exclude_job=None, *, workspace=None)`, `wait_free(path, timeout, cancelled=None, *, workspace=None)`, `snapshot()`, `expire()`; deciziile se iau în hub, citirile din oglindă.
- Excepția `HubError(message=HUB_DOWN, status=503, payload=None)` (`TeamError = HubError`, alias), cu proprietatea `unreachable` (502/503/504 fără JSON = hub neatins); mesajul implicit când hub-ul nu răspunde: `Hub-ul nu răspunde; claims-urile sunt locale până revine.` Funcția `unavailable(state)` dă textul folosit și de daemon (§4.7) și de toolurile `hub_*` (§6.3).

## 6. Joburi, claims și jurnal pe workspace

### 6.1 Workspace-ul unui job

Fiecare job primește `workspace` = cheia curentă a daemon-ului la creare. Dacă nu există identitate încă (`null`), jobul primește cheia la primul `POST /v1/identity` (toate joburile fără workspace o iau atunci). Claims-urile, jurnalul și sync-ul folosesc cheia jobului. `hub_board`/`hub_project` arată workspace-ul jobului.

### 6.2 Normalizare și conflicte (neschimbate, `claims.py`)

- Se elimină prefixul `game.`; `/` devine `.`; `workspace` (orice majuscule) devine `Workspace`; ≤ 64 de segmente, ≤ 512 caractere, fără caractere de control. Speciale: `@play` (extins de daemon la `@play:<studio_id>`) și `@all`.
- Conflict: una dintre căi este prefix al celeilalte (strămoș, egală sau descendent); `@all` cu orice; specialele doar cu ele însele. Per workspace.
- Expirare la 600 s fără `touch` (orice apel al deținătorului reînnoiește); eliberare la încheiere/anulare/închidere, prin `release` sau la offline (60 s) / revocare.

### 6.3 Impunere în `/agent/JOB/call`

`READ_TOOLS` nu cer claim. Pentru celelalte, înainte de aprobare:

| Tool | Ce trebuie în claims |
|---|---|
| `multi_edit` | `file_path` |
| `execute_luau` | parametru obligatoriu `scope` (text sau listă), strict în claims; literalele `game.A.B`, `workspace.A`, `game:GetService("X").Y` din `code` trebuie să fie în claims sau strămoși ai unui claim (refuz cu literalul) |
| `start_stop_play`, `user_keyboard_input`, `user_mouse_input`, `character_navigation` | `@play` |
| `insert_asset`, `generate_*`, `store_image`, `upload_image`, `segment_mesh`, orice alt tool modificator | primul dintre `parent_path`, `path`, `target_path`, `file_path`; altfel `@all` |

Refuzul este un rezultat `isError` care numește calea și deținătorul („Fără claim pe X. Este ținută de ana (Workspace.Map). Așteaptă cu hub_wait sau alege altă zonă.”) și un eveniment `claim` `denied`; nu încheie jobul. Înainte de impunere daemon-ul face `touch` (în hub sau local).

Detecția literalelor din `code` (`claims.luau_literals`) este **best-effort, nu o barieră de securitate**: prinde formele `game.A.B`, `workspace.A` și `game:GetService("X").Y`, dar nu indexarea cu paranteze (`game.Workspace["Map"]`), `FindFirstChild("Workspace")`, o variabilă intermediară sau un `GetService` cu argument calculat. Scopul ei este să prevină greșelile, nu ocolirile intenționate: claims-urile sunt oricum self-service (orice job poate cere `@all`), deci coordonează munca, nu delimitează încrederea.

Când `hub.status != "approved"`: claims-urile sunt **locale** (o `ClaimTable` a daemon-ului, valabilă doar pe acest PC), modificările nu sunt blocate, iar toolurile `hub_*` spun explicit „Hub-ul nu este disponibil (stare: offline): claims-urile sunt locale, colegii nu le văd.” Reconciliere: când hub-ul devine `approved`, claims-urile locale ale joburilor active sunt retrimise o dată la hub (`claim` cu aceleași căi); cele refuzate produc `claim` `denied` cu deținătorul și sunt abandonate; tabela locală se golește. Nu există cădere înapoi în sens invers pentru claims-urile deja acordate de hub (rămân în oglindă până expiră).

### 6.4 Jurnal

Fiecare apel modificator executat fără eroare adaugă `{time, job_id, provider, tool, paths, summary (≤ 300), workspace}`. Cu hub aprobat, intrarea merge la hub (`sync.journal`) și revine numerotată; fără hub, se reține local (≤ 500, `seq` local). Citirile apar doar în feed-ul sesiunii.

## 7. Proxy MCP și toolurile `hub_*`

### 7.1 Proxy

`bridge_mcp.py` (Mod S, config din `STUDIO_HARNESS_URL`, `STUDIO_HARNESS_JOB_ID`, `STUDIO_HARNESS_JOB_TOKEN`) și `harness_mcp.py` (Mod T, shim stdio care pornește/atașează daemon-ul și creează sesiunea din terminal) rutează `initialize`, `ping`, `tools/list` → `GET /agent/JOB/tools`, `tools/call` → `POST /agent/JOB/call`. Un job `queued`/terminal nu poate folosi proxy-ul (409). `subagent` și `skill` native nu sunt expuse. Schemele primesc `datamodel_type` (Edit/Client/Server), pierd `studio_id`, `execute_luau` primește `scope` obligatoriu; `annotations.readOnlyHint` pentru `READ_TOOLS`. `harness_mcp.compatible_version(version)` acceptă explicit daemon ≥ 1.0 și 0.x ≥ 0.4: compară doar `major.minor`, ignoră un sufix (`1.0.0-rc1`) și refuză orice text care nu începe cu `<major>.<minor>`. `bridge_mcp.py` raportează `serverInfo = {"name": "StudioHarness", "version": "1.0.0"}`. Un daemon cu alt token local pe portul 34871 → „Pe portul 34871 rulează un daemon cu alt token local (un daemon pornit cu alt --state-dir sau un bridge vechi 0.3). Închide-l și reîncearcă.”

### 7.2 Tooluri de coordonare (servite de daemon, în `tools/list`)

| Tool | Argumente | Rezultat (text JSON) |
|---|---|---|
| `hub_board` | `{}` | `{"developer", "hub": {"status", "url", "notice": null \| "…"}, "workspace": meta \| null, "members": [prezenți], "sessions": [proprii + ale altora din workspace], "claims": [ale workspace-ului], "journal": [ultimele 20], "project": {"place_name", "count", "groups": {cheie: număr}} \| null}` |
| `hub_claim` | `{"paths": [1–32], "reason"?}` | `{"ok": true, "claimed": [căi], "scope": "hub" \| "local", "notice"?}`; la conflict `isError` cu `{"ok": false, "conflicts": [{"path", "holder", "developer", "held_path", "since"}], "hint": "Așteaptă cu hub_wait sau alege altă zonă."}` |
| `hub_release` | `{"paths"?}` | `{"ok": true, "released": [căi]}` |
| `hub_wait` | `{"path", "timeout_seconds"?: ≤ 300 (implicit 60)}` | text: liberă / încă ținută (`isError` la timeout); nu revendică |
| `hub_project` | `{"group"?: cheie}` | fără argument: `{"snapshot_id", "place_name", "place_id", "workspace", "count", "truncated", "groups": {cheie: {"label", "count", "by_class", "sample": [≤ 50 căi]}}}`; cu `group`: `{"place_name", "count", "truncated", "workspace", "group": {"key", "label", "count", "by_class", "entries": [≤ 500 {"id", "path", "className", "name"}]}}` |

Proiectul văzut de agent: cel local dacă `project.workspace == job.workspace`, altfel proiectul workspace-ului din hub (`fetch_project(key)`, servit din memorie și când hub-ul tocmai a căzut, cât timp digest-ul nu s-a schimbat), altfel `isError` „Nu există încă o hartă a proiectului: pluginul Studio o trimite la conectare și la fiecare schimbare.”

`hub.notice` (în `hub_board`) este `null` când hub-ul este `approved`; altfel textul `HUB_NOTICE`: „Hub-ul nu este disponibil (stare: <status>): claims-urile sunt locale, colegii nu le văd.” `hub_claim` întoarce `notice` **doar** când `scope` este `local` — același text, sau, cu hub aprobat dar fără workspace pe job, „Jobul nu are încă un workspace (pluginul Studio nu a trimis identitatea): claim-ul este local.”

`hub_board.workspace` este meta workspace-ului **jobului**: cea curentă a daemon-ului când cheile coincid, altfel rândul din sumarul hub-ului, altfel `{key, name: key}`; `null` doar când jobul nu are încă workspace. `hub_project` întoarce `workspace` și în sumar, și în răspunsul cu `group`.

Promptul de sistem Mod S și `skills/studio/SKILL.md` cer: citește `hub_board` și `hub_project`, revendică subarborele grupei pe care o modifici (nu servicii întregi), `execute_luau` cu `scope`, eliberează la final.

## 8. Pluginul Studio 1.0

### 8.1 Loader (`studio-plugin/StudioHarness.server.luau`, `-- Studio Harness Loader 1.1.0`)

- `build_studio_plugin.py` adaugă în `.rbxmx` un `StringValue` numit `LocalToken`, copil al scriptului loader, cu `Value = "STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER"`. `dist/StudioHarness.rbxmx` din repo și din pachete conține doar placeholder-ul.
- La instalare (`install-studio-plugin.ps1`, `updater.install_studio_plugin(source, backup_root, environ=None, local_token=None)`), placeholder-ul este înlocuit cu conținutul lui `local-token` (creat dacă lipsește) când fișierul este scris în `%LOCALAPPDATA%\Roblox\Plugins\StudioHarness.rbxmx`. Tokenul este `[A-Za-z0-9_-]`, deci nu cere escapare XML; compararea „fișier identic” se face după substituire.
- Loader-ul citește `script:FindFirstChild("LocalToken")`; valoare validă (8–512 caractere, fără spații, caractere de control sau `\`, diferită de placeholder) → o salvează în `plugin:SetSetting("StudioHarnessBridgeToken")` și o folosește (are prioritate față de setarea salvată anterior); altfel rămâne câmpul manual din „Avansat” (fallback pentru loader-ele 1.0.0 sau instalări manuale). Codul folosit la pornire este cel din setare; dacă `SetSetting` eșuează, rămâne cel livrat, direct din memorie.
- Aplicația încorporată pornește cu handoff-ul `{ controller = { token = <cod>, source = "loader" | "settings" } }` (fără `store`: la prima pornire nu există istoric). La metamorfoză, handoff-ul este `app:snapshot()` = `{ store = Store:serialize(), controller = Controller:snapshot(), hubVisible = … }`; `Store.deserialize(nil)` este tolerantă, deci ambele forme funcționează. Când bundle-ul nou nu pornește și loader-ul revine la cel anterior, handoff-ul primește în plus cheia `bundleError = "<revizie>: <eroare>"` (`Loader.retryHandoff`, funcție pură testată); `Main.start` o preia în `store.bundleError`, tăiată la 200 de caractere, iar HubView o arată în bannerul `danger` „Interfața nouă nu a pornit: …” din `design/DESIGN.md` §12.2.
- Restul rămâne: pornește aplicația din `Modules` (încorporat), apoi cu tokenul cere `GET /v1/status` la 5 s pentru `plugin_bundle.revision`; când diferă: `GET /v1/plugin/bundle` → `app:snapshot()` → `app:destroy()` → folder `App-<revision>` cu `ModuleScript`-uri → `require(Main).start(plugin, handoff, toolbar, button)`; la eșec revine la bundle-ul anterior (păstrat în memorie), cu avertisment. `Loader.shouldSwap`, `validateBundle`, `sameSources`, `buildFolder` rămân funcții pure testate în `tests/Loader.spec.luau`. Loader-ul și `BridgeController.luau` sunt singurele module care folosesc `HttpService`.

### 8.2 Aplicația (`studio-plugin/modules/`, `Main.luau` = `-- Studio Harness App 1.0.0`)

`Main.start(plugin, handoff?, toolbar?, button?) -> app`, `app:snapshot() -> handoff`, `app:destroy()`. Handoff = `Store:serialize()` (`HANDOFF_VERSION` rămâne 1; câmpurile noi sunt opționale, snapshot-urile 0.8 se deserializează).

`SessionStore.luau` (pur, fără servicii Roblox), câmpuri 1.0:

```lua
store.identity   = { userId = 12345, name = "ellob", avatar = "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48" } -- sau nil
store.workspace  = { key = "game:987654", name = "Ball", placeId = 1291603, gameId = 987654, creatorId = 555, creatorType = "User" } -- sau nil
store.hub        = { status = "approved", url = "https://…", hubId = "…", deviceId = "…", error = nil, panelUrl = "https://…/panel", enrollment = "approve" }
store.members    = { { deviceId = "…", userId = 1, name = "ana", machine = "PC-ANA", lastSeen = 0, me = false }, … }
store.workspaces = { { key = "…", name = "…", membersOnline = 2, sessionsActive = 1, claims = 0, mine = true }, … }
store.viewWorkspace = nil -- cheia workspace-ului privit (nil = cel curent)
-- ferestrele (store.windows) primesc owner = { userId, name, deviceId, machine } și workspace = key; store.team dispare
store.REQUIRED_FEATURES = { "queued_sessions", "batch_events", "terminal_sessions", "claims", "board", "identity", "workspaces", "panel" }
```

Pe lângă câmpurile de mai sus, store-ul 1.0 mai ține `store.view` (răspunsul workspace-ului privit), `store.machine`,
`store.pluginConnected`, `store.daemonProject` (sumarul `project` din status), `store.claims`, `store.journal` (cea mai nouă
intrare prima, maximum 10) și, pe ferestre, `window.owner = {userId, name, deviceId, machine}`, `window.mine`,
`window.workspace`, `window.started` (momentul creării proprii, respectiv `session.started` pentru sesiunile din terminal și
cele remote), `window.approvals[*].inspectable` (pornește `false`, nu se moștenește prin handoff).

API-ul 1.0 folosit de view-uri: `applyStatus(status)` (mapează `/v1/status`: `identity`, `workspace`, `hub`, `members`,
`workspaces`, `update`, `project`, `providers`, `plugin_connected`; `identity = null` **lasă** `identity` nil, ca numele
local citit din Studio să rămână), `syncBoard(board)` (mapează `/v1/board`: sesiuni cu `mine`/`remote`, claims cu `mine`,
jurnal), `resetHub()` (la deconectarea daemon-ului), `applyMembers`, `applyWorkspaces(rows)`, `applyPresence`,
`setViewWorkspace(key)` / `applyWorkspaceView(key, răspunsul /v1/hub/workspace)` / `viewedWorkspace()` / `sessionsInView()`,
`membersInView()`, `sessionsMine()`, `sessionsOthers()`, `workspaceLabel()`, `workspaceKey()`, `ownIdentity()`,
`ownsClaim(claim)`, `hubStatusText()` → `(text, rol)`, `setApprovalInspectable(id, approvalId, ok)`, plus helperii puri
`Store.identityFrom`, `Store.workspaceFrom`, `Store.hubFrom`, `Store.memberFrom`, `Store.workspaceRowFrom`,
`Store.ownerFrom`, `Store.sessionRowFrom`, `Store.journalFrom`, `Store.hubText`, `Store.avatarUrl`, `Store.workspaceName`.
Handoff-ul (`serialize`/`deserialize`) poartă `identity`, `workspace`, `hub`, `machine`, `viewWorkspace` și `window.started`.
Sesiunile cu `mine == false` sunt doar de citit (fără Oprește, Eliberează, aprobări).

### 8.3 `BridgeController.luau`

- Conectare automată la încărcare cu tokenul din handoff sau din setări (livrat de loader); fără buton „Conectează” în fluxul normal; reconectare cu backoff 1 → 2 → 4 → 8 → 15 s (un 401 sare direct la 15 s și face `needsToken()` adevărat); câmpul de cod apare doar când `needsToken()` este adevărat.
- API 1.0: `Controller.new(plugin, store, snapshot?)`, `connect(token?, source?)`, `disconnect(message?, code?)` (**fără** mesaj = oprire manuală, fără reconectare), `stop()`, `snapshot()` = `{token, source, manualStop, identity}` (tokenul nu ajunge niciodată în store sau în `Store:serialize()`), `savedToken()`, `needsToken()`, `tokenSource()` ∈ `loader | settings | manual | nil`, `hubStatus()`, `openPanel()`, `viewWorkspace(key)` (`nil` sau cheia curentă = înapoi), `refreshWorkspaces()`, `viewError()` (text sau `nil`), plus acțiunile de sesiune (`createWindow`, `send`, `retry`, `decide`, `cancel`, `close`, `releaseClaims`, `setDefaultStudio`).
- `store.identity` este completat local din `StudioService`/`Players` până când daemon-ul îl confirmă prin status sau tablă.
- Identitatea (§1.5, §4.4): `POST /v1/identity` la conectare, la 30 s și la schimbarea `game.PlaceId`/`game.GameId`/`game.Name` (`GetPropertyChangedSignal`).
- Polling: `POST /v1/jobs/poll` la 1 s (loturi de 16, cursor per fereastră), `GET /v1/board` la 2 s, `GET /v1/status` la 10 s (și imediat la conectare, și ori de câte ori există joburi active), `POST /v1/identity` la 30 s, vizualizarea altui workspace la 5 s, un chunk de inventar per iterație. `bridge_id` schimbat → invalidează referințele remote, păstrează textele locale.
- „Panou” → `POST /v1/panel/open`. Acțiuni: `chat`, `approve`, `cancel`, `release`, `default-studio` ca în 0.8. `GET /v1/hub/workspaces` / `GET /v1/hub/workspace?key=` pentru „Alte workspace-uri” (read-only).
- 401/403 → deconectare cu mesaj; 404 pe `/v1/board` sau `/v1/jobs/poll` → „daemon vechi”; 3 eșecuri consecutive → deconectare cu reîncercare. O eroare 4xx (alta decât 401/403) la `POST /v1/identity` **nu** deconectează: identitatea se retrimite la următorul interval.

### 8.4 Interfața

Conform `design/DESIGN.md`: dock widget „Studio Harness” (lățime minimă 320, scroll vertical) cu antet (avatar 28 px, nume Roblox, punct de stare hub verde/chihlimbar/roșu/gri, `v1.0.0`, buton „Panou”), banner condiționat („Așteaptă aprobarea adminului · dispozitiv ab12cd34”, „Hub offline: …”, „Actualizare aplicată”), workspace (nume, `place <id> · creator`, avatare 24 px ale prezenților, „Alte workspace-uri ▾”), acțiunea principală (`Claude Code | Codex` + „Sesiune nouă”, disponibilitatea CLI-urilor), sesiuni (carduri grupate „Ale tale” / „În workspace”), aprobare, claims (cu „Eliberează” pe ale tale), jurnal (ultimele 10), subsol („Daemon · 127.0.0.1:34871 · la zi”, „Avansat”: cod de asociere doar fără token livrat, Studio țintă, „Deconectează daemon-ul”). `Theme.luau` urmează tema Studio (`settings().Studio.Theme`, `ThemeChanged`; `Main` apelează `Theme.install(app, …)` o singură dată, înainte de a construi view-urile), `RichText = false` peste tot, textele nu se trunchiază brutal. `canSend` (Mod S): conectat, compatibil, provider `available`, scenă fixată, fără job activ în fereastră.

**Etichetele și textele de stare** (sesiune, hub, dispozitiv, bannere, stări goale) sunt normate în `design/DESIGN.md` §6 și §12 — acolo se schimbă, nu aici. Contractul fixează doar **cheile** de stare: `pending | approved | revoked` pentru dispozitiv, `connecting | pending | approved | offline | revoked | disabled` pentru legătura daemon → hub, `queued | running | waiting_approval | completed | failed | cancelled | lost` pentru sesiuni. Aceeași regulă pentru panou (§9): clasele CSS și textele sunt în `design/DESIGN.md` §9 și §12.

### 8.5 Inventarul (`ProjectScanner.luau`, `POST /v1/project/chunks`)

Scanare la conectare, la 30 s dacă s-a schimbat (număr de instanțe + sumă a numelor) și la 5 s după `DescendantAdded`/`DescendantRemoving`; rădăcini: Workspace, ReplicatedStorage, ReplicatedFirst, ServerScriptService, ServerStorage, StarterGui, StarterPack, StarterPlayer, Lighting, SoundService, MaterialService, Teams, TextChatService, Chat; plafon 20 000 (`truncated`). Format `nodes: [[id, parent, className, name, flags]]` (flags: 1 Source, 2 atribute, 4 RunContext Client, 8 RunContext Server). Chunk-uri ≤ 700 KB:

```json
{"snapshot_id": "…", "index": 0, "total": 3, "place_id": 1291603, "game_id": 987654, "place_name": "Ball",
 "creator_id": 555, "creator_type": "User", "studio_id": "…", "truncated": false, "nodes": []}
```

`ProjectScanner` expune și helperii puri `Scanner.place()` (locul curent ca tabel), `Scanner.identityPayload(place, userId, name)` și `Scanner.chunkPayload(project, place, studioId)`, folosiți de `BridgeController` în loc de citiri inline din `game`. Rezerva antetului unui chunk este 2048 de octeți (`CHUNK_BYTES = 700·1024 − 2048`). Daemon-ul asamblează (timeout 60 s, `total` constant, ≤ 64 chunk-uri), clasifică prin `project_map.classify` și reține `project = {snapshot_id, place_id, game_id, place_name, creator_id, creator_type, workspace: workspace_key(game_id, place_id), studio_id, developer, machine, taken, count, truncated, groups, nodes}`; răspuns `{"ok": true, "complete": bool, "received", "total", "count"?, "groups"?}`. Un proiect nou trezește sync-ul (`project_digest`).

## 9. Panoul web (`panel/index.html`, un singur fișier, fără CDN)

### 9.1 Login și sesiune

- URL: `<hub_url>/panel`. `BASE_PATH = location.pathname` fără sufixul `/panel`; toate cererile sunt `BASE_PATH + "/hub/…"`, `credentials: "omit"`, `cache: "no-store"`.
- Fragmentul `#device=<64 hex>` (deschis de daemon): panoul îl citește la încărcare, îl salvează în `localStorage["studioHarness.device"]`, îl șterge din URL (`history.replaceState`) și continuă.
- Ecranul de login: „Deschide panoul din pluginul Studio Harness (butonul „Panou”). Se autentifică singur.” + câmpul „sau lipește codul” (placeholder „cod de dispozitiv sau admin”, minimul 16 caractere). Un cod de **64 hex** se verifică întâi ca dispozitiv (`X-Studio-Harness-Device`) și, la refuz, ca admin; **orice alt cod** se verifică direct ca admin (`X-Studio-Harness-Admin`), pentru că hub-ul refuză oricum cu 401 un antet de dispozitiv care nu are 64 hex. Verificarea folosește `GET /hub/status`; succesul salvează tipul (`localStorage["studioHarness.admin"]` pentru codul de admin). Un 403 `pending`/`revoked` la verificarea ca dispozitiv este tot un succes: panoul salvează codul de dispozitiv și trece direct la ecranul de așteptare. Eroare clară la refuz, în `.field__error`: „Codul nu a fost acceptat. Verifică-l sau deschide panoul din plugin (butonul Panou).” (textele exacte ale ecranului sunt în `design/DESIGN.md` §12.4.2 și §12.5).
- Antetele se trimit pe toate cererile `/hub/*` (ambele, dacă există ambele coduri). 401 pe orice cerere → înapoi la login (codul se șterge); 403 `pending` → ecranul „în așteptare”; 403 `revoked` → ecran „Dispozitivul a fost revocat” cu buton „Folosește alt cod”.
- Pending: „Dispozitivul tău așteaptă aprobarea” cu `device_id`, numele Roblox, mașina (din `/hub/status`); reîncearcă la 5 s.

### 9.2 Shell și date

- Bară de sus: „Studio Harness”, comutator temă (`localStorage["studioHarness.theme"]`, implicit `prefers-color-scheme`), eu (avatar + nume + „admin”), badge „N în așteptare” (admin). Bară laterală: workspace-urile (nume, avatarele prezenților, sesiuni/claims); pe ecran îngust un `select`. Workspace-ul selectat se reține în `localStorage["studioHarness.workspace"]`.
- File: **Activitate** (prezenți, sesiuni live cu fluxul de evenimente prin `GET /hub/session?job=&after=` la click, claims, jurnal), **Proiect** (`GET /hub/project?key=`, harta pe grupe, căutare, detalii pe grupă), **Dispozitive** (admin: pending cu „Aprobă”/„Revocă”, lista tuturor, „Uită”, modul de înrolare; non-admin: doar dispozitivul propriu).
- Polling: `GET /hub/panel-data?workspace=<key>` la 2 s; proiectul la 30 s sau la schimbarea `digest`-ului; sesiunea deschisă la 2 s (`GET /hub/session?job=&after=`). Răspunsul `panel-data` se compară cu cel anterior ca JSON cu `hub.now` neutralizat: identic → pasul de randare se sare complet (timpii scurși se recalculează local, la secundă). Se randează doar fila vizibilă; celelalte la activare.
- `me.workspace` din `panel-data` este cheia ultimului workspace raportat de dispozitiv (§3.4). Hub-ul o trimite ca panoul să poată marca workspace-ul propriu și după ce prezența a expirat (`ONLINE_SECONDS`); panoul 1.0.0 **nu o consumă încă** — marchează prezența proprie din `selected.members` — și degradează curat, pentru că un câmp neconsumat nu schimbă nimic. Eticheta „· al tău” de sub numele jocului înseamnă, ca în plugin (`Theme.placeLine`), că **locul îți aparține** (`creator_id` = contul tău); prezența proprie se arată separat, cu chip-ul „ești aici”.
- Avatare: `<img src="BASE_PATH/hub/avatar?user=<id>&size=48">` cu fallback la inițiale desenate (SVG `data:` URI) la eroare/204.
- `?demo=1`: date de exemplu locale, fără nicio cerere.
- Toate textele din date se pun cu `textContent`; fără `innerHTML` cu date. Responsive ≤ 400 px, dark/light, fără scroll orizontal, contrast ≥ 4.5:1, focus vizibil, `aria-*` pe file și butoane. CSP neschimbat (§3.1).

## 10. Actualizare, publicare, deploy

### 10.1 Manifest și canal

`releases/manifest.json`:

```json
{"version": "1.0.0", "published": "2026-09-14T12:00:00", "notes": "Studio Harness 1.0.0", "loader_version": "1.1.0", "bundle_revision": "<16 hex>",
 "files": {"plugin": {"url": "…/roblox-studio-harness-1.0.0.zip", "sha256": "<64 hex>", "size": 1},
           "hub": {"url": "…/studio-harness-hub-1.0.0-ubuntu.zip", "sha256": "<64 hex>", "size": 1},
           "studio_app": {"url": "…/studio-harness-app-1.0.0.zip", "sha256": "<64 hex>", "size": 1}}}
```

`update-channel.json` (neschimbat): `manifest_url` = `https://lostcube.pro/roblox/harness/releases/manifest.json` (canalul public al hub-ului), `upstream_manifest_url` = raw GitHub (`https://raw.githubusercontent.com/OWNER/REPO/BRANCH/releases/manifest.json`), `auto`, `github_repo`. Ordinea: hub-ul se actualizează primul din upstream și oglindește în `/releases/`; daemon-ii se actualizează de la hub. URL-urile acceptate: `https://`, `http://127.0.0.1:`, `http://localhost:`.

### 10.2 Publicare (`scripts/publish_release.py`)

`Publish.cmd` = `publish_release.py --github --bump patch` (repo din `update-channel.json["github_repo"]`): bump în `plugin.json`, `studio_bridge.py`, `team_hub.py`, antetul `App` din `Main.luau` și `server_version` (loader-ul se schimbă manual), `build_studio_plugin.py` → `dist/StudioHarness.rbxmx` cu `LocalToken` placeholder, pachetele `plugin`/`hub`/`studio_app` + `.sha256` + manifest, `scan_secrets` (oprește publicarea), apoi `git add -A`, `git commit -m "Studio Harness <versiune>"`, `git push origin <branch>` cu credențialele git ale utilizatorului. `--dry-run` = doar build + scan. Pachetele nu conțin `Setup-Team.cmd`, `setup-team.ps1`, `Start-Team-Hub.cmd`, `team.json`, `local-token`, `device-token`, `hub-admin-token`, `team-token`, `daemon.log`, `.env*`; `scan_secrets` refuză aceste nume și modelele de chei (Anthropic, HF, GitHub, AWS, `"…_token": "…"`). Pachetul `plugin` include `Start-Hub.cmd`, `Start-Daemon.cmd`, `Install-Studio-Plugin.cmd`, `Install-Codex-Config.cmd`, `Publish.cmd`; `EXCLUDE_FILES` scoate `scripts/setup-team.ps1`, `Setup-Team.cmd` și `Start-Team-Hub.cmd` chiar dacă au rămas într-un checkout vechi. Pachetul `hub` (`HUB_FILES`) conține modulele pe care `team_hub.py` le importă (`claims.py`, `local_state.py`, `project_map.py`, `updater.py`), `update-channel.json`, `panel/index.html` și fișierele din `deploy/ubuntu/`.

`scan_secrets` privește **tot ce ajunge în commit** (`git add -A`), nu doar ce intră în pachete: `tests/` și `design/` sunt excluse din arhive (`EXCLUDE_DIRS`), dar sunt publicate pe GitHub, deci un token lipit într-un fixture sau într-un document de design oprește publicarea. Un `.rbxmx` al cărui `LocalToken` are altceva decât placeholder-ul (pachetul unui plugin deja instalat, cu tokenul UI injectat) este tratat la fel: publicarea se oprește. Când sursa loader-ului lipsește, `dist/StudioHarness.rbxmx` nu se reconstruiește, dar nici nu se publică nevăzut: fișierul existent este verificat pentru token injectat și publicarea se oprește dacă verificarea nu poate fi făcută.

`verify_versions(version)` oprește publicarea (inclusiv `--dry-run`) când versiunile nu sunt aliniate: `studio_bridge.VERSION`, `team_hub.VERSION` și antetul `-- Studio Harness App <versiune>` din `Main.luau` trebuie să fie exact versiunea publicată, iar loader-ul (`-- Studio Harness Loader <versiune>`) cel puțin `MIN_LOADER_VERSION = 1.1.0` — primul loader care citește `LocalToken`.

### 10.3 Instalare locală

`Install-Studio-Plugin.cmd` → `install-studio-plugin.ps1`: citește/creează `local-token` în directorul de stare (`STUDIO_HARNESS_STATE_DIR`, altfel `%LOCALAPPDATA%\StudioHarness`; tot acolo ajunge și `plugin-backups\`), copiază `dist/StudioHarness.rbxmx` în `Roblox\Plugins\` înlocuind placeholder-ul, copiază modulele în `Roblox\Plugins\StudioHarness\app\`, verifică hash-urile și afișează un rezumat JSON cu `LocalTokenFile`, `LocalTokenCreated`, `LocalTokenInjected` (niciodată valoarea tokenului) și `StudioRestartRequired: true` pentru loader. `build_studio_plugin.verify` raportează `local_token: "placeholder"` pentru fișierul construit și `"injected"` pentru unul instalat. `Start-Daemon.cmd` rămâne opțional (doar Studio, fără terminal). `Install-Codex-Config.cmd` adaugă `mcp_servers.studio_hub` și `notify` în `%USERPROFILE%\.codex\config.toml`. Pluginul Claude Code declară doar `studio_hub` (`.mcp.json`) și hook-urile `SessionStart`/`UserPromptSubmit`/`Stop`/`SessionEnd` → `harness_hook.py`.

### 10.4 Deploy Ubuntu și Docker

- `deploy/ubuntu/install.sh`: utilizatorul de sistem `studio-harness`, `/var/lib/studio-harness/app` (`team_hub.py`, `claims.py`, `local_state.py`, `project_map.py`, `updater.py`, `update-channel.json`, `panel/index.html`, `releases/`), starea în `/var/lib/studio-harness` (0700), serviciul `studio-harness-hub.service` (`--listen 127.0.0.1 --port 34880 --state-dir /var/lib/studio-harness`, izolare systemd). La final afișează calea `hub-admin-token` (`sudo cat …/hub-admin-token`) și explică: developerii apar în panou ca `pending`; adminul îi aprobă din fila Dispozitive sau cu `sudo -u studio-harness python3 /var/lib/studio-harness/app/team_hub.py --state-dir /var/lib/studio-harness --port 34880 --approve-pending`.
- Caddy (`handle_path /roblox/harness/*`, `redir /roblox/harness /roblox/harness/panel 302`, `response_header_timeout 90s`) și nginx (`location /roblox/harness/ { proxy_pass http://127.0.0.1:34880/; proxy_read_timeout 90s; }`) neschimbate. Adresele publice: `…/roblox/harness/panel`, `…/roblox/harness/healthz`, `…/roblox/harness/releases/manifest.json`, `…/roblox/harness/hub/...`.
- `deploy/ubuntu/Dockerfile` (volum `/data`) și `Dockerfile` din rădăcină (Hugging Face Space, port 7860, `STUDIO_HARNESS_STATE_DIR=/home/hub/state`): codul de admin vine din env `STUDIO_HARNESS_ADMIN_TOKEN` (Secret al Space-ului) sau din `hub-admin-token` generat în volum.
- `Start-Hub.cmd` (înlocuiește `Start-Team-Hub.cmd`): pornește `team_hub.py` local, pe portul 34880, și primește argumentele hub-ului (`--show-admin-code`, `--approve-pending`, `--open-enrollment`). Un daemon **de pe același PC** îl folosește cu `{"hub_url": "http://127.0.0.1:34880"}` în `config.json` sau cu `STUDIO_HARNESS_HUB_URL`; din LAN sau din internet hub-ul se folosește **exclusiv prin HTTPS**, cu un reverse proxy în față (§1.1: `http://` este acceptat doar pentru `127.0.0.1`/`localhost`, deci `http://<ip-din-LAN>:34880` ar lăsa daemon-ul în `disabled`).

## 11. Convenții și coduri

- Fișiere LF, UTF-8 (`.ps1` cu BOM). Python 3.10+, fără dependențe. Luau compatibil Roblox Studio, fără `require` de fișiere; `HttpService` doar în loader și `BridgeController.luau`; `SessionStore.luau` și `ProjectScanner.luau` testabile cu `luau.exe`.
- Testele Python nu scriu în `%LOCALAPPDATA%` real (`STUDIO_HARNESS_STATE_DIR` temporar), nu ating rețeaua (fetcher-e și `request` injectabile, servere pe loopback cu port 0), nu folosesc porturile 34871/34880.
- Mesajele de eroare au forma `{"ok": false, "error": "<română>"}`; niciun token, URL cu token sau antet în mesaje și log-uri.
- Când se schimbă o rută sau un câmp, se actualizează acest contract, testele și documentele care îl menționează în aceeași fază.

Coduri HTTP folosite de hub și daemon:

| Cod | Hub | Daemon |
|---|---|---|
| 200 | ok | ok |
| 202 | `register` în așteptare | — |
| 204 | avatar indisponibil | — |
| 302 | `/` → panou | — |
| 400 | corp/câmp invalid, fără workspace curent, rută care cere dispozitiv | corp/câmp invalid, `scope` lipsă, tool necunoscut |
| 401 | dispozitiv necunoscut, cod admin invalid | token UI sau token de job invalid |
| 403 | origin străin, `pending`, `revoked`, admin necesar | Host/Origin nepermis, job al altui developer |
| 404 | rută inexistentă (`/team/*`), dispozitiv/workspace/sesiune inexistente, panou lipsă | rută inexistentă, job inexistent, `provider/*`, module lipsă |
| 409 | conflict de claim | aprobare inactivă, sesiune/conversație ocupată, Studio neconectat, `client_request_id` refolosit, sesiune terminal închisă, hub `disabled` la `panel/open` |
| 410 | — | cerere deduplicată expirată |
| 413 | corp > 4 MB | corp > 1 MB, argumente > 512 KB, inventar > 20 000 |
| 415 | content-type | content-type |
| 429 | — | coadă plină (32), sesiuni terminal (64), chitanțe (4096) |
| 503 | — | daemon în închidere, hub neaprobat la proxy, eroare MCP oficial |
| 500 | eroare internă (fără detalii) | eroare internă (fără detalii) |

## 12. Compatibilitate și migrare de la 0.8

- Hub 1.0 ↔ daemon 0.8: daemon-ul vechi cere `/team/*` → 404 și rămâne „solo”; mesajul lui spune că hub-ul nu răspunde. Daemon 1.0 ↔ hub 0.8: 404 pe `/hub/*` **sau** 401 fără `status` în corp → `offline`, `error = "Hub-ul rulează o versiune mai veche."`, claims locale.
- Starea `hub-state.json` 0.8 (`format: 1`) se ignoră (doar `hub_id` se păstrează). Toate dispozitivele apar ca `pending` la prima conectare 1.0 (sau `approved` cu `--open-enrollment`).
- Loader 1.0.0 (instalat de 0.8) cu aplicația 1.0.0 (hot swap): nu are `LocalToken`, dar setarea `StudioHarnessBridgeToken` salvată în 0.8 rămâne valabilă; utilizatorul repornește Studio o dată după instalarea loader-ului 1.1.0.
- `team.json` este ignorat; `Setup-Team.cmd`, `setup-team.ps1`, `Start-Team-Hub.cmd` sunt eliminate din repo și din pachete.
- `harness_mcp.compatible_version` acceptă ≥ 1.0 și 0.x ≥ 0.4 (doar `major.minor`, sufixul ignorat); shim-ul 0.8 funcționează cu daemon-ul 1.0 (rutele `/v1/terminal/*` sunt neschimbate), iar proxy-ul `bridge_mcp.py` raportează `serverInfo.version = "1.0.0"`.

## Aprobarea operațiilor (1.0)

Daemon-ul ține modul de aprobare în `config.json` (cheia `auto_approve`), deci rezistă peste reporniri; implicit lipsește, adică `ask`.

| Mod | Ce trece fără să întrebe |
| --- | --- |
| `ask` (implicit) | nimic: fiecare operație a agentului cere aprobarea omului |
| `edits` | orice modificare a scenei; generarea (`generate_mesh`, `generate_material`, `generate_procedural_model`, `subagent`) tot se cere, fiindcă poate consuma credite Roblox |
| `all` | tot, inclusiv generarea |

- `GET /v1/status` întoarce `settings: {auto_approve}`, iar `features.settings` este `true`.
- `POST /v1/settings` (token UI) cu `{"auto_approve": "ask" | "edits" | "all"}` → `{ok, settings}`; orice altă valoare este `400`. Revenirea la `ask` șterge cheia din `config.json`.
- O operație trecută automat emite în sesiune un eveniment `status` („Aprobat automat…”), deci rămâne în fluxul ferestrei și în jurnal: nimic nu se execută pe tăcute.
- În plugin, butonul din secțiunea acțiunii ciclează `ask → edits → all` și arată în text ce se întâmplă acum.

## Panourile pluginului și modul Play

Studio descarcă pluginul la fiecare intrare în Play (`plugin.Unloading`) și îl reîncarcă după. Un `DockWidgetPluginGui` distrus nu mai poate fi recreat cu același id cât ține sesiunea Studio, deci aplicația **nu** distruge niciodată panoul hub-ului și nici panourile sesiunilor la oprire: le golește conținutul și le lasă instanței următoare, care la pornire curăță ce a rămas în ele. Panoul unei sesiuni se distruge doar când fereastra dispare din store (utilizatorul a închis-o), pentru că atunci id-ul nu se mai repetă.

