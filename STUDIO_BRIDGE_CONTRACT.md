# Contractul Studio Harness Bridge — joburile Mod S (aliniat la 1.0.0)

> Notă de versiune. Acest document a fost scris pentru 0.3.0 („Sesiuni Matrix”) și descrie baza joburilor `studio` (Mod S): fereastra de conversație din plugin, `POST /v1/chat`, deduplicarea, coada FIFO, `POST /v1/jobs/poll`, aprobările și proxy-ul MCP. Tot ce nu este menționat aici se decide după `STUDIO_HUB_CONTRACT.md` (1.0.0), care are prioritate la orice diferență. Secțiunile marcate „1.0” au fost corectate pentru 1.0: autentificarea providerilor din plugin a dispărut, tokenul UI este livrat automat de installer, interfața urmează `design/DESIGN.md` (nu mai există tema Matrix verde pe negru), iar pluginul are loader + aplicație cu hot swap.

Plugin NATIV Roblox Studio: un dock widget „Studio Harness” (hub) și ferestre flotante distincte pentru conversații. Nu sunt ferestre HTML în Studio.

## Limite și identități

- Un `window_id` local identifică o fereastră UI. Fiecare are propriile mesaje, draft, provider, scenă, `session_id`, `job_id`, cursor și aprobare. Nu se partajează un singur `state.job` între ferestre.
- `session_id` este identitatea conversației în daemon. Providerul și `studio_id` nu se schimbă pe o conversație existentă.
- `bridge_id` se schimbă la repornirea daemon-ului. UI invalidează atunci doar referințele remote/joburile vechi, păstrează textele locale și cere o conversație remote nouă; nu retransmite automat cereri.
- Minimizarea ascunde widgetul, nu anulează jobul. Oprește anulează numai jobul ferestrei. Închiderea definitivă a unei ferestre active cere confirmare și anularea jobului înainte de eliminare.
- Se pot trimite cereri din ferestre diferite, dar daemon-ul le execută **FIFO, una câte una**. Nu există inferență sau modificare de scenă simultană pentru Mod S. Restul ferestrelor afișează În coadă. (1.0: sesiunile din terminal, Mod T, rulează în paralel cu coada Mod S; apelurile lor către Studio sunt serializate de daemon.)
- Un singur job neterminal per `session_id`. Anularea unuia nu schimbă starea sau aprobările altuia.
- Scenele sunt reverificate înainte de pornirea providerului, nu sub lockul unei operații lungi în handlerul de enqueue.
- 1.0: fiecare job poartă `workspace` (cheia jocului, `STUDIO_HUB_CONTRACT.md` §2) și `owner` (contul Roblox al developerului). Ferestrele cu `mine == false` (sesiuni ale colegilor, oglindite prin hub) sunt doar de citit.

## HTTP local

Adresă `http://127.0.0.1:34871`, JSON UTF-8, antet `X-Studio-Harness-Token`. Tokenul UI (`local-token`) NU este o credențială Claude/Codex/Roblox. Daemon-ul verifică `Host` loopback, refuză antetul `Origin` și nu expune credențialele providerilor.

1.0: tokenul UI ajunge în plugin automat (`StringValue` `LocalToken` injectat în `.rbxmx` la instalare; loader-ul îl salvează cu `plugin:SetSetting("StudioHarnessBridgeToken")` și îl dă aplicației în handoff-ul de pornire `{ controller = { token, source = "loader" | "settings" } }`); pluginul se conectează singur la încărcare, cu reconectare 1 → 15 s. Câmpul manual de cod apare în „Avansat” doar când `Controller:needsToken()` este adevărat (niciun cod valid salvat sau daemon-ul l-a refuzat cu 401).

### Conectare și stare

`GET /v1/status` — forma completă este în `STUDIO_HUB_CONTRACT.md` §4.3. Pentru Mod S contează:

```json
{
  "ok": true,
  "version": "1.0.0",
  "bridge_id": "uuid-opac",
  "features": {"queued_sessions": true, "batch_events": true, "terminal_sessions": true, "claims": true, "board": true,
               "project": true, "plugin_bundle": true, "identity": true, "workspaces": true, "panel": true},
  "providers": {"claude": {"available": true}, "codex": {"available": true}},
  "active_job_id": null,
  "queued_count": 0
}
```

Dacă feature-urile cerute lipsesc, UI afișează clar că trebuie actualizat daemon-ul și nu trimite chat printr-un contract incompatibil. `status` nu pornește CLI-uri sau modele. 1.0: `providers.<p>` are doar `available` (CLI-ul oficial există pe PC); nu mai există `auth`, verificarea contului sau abonamentului.

- `GET /v1/studios` → `{ok:true,studios:[{id,name}],connected:boolean}`. Nu interoga în paralel cu operații native lungi din workerul de anulare. Poate fi goală în primele secunde de conectare.
- 1.0: `POST /v1/provider/check` și `POST /v1/provider/login` → **404**. Nu există login sau verificare de cont în plugin; CLI-ul rulează cu configurația normală a utilizatorului.

### Trimitere

`POST /v1/chat`:

```json
{
  "provider": "claude",
  "prompt": "...",
  "studio_id": "studio-uuid",
  "session_id": null,
  "client_request_id": "uuid-generat-o-data-pentru-acest-click",
  "context": {"place_id":0,"game_id":0,"place_name":"...","selection":[]}
}
```

Răspuns: `{ok:true,job_id,session_id,state:"queued"|"running",workspace}` (`workspace` = cheia jobului, `null` până la primul `POST /v1/identity`).

`studio_id` rămâne obligatoriu în Mod S (text nevid ≤ 256): fereastra fixează instanța la creare. `context` trebuie să fie un obiect; `prompt` ≤ 32 000 de caractere (daemon: „Alege un provider și un mesaj de maximum 32000 de caractere.”). Pluginul oprește trimiterea mai devreme, la 24 KB, cu „Mesajul trebuie să conțină text și să fie de cel mult 24 KB.”: un prompt refuzat de daemon ar pierde un tur întreg.

`client_request_id` este opțional pentru clienții vechi, dar obligatoriu în UI. Retrimiterea aceluiași ID cu același payload întoarce același job fără inferență nouă cât detaliile există. Același ID cu alt payload → 409. Se păstrează cel mult 64 de joburi detaliate și 4096 de înregistrări minimale de deduplicare pe durata daemon-ului. Retry-ul unui job eliminat din istoricul detaliat → 410 explicit, fără reexecutare. La atingerea limitei 4096, cererile noi sunt refuzate cu 429; ID-urile nu se reciclează în tăcere. UI nu face retry orb pentru POST-uri cu efecte.

Coada are o limită explicită, cel mult 32 de joburi neterminale. Plin → 429. Nu șterge joburi active la limitarea istoricului. 1.0: singura condiție de provider înainte de enqueue este `providers[provider].available`; nu mai există verificarea contului.

### Evenimente

Stări: `queued`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled` (1.0: `lost` doar pentru sesiunile remote ale colegilor offline).

`GET /v1/jobs/JOB/events?after=N` rămâne compatibil.

`POST /v1/jobs/poll` (read-only, fără inferență):

```json
{"jobs":[{"job_id":"...","after":0},{"job_id":"...","after":12}]}
```

Răspuns:

```json
{"ok":true,"bridge_id":"...","jobs":[
  {"ok":true,"job_id":"...","state":"running","events":[],"last_seq":12},
  {"ok":false,"job_id":"...","status":404,"error":"Cererea nu există."}
]}
```

Maximum 16 intrări pe poll. Un job lipsă nu anulează actualizarea celorlalte. Un poll la aproximativ o secundă pentru toate ferestrele active; fără worker/timer HTTP separat per fereastră. Evenimentele sunt aplicate strict ferestrei cu `job_id` corespunzător; fiecare fereastră își păstrează propriul cursor. 1.0: sesiunile remote (ale colegilor) se servesc din oglinda hub-ului, cu `remote: true`.

Tipuri de evenimente: `text`, `status`, `tool`, `approval`, `error`, `done`, plus (din 0.4) `prompt` și `claim`. Textul unui eveniment este limitat la 64 000 de caractere, iar un job reține ultimele 1024 de evenimente. Câmpuri: `seq`, `type`, `text?`, `tool?`, `approval_id?`, `arguments?`, `state?` (la `done`), `action?`/`paths?`/`holder?` (la `claim`). Un `status` cu `approval_active:false` și `approval_id` elimină doar acea aprobare. Evenimentele vechi se deduplică după cursor.

### Control

- `POST /v1/jobs/JOB/approve` cu `{approval_id,allow:boolean}`. Numai aprobarea activă a acelui job poate fi schimbată. 409 pentru aprobare expirată → UI elimină numai acel ID și avansează coada locală. 403 pe un job remote.
- `POST /v1/jobs/JOB/cancel` cu `{}` (acceptăm și `[]` pentru tabel Lua gol). Anulare urgentă, independentă de workerul de polling. Un job încă în coadă nu pornește providerul după anulare. Claims-urile jobului se eliberează.
- `POST /v1/jobs/JOB/release` cu `{paths?}` eliberează claims-urile jobului (0.4+).

## Proxy MCP

Token per job, distinct de tokenul UI; rutare doar `/agent/JOB/tools` și `/agent/JOB/call`. Un job queued/terminal nu poate folosi proxy-ul. `studio_id` rămâne fixat în daemon. Toate modificările din Mod S, inclusiv Luau arbitrar și Play/Stop, cer aprobare per apel în fereastra de origine și un claim pe subarborele atins (`STUDIO_HUB_CONTRACT.md` §6.3). Nu expunem `subagent` sau `skill` native pentru a lansa agenți suplimentari.

## UI (1.0)

Sistemul de design este `design/DESIGN.md` (paletă dark/light după tema Studio, `GothamMedium`/`Gotham` pentru UI, `Code` pentru id-uri și cod; chihlimbar pentru aprobare, roșu numai pentru erori). Invariantele de mai jos rămân obligatorii:

- Hub-ul (dock widget „Studio Harness”) arată identitatea Roblox, starea hub-ului, workspace-ul curent și prezenții, crearea de sesiuni (`Claude Code | Codex` + „Sesiune nouă”), sesiunile („Ale tale” / „În workspace”), aprobările, claims-urile și jurnalul; „Avansat” ține codul de asociere de rezervă, Studio-ul țintă, `device_id`-ul complet și deconectarea daemon-ului. Wireframe-urile și textele normative sunt în `design/DESIGN.md` §10 și §12.
- Un `DockWidgetPluginGui` cu ID distinct per sesiune, `InitialDockState.Float`, dimensiuni și limite minime. Chrome-ul exterior/mutarea sunt native. Nu promitem coordonate programatice sau aranjare automată neprobate.
- Se pot crea ferestre draft fără daemon conectat, dar nu se poate trimite o cerere fără provider disponibil și țintă validă.
- Fiecare fereastră: nume editabil, provider fix, scenă explicită, chat cu bule diferențiate (utilizator / asistent / tool / eroare / claim), composer, stare, minimizare, Nou (conversație nouă) și Oprește. Aprobările sunt în fereastra de origine, alături de chat când încape, dedesubt când e îngustă.
- Mesajele și codul sunt text literal, `RichText = false` peste tot, fără RichText activ pe date externe. Codul trebuie să fie integral inspectabil (wrap, scroll, TextFits/limită); altfel doar Refuză este activ.
- O fereastră minimizată continuă să primească evenimente prin controllerul comun. Sesiunea poate fi restaurată din hub. Închiderea hub-ului nu distruge ferestrele sesiunilor.
- Pentru restaurare (și pentru handoff-ul la hot swap) se păstrează metadate UI (ID local/nume/provider/vizibilitate/mesaje/draft/job/cursor, plus `owner`, `workspace`, `started` în 1.0), nu credențiale și nu referințe remote neverificate. Codul local trăiește doar în `Controller:snapshot()` (`{token, source, manualStop, identity}`), niciodată în `Store:serialize()`. Nu retransmite mesaje la restaurare.
- Nicio inferență înainte de Trimite. Anularea rămâne explicită. 1.0: conectarea la daemon este automată (tokenul livrat de loader); nicio cerere HTTP nu pleacă fără token.

## Împachetare (1.0)

`StudioHarness.server.luau` este loader-ul (Script de intrare, `-- Studio Harness Loader 1.1.0`), cu copilul `LocalToken` (StringValue, placeholder în repo, înlocuit la instalare). Aplicația este în `studio-plugin/modules/*.luau` (intrarea `Main.luau`, `-- Studio Harness App 1.0.0`); modulele devin ModuleScript-uri sub folderul **Modules**, sibling cu scriptul de intrare (fallback offline încorporat), și se încarcă prin `script.Parent.Modules`. La rulare, loader-ul înlocuiește aplicația fără repornirea Studio-ului cu bundle-ul servit de daemon (`GET /v1/plugin/bundle`, folder `App-<revision>`). Testele Luau din `studio-plugin/tests/` nu se includ în pachet.
