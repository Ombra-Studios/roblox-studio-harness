---
title: Studio Harness Hub
emoji: 🛰️
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# Studio Harness 1.0

**Roblox Studio condus din terminalul tău Claude Code sau Codex, pe contul tău, cu tot ce fac agenții vizibil live în Studio și în panoul web.**

Developerii lucrează pe mai multe jocuri, fiecare de pe PC-ul lui, cu contul lui. Studio Harness leagă patru piese:

- **Pluginul Studio** (`dist/StudioHarness.rbxmx`): loader 1.1.0 + aplicația 1.0.0, care se schimbă fără repornirea Studio-ului. Arată sesiunile (ale tale și ale colegilor din același joc), aprobările, zonele revendicate și jurnalul modificărilor. De acolo pornești și sesiuni scrise direct în Studio.
- **Daemon-ul local** (`scripts/studio_bridge.py`, `127.0.0.1:34871`): pornit automat de serverul MCP la prima sesiune CLI. Este proxy filtrat către MCP-ul oficial al Roblox Studio, impune revendicările (claims), ține jurnalul și servește pluginul.
- **Hub-ul central** (`scripts/team_hub.py`, implicit `https://lostcube.pro/roblox/harness`): adună dispozitivele aprobate, workspace-urile, sesiunile, claims-urile și jurnalul tuturor; servește panoul web și canalul de actualizări.
- **Panoul web** (`panel/index.html`, servit de hub la `/panel`): aceleași date în browser, cu filele Activitate, Proiect și Dispozitive.

Nu configurezi nimic: instalezi pluginul, deschizi Studio și apari în hub. Singurul pas uman este aprobarea PC-ului tău de către admin, o singură dată. Nu există login în plugin, profile dedicate, chei API sau gateway de autentificare: CLI-ul rulează cu contul cu care ești deja autentificat în terminal.

Referințe: `STUDIO_HUB_CONTRACT.md` (rute, câmpuri, coduri), `design/DESIGN.md` (sistemul de design), `design/V1-BRIEF.md` (deciziile 1.0), `VERIFICATION.md` (ce a fost verificat).

## Instalare în 3 pași

**Cerințe:** Windows cu Roblox Studio și MCP-ul activat (**Assistant → … → Manage MCP Servers → Enable Studio as MCP server**), Python 3.10 sau mai nou în `PATH`, Claude Code **2.1.259+** și/sau Codex **0.154+** instalate oficial și autentificate în terminal.

### 1. Pluginul Studio

Deschide **`Install-Studio-Plugin.cmd`**. Construiește pachetul, îl copiază în folderul local de pluginuri Studio cu codul local injectat automat, copiază modulele aplicației în `Roblox\Plugins\StudioHarness\app\` și păstrează versiunea anterioară ca backup în `%LOCALAPPDATA%\StudioHarness\plugin-backups\`.

Salvează lucrul din Studio și repornește-l o dată (loader nou). În fila **Plugins** apare grupul **Studio Harness** cu butonul **Hub**.

### 2. Pluginul Claude Code (și/sau Codex)

Instalează-l o singură dată, ca plugin al utilizatorului; se aplică apoi oricărui folder în care deschizi Claude Code:

```powershell
claude plugin marketplace add "C:\cale\catre\roblox-studio-harness"
claude plugin install roblox-studio-harness@studio-harness
```

Pluginul aduce serverul MCP `studio_hub` (`.mcp.json`), hook-urile care trimit promptul și textul asistentului în hub (`hooks/hooks.json`) și skill-ul `studio`. La prima sesiune, serverul MCP pornește daemon-ul în fundal; jurnalul lui este în `%LOCALAPPDATA%\StudioHarness\daemon.log`. Fiecare sesiune nouă de terminal apare live în pluginul din Studio, cu numele providerului și al folderului, iar când o închizi trece în „Finalizat”; dacă terminalul este ucis brutal, daemon-ul o închide singur după o jumătate de minut și îi eliberează claims-urile.

Pentru o probă rapidă (`claude --plugin-dir "C:\cale\catre\roblox-studio-harness"`) pluginul se încarcă doar pentru sesiunea aceea. Înăuntrul repo-ului, `.claude/settings.json` dezactivează intenționat copia de proiect a serverului `studio_hub`: acolo el vine din pluginul instalat, iar două servere cu același nume ar înregistra sesiunea de două ori.

Pentru Codex, deschide **`Install-Codex-Config.cmd`**: adaugă `mcp_servers.studio_hub` și `notify` în `%USERPROFILE%\.codex\config.toml`, cu backup, apoi repornește Codex.

### 3. Deschide Studio și cere aprobarea

Pluginul se conectează singur la daemon (fără cod tastat), daemon-ul se înregistrează singur la hub, iar PC-ul tău apare în panou ca dispozitiv **în așteptare**. Un admin îl aprobă o singură dată din panou (fila **Dispozitive**) sau din terminalul serverului (`--approve-pending`). Până atunci lucrezi normal, dar claims-urile rămân locale: colegii nu le văd.

Dacă folosești doar Studio, fără terminal deschis, pornește daemon-ul cu **`Start-Daemon.cmd`**.

## Identitatea Roblox și aprobarea dispozitivului

**Cine ești** vine din Roblox, **ce ai voie** vine din tokenul de dispozitiv. Sunt două lucruri separate:

- **Identitatea** este citită de plugin din Studio: `StudioService:GetUserId()`, numele prin `Players:GetNameFromUserIdAsync` și avatarul derivat din id (`rbxthumb://…` în Studio, `GET /hub/avatar?user=` în panou). Pluginul o trimite daemon-ului la conectare, la 30 s și când se schimbă jocul deschis. Ea este informativă: după ea ne recunoaștem între noi în listele de prezenți, în sesiuni, în claims și în jurnal. Fără cont disponibil (`user_id = 0`) apari cu numele mașinii.
- **Autorizarea** vine din `%LOCALAPPDATA%\StudioHarness\device-token`: 64 de caractere hexazecimale generate local, la prima pornire a daemon-ului. Daemon-ul îl trimite doar în antetul cererilor către hub, peste HTTPS; hub-ul reține doar `sha256` și `device_id` (primele 16 caractere din amprentă), pe care îl vezi și în plugin, și în panou.
- **Stările dispozitivului**: `pending` (înregistrat, așteaptă adminul), `approved`, `revoked`. Starea legăturii daemon → hub, afișată în plugin: `connecting`, `pending`, `approved`, `offline` (hub-ul nu răspunde sau rulează o versiune mai veche), `revoked`, `disabled` (daemon pornit cu `--no-hub`). În orice stare diferită de `approved`, sesiunile merg mai departe, dar claims-urile sunt doar locale, iar toolurile spun explicit asta.
- **Adminul** are un singur secret, creat de hub pe server: `hub-admin-token` (sau variabila de mediu `STUDIO_HARNESS_ADMIN_TOKEN`). Cu el intră în panou și aprobă, revocă sau uită dispozitive și schimbă modul de înrolare (`approve`, implicit, sau `open` = aprobare automată, doar în rețele de încredere).

Niciun token nu apare în interfață, în jurnale, în pachete sau în documente; daemon-ul afișează la pornire doar căile fișierelor.

## Workspace-uri

**Workspace = jocul deschis în Studio.** Cheia este `game:<gameId>`, iar pentru un loc fără joc publicat `place:<placeId>`; un fișier nepublicat intră în `local`. Cheia se calculează mereu din id-uri (hub-ul nu are încredere într-o cheie trimisă de client) și vine din identitatea raportată de plugin, deci se schimbă singură când deschizi alt joc.

Pe workspace se grupează tot: developerii prezenți, sesiunile, claims-urile, jurnalul și harta proiectului. Două jocuri diferite nu se blochează reciproc — un claim pe `Workspace.Map` într-un joc nu spune nimic despre celălalt. În plugin vezi workspace-ul curent și poți privi și celelalte („Alte workspace-uri”, doar de citit); în panou îl alegi din bara laterală.

## Lucrul

### Din terminal (Mod T)

Scrii în Claude Code sau Codex ca de obicei. Sesiunea apare în Studio și în panou cu prompturile, apelurile de tool, rezultatele și textul asistentului. Toolurile Roblox trec prin daemon, care alege singur instanța Studio după numele jocului din workspace și impune claims-urile. Permisiunile rămân ale terminalului; Studio cere aprobare doar pentru generare (`generate_*`), care poate consuma cote Roblox.

### Din Studio (Mod S)

**Sesiune nouă** în plugin → alegi `Claude Code` sau `Codex` → scrii cererea → **Trimite**. Daemon-ul rulează CLI-ul headless cu configurația ta normală, izolat de hook-urile și serverele MCP arbitrare ale terminalului. Cererile din ferestre diferite intră într-o coadă FIFO; aprobările apar în fereastra sesiunii, cu codul integral.

### Agenții și claims-urile

Toolurile de coordonare servite de daemon, în plus față de toolurile Roblox Studio:

| Tool | Ce face |
| --- | --- |
| `hub_board` | Tabla workspace-ului: starea hub-ului, colegii prezenți, sesiunile, claims-urile, ultimele modificări și sumarul hărții proiectului. |
| `hub_project` | Harta proiectului pe grupe funcționale (grafică, asseturi, audio, interfață, scripturi server/client/partajate, rețea, date, fizică, gameplay, setări). |
| `hub_claim` | Revendică exclusiv unul sau mai mulți subarbori înainte de a-i modifica; totul sau nimic, cu deținătorul la conflict. Răspunde cu `scope: "hub"` (comun cu colegii) sau `"local"` (hub indisponibil ori job fără workspace), iar la `local` adaugă `notice` cu motivul. |
| `hub_release` | Eliberează claims-urile tale. |
| `hub_wait` | Așteaptă (cel mult 300 s) până când o cale devine liberă; nu o revendică. |

Regulile impuse de daemon, nu doar recomandate:

- Un claim blochează pentru ceilalți strămoșii căii, calea și descendenții ei. `Zone3` și `Zone4` pot fi lucrate simultan; `Map` și `Zone3` nu.
- `multi_edit` cere claim pe script; `execute_luau` cere parametrul `scope` și refuză codul ale cărui literale ies din claims; Play, tastatura, mouse-ul și navigația cer claim-ul `@play`.
- Claims-urile expiră după 10 minute fără activitate, la sfârșitul sesiunii sau când le eliberezi (**Eliberează** în plugin ori în panou). Ceilalți primesc refuz cu numele deținătorului.
- Jurnalul reține fiecare modificare executată: cine, ce tool, ce căi, în ce workspace.

Agentul citește întâi `hub_board` și `hub_project`, apoi revendică subarborele grupei pe care o modifică — nu servicii întregi (`skills/studio/SKILL.md`).

## Panoul web

Butonul **Panou** din plugin deschide browserul la `<hub>/panel#device=…`; panoul salvează codul de dispozitiv în `localStorage`, îl șterge din adresă și continuă. Alternativ, în ecranul de login poți lipi un cod: 64 hex = dispozitiv, orice altceva de cel puțin 16 caractere = cod de administrator.

- **Așteptare**: un dispozitiv neaprobat vede doar ecranul „Dispozitivul tău așteaptă aprobarea”, cu `device_id`, numele Roblox și mașina; se reîncearcă singur la 5 s.
- **Activitate**: prezenții din workspace, sesiunile live (click → fluxul de evenimente), claims-urile și jurnalul.
- **Proiect**: harta workspace-ului pe grupe funcționale, cu căutare și detalii.
- **Dispozitive**: pentru admin, dispozitivele în așteptare cu **Aprobă** / **Revocă** / **Uită** și modul de înrolare; pentru ceilalți, doar propriul dispozitiv.

Datele se împrospătează singure (2 s pentru tablă, 30 s pentru harta proiectului), fără butoane de refresh. Tema dark/light urmează sistemul și se poate comuta; `?demo=1` arată panoul cu date de exemplu, fără nicio cerere către server.

## Auto-update

Repo-ul este publicat și ca **Space Docker** pe Hugging Face: același cod rulează hub-ul (portul 7860, HTTPS gratuit) și servește canalul `releases/manifest.json`.

- **Hub-ul găzduit** verifică manifestul în fiecare oră, oglindește pachetele noi în `/releases/` și aplică pachetul `hub`. Pe Linux se relansează singur cu socketul de ascultare moștenit (`--inherit-socket`), deci fără gol de port și fără reînregistrarea daemon-ilor.
- **Daemon-ul și pluginul Studio** verifică manifestul la 20 s după pornire și apoi la 6 ore, se actualizează de la hub (nu direct din upstream), verifică SHA256 și aplică pachetul doar când nu rulează sesiuni, cu backup. Modulele aplicației ajung în `Roblox\Plugins\StudioHarness\app\` și se încarcă **fără repornirea Studio-ului**; `.rbxmx`-ul este rescris (cu codul local injectat) doar când se schimbă versiunea loader-ului — atunci pluginul cere o repornire a Studio-ului.
- Canalul se schimbă sau se oprește din `update-channel.json` (`auto: false`) ori cu `STUDIO_HARNESS_AUTO_UPDATE=0`. Un checkout git nu este actualizat automat.

## Publicare

**`Publish.cmd`** = `python scripts/publish_release.py --github --bump patch`:

1. ridică versiunea în `.claude-plugin/plugin.json`, `scripts/studio_bridge.py`, `scripts/team_hub.py` și în antetul aplicației din `Main.luau` (versiunea loader-ului se schimbă manual);
2. reconstruiește `dist/StudioHarness.rbxmx` (cu `LocalToken` ca placeholder, niciodată cu un token real);
3. construiește pachetele `plugin`, `hub` și `studio_app`, cu `.sha256` și manifest;
4. scanează repo-ul după secrete (chei API, tokenuri, fișiere de stare) și **oprește publicarea** dacă găsește ceva;
5. face `git add -A`, `git commit`, `git push` cu credențialele tale git.

`--dry-run` doar construiește și scanează. Opțional, `--repo OWNER/nume` încarcă și Space-ul Hugging Face, cu `HF_TOKEN` din mediul terminalului publicatorului (niciodată dintr-un fișier). Codul de administrator al hub-ului găzduit se pune ca **Secret** al Space-ului (`STUDIO_HARNESS_ADMIN_TOKEN`), niciodată în repo.

## Hub propriu

Implicit nu ai nimic de făcut: daemon-ul se conectează la `https://lostcube.pro/roblox/harness`.

- **Pe acest PC** (teste, self-hosting): **`Start-Hub.cmd`** pornește `scripts/team_hub.py` pe portul 34880 și pasează mai departe argumentele hub-ului: `--show-admin-code` (afișează codul o dată la pornire; altfel se afișează doar calea `%LOCALAPPDATA%\StudioHarness\hub-admin-token`), `--approve-pending` (aprobă toate dispozitivele în așteptare și iese; iese cu 1 dacă hub-ul în execuție refuză codul), `--open-enrollment` (aprobare automată), `--listen`, `--port`, `--state-dir`, `--no-auto-update`. Hub-ul ascultă implicit **doar pe `127.0.0.1`**: `--listen 0.0.0.0` îl expune în rețea pe HTTP simplu și se folosește numai în spatele unui proxy HTTPS (la pornire hub-ul scrie un avertisment în acest caz; `Start-Hub.cmd` o spune în comentarii). Daemon-ul îl folosește cu `{"hub_url": "http://127.0.0.1:34880"}` în `%LOCALAPPDATA%\StudioHarness\config.json` sau cu variabila `STUDIO_HARNESS_HUB_URL`.
- **Pe un server Ubuntu**: `sudo bash deploy/ubuntu/install.sh` (systemd, Caddy sau nginx, HTTPS, Docker) — pașii compleți în `deploy/ubuntu/README.md`. Codul de administrator ajunge în `/var/lib/studio-harness/hub-admin-token`. Developerii care folosesc alt domeniu pun `{"hub_url": "https://<domeniul-vostru>/roblox/harness"}` în `config.json`.
- Peste `http://` se acceptă doar `127.0.0.1` și `localhost`; din LAN sau din internet hub-ul se folosește exclusiv prin HTTPS, cu un reverse proxy în față. O adresă respinsă lasă daemon-ul în starea `disabled`, nu îl trimite tăcut către hub-ul public.

## Siguranță și limite

- Nicio credențială Claude, ChatGPT sau Roblox nu ajunge în plugin, daemon, hub sau fișierele proiectului.
- CLI-urile headless nu moștenesc hook-urile și serverele MCP arbitrare ale terminalului (`--strict-mcp-config`, `--setting-sources ""`).
- Instanța Studio este fixată de daemon; agentul nu o poate schimba prin argumente. Tokenul unei sesiuni este limitat la acea sesiune și nu ajunge niciodată la hub.
- Publicarea, ștergerea, DataStore, permisiunile și generarea cer aprobare explicită. Un refuz de claim sau de aprobare nu se ocolește.
- Nu se ocolesc autentificarea Roblox, dreptul Edit, moderarea sau permisiunile Studio. Codul generat poate fi greșit; păstrează copii ale proiectelor importante.
- Daemon-ul ascultă exclusiv pe `127.0.0.1`, respinge cererile cross-origin și cere codul local pe rutele UI. Singurul proces expus în rețea este hub-ul, protejat de tokenuri de dispozitiv aprobate și de codul de administrator, cu HTTPS în față.
- Identitatea Roblox nu este verificată criptografic — este o etichetă, nu o credențială. Cine poate scrie în directorul de stare al unui PC aprobat poate acționa ca acel dispozitiv.

## Diagnostic

- **Pluginul spune „Daemon · nu răspunde”**: pornește o sesiune CLI cu pluginul sau `Start-Daemon.cmd`; vezi `%LOCALAPPDATA%\StudioHarness\daemon.log`.
- **„Pe portul 34871 rulează un daemon cu alt token local…”**: pe port ascultă un daemon pornit cu alt `--state-dir` (sau un bridge vechi 0.3). Închide-l și reîncearcă.
- **„Așteaptă aprobarea adminului”**: normal la primul PC; cere aprobarea din panou. Verifică `device_id`-ul afișat în plugin cu cel din fila Dispozitive.
- **„Hub offline” sau „Hub-ul rulează o versiune mai veche.”**: hub-ul nu răspunde sau este încă pe 0.8. Lucrul continuă cu claims locale.
- **„Mai multe instanțe Studio deschise; alege Studio-ul țintă în Avansat.”**: fixează instanța din secțiunea Avansat a pluginului.
- **„Fără claim pe …”**: agentul trebuie să apeleze `hub_claim` sau să aștepte cu `hub_wait`; deținătorul o poate elibera.
- **Serverul `studio_hub` nu pornește în CLI**: verifică `python --version` (3.10+) și `daemon.log`.
- **Roblox `User is not authenticated`**: reautentifică Roblox Studio. Loginul Claude sau Codex nu îl înlocuiește.

## Structura repo-ului

| Cale | Ce conține |
| --- | --- |
| `studio-plugin/StudioHarness.server.luau` | Loader-ul 1.1.0: citește codul local injectat (`LocalToken`), pornește aplicația și o schimbă la cald. |
| `studio-plugin/modules/` | Aplicația: `Main`, `SessionStore` (stare pură), `BridgeController` (HTTP, identitate), `Theme` (sistemul de design), `HubView`, `SessionView`, `ApprovalView`, `ProjectScanner`. |
| `studio-plugin/tests/` | Spec-uri pure Luau: `SessionStore`, `ProjectScanner`, `Loader`, `Theme`. |
| `dist/StudioHarness.rbxmx` | Pachetul instalabil (cu placeholder pentru codul local). |
| `scripts/studio_bridge.py` | Daemon-ul: HTTP loopback, coadă Mod S, sesiuni de terminal, claims, jurnal, proxy MCP, identitate, actualizare. |
| `scripts/team_hub.py` | Hub-ul central: dispozitive, workspace-uri, sesiuni, claims, jurnal, panou, canal de actualizări. |
| `scripts/team_client.py` | Clientul hub din daemon (`HubClient`): înregistrare, sync la 2 s, oglinda locală, claims. |
| `scripts/claims.py`, `scripts/project_map.py`, `scripts/local_state.py` | Modelul claims-urilor, cheile și harta proiectului, starea locală (coduri, `config.json`). |
| `scripts/harness_mcp.py`, `scripts/bridge_mcp.py`, `scripts/harness_hook.py` | Serverul MCP `studio_hub` (Mod T), proxy-ul MCP per job (Mod S), hook-urile Claude Code și `notify` pentru Codex. |
| `scripts/providers.py`, `scripts/roblox_harness.py`, `scripts/mcp_client.py` | Adaptoarele CLI headless, CLI-ul de diagnostic, transportul MCP. |
| `scripts/build_studio_plugin.py`, `scripts/updater.py`, `scripts/publish_release.py`, `scripts/install-studio-plugin.ps1` | Construirea pachetului, auto-update-ul, publicarea și instalarea locală. |
| `panel/index.html` | Panoul web, un singur fișier, fără CDN. |
| `deploy/ubuntu/` | Instalare pe server: `install.sh`, unitatea systemd, Caddy, nginx, Dockerfile, `README.md`, `AI-BRIEF.md`. |
| `Dockerfile` | Space-ul Hugging Face care rulează hub-ul (port 7860). |
| `.mcp.json`, `hooks/hooks.json`, `skills/studio/SKILL.md`, `codex/config.snippet.toml` | Integrarea în CLI-uri. |
| `design/` | `V1-BRIEF.md` (deciziile 1.0), `DESIGN.md` (sistemul de design), machetele. |
| `tests/` | Suita Python (unittest), fără rețea externă și fără CLI-uri reale. |

## Construire și teste

```powershell
python scripts/build_studio_plugin.py
python -m unittest discover -s tests -v
```

Testele Python nu pornesc CLI-uri, browser sau inferență, nu ating rețeaua externă și nu scriu în starea reală (`STUDIO_HARNESS_STATE_DIR` și `HARNESS_TEST_TMP` indică directoare temporare). Pe Windows rulează-le cu `PYTHONIOENCODING=utf-8`. Pentru Luau, `luau-compile` și `luau` din release-ul oficial compilează modulele și rulează spec-urile. `.runtime/` conține doar configurații efemere de job.

Ce a fost verificat și cum se repetă: `VERIFICATION.md`.
