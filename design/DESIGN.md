# Studio Harness 1.0 — sistem de design

Contractul vizual comun pentru pluginul Studio (`studio-plugin/modules/Theme.luau` + view-uri) și panoul web (`panel/index.html`). Pornește de la `design/V1-BRIEF.md` §7 (valorile de acolo sunt punctul de plecare) și îl rafinează acolo unde contrastul, lizibilitatea sau consecvența o cer; fiecare abatere este argumentată la locul ei. Când acest document și codul se contrazic, documentul câștigă (după brief). Când brief-ul și documentul se contrazic, brief-ul câștigă.

Cine îl folosește:

| Faza | Agent | Ce citește obligatoriu |
|---|---|---|
| 4C | panou (login, pending, shell, Activitate, Dispozitive) | §2 tokens, §3 tipografie, §7 catalog, §9 CSS, §11 wireframe-uri panou, §12 mesaje |
| 5A | `Theme.luau`, `Main.luau`, `Theme.spec.luau` | §2, §5, §6, §7 (semnăturile Luau), §8 API Theme, §13, §14 |
| 5B | panou (Proiect, fluxul sesiunii, stări goale) | §7, §9, §11, §12 |
| 6A | `HubView.luau` | §7, §8, §10 (HubView), §12, §13 |
| 6B | `SessionView.luau`, `ApprovalView.luau` | §7 (bubble, card, banner), §8, §10 (SessionView, ApprovalView), §12 |
| 6C | polish panou | §5, §9 (responsive, teme), §13, §14 |
| 8C | revizie UX (read-only) | tot, plus lista de verificare din §15 |

Convenție de nume: fiecare componentă are **același nume** în CSS (clasa `.card`) și în Luau (`Theme.card`). Token-urile au același nume în CSS (`--text-2`) și în Luau (`text2`): kebab-case în CSS, camelCase în Luau, aceeași rădăcină.

---

## 1. Direcție estetică

### 1.1 Subiectul

Studio Harness este stratul de coordonare dintre 5–6 developeri și agenții lor A.I., în interiorul unui joc Roblox. Interfața trăiește într-un dock de 320 px lângă viewport-ul Studio și într-un panou web deschis pe un al doilea monitor sau pe telefon. Este citită în priviri scurte, între două editări: *cine mai e în jocul ăsta, ce rulează, ce îmi cere aprobarea, ce e blocat*. Nu este un terminal, nu este un dashboard de marketing, nu este o consolă de administrare.

Trei decizii dau identitatea produsului; restul este disciplină.

### 1.2 Prezența este centrul

Identitatea 1.0 este contul Roblox: nume și avatar, nu token-uri și nume tastate. De aceea oamenii sunt primul lucru vizibil în fiecare ecran: rândul de avatare din workspace (plugin), coloana de workspace-uri cu avatarele celor prezenți (panou), avatarul proprietarului pe fiecare card de sesiune. Avatarele sunt rotunde, se suprapun cu 6 px și au un inel de 2 px în culoarea suprafeței, ca să se citească drept „grup”, nu drept listă. „Tu” apare mereu primul.

### 1.3 Șina de stare (rail)

Starea (în lucru, cere aprobare, eroare, în coadă) apare într-un singur loc pe fiecare obiect: **o șină verticală de 3 px pe marginea stângă** a cardurilor de sesiune, a cardului de aprobare, a bannerelor și a rândurilor de mesaje din flux. Aceeași șină, aceeași grosime, aceeași paletă în plugin și în panou. Nimic altceva nu poartă culoare de stare: titlurile, fundalurile și conturul rămân neutre. Verdele primar apare doar pe butonul primar, pe starea „în lucru” și pe punctul „hub conectat”. Când o privire scurtă prinde o pată chihlimbar pe stânga, știe fără să citească: ceva așteaptă.

### 1.4 Text calm, cifre precise

Tot ce e omenesc (nume, titluri, mesaje, butoane) este sans-serif în greutăți 400/500/600. Monospace apare doar unde textul este un identificator: `device_id`, `place 1291603`, căi `Workspace.Map.Zone3`, argumentele unui tool. Cifrele sunt tabulare (`font-variant-numeric: tabular-nums`) ca timpul scurs să nu „danseze” la fiecare poll. Fără majuscule spațiate, fără etichete-eyebrow deasupra titlurilor, fără săgeți la capătul butoanelor, fără gradienți, fără umbre în dark, fără iconițe decorative sau emoji: singurele glife sunt `▾` / `▸` (pliere), `×` (închide) și punctul de stare.

### 1.5 Ce nu facem (moștenirea 0.8 și clișeele)

- Nu verde-terminal pe negru, nu font monospace pentru interfață, nu glow.
- Nu carduri pentru orice: un **card** este o promisiune că poți acționa pe el (deschizi sesiunea, decizi aprobarea, aprobi dispozitivul). Listele de fapte (claims, jurnal, membri, dispozitive) sunt **rânduri** fără chenar. Secțiunile nu au chenar: le structurează titlul și spațiul.
- Nu numerotări (01/02/03), nu separatori cu punct mediu în afara meta-liniilor cerute de brief (`place 1291603 · al tău`, `Daemon · 127.0.0.1:34871 · la zi`).
- Nu animații la încărcare, nu tranziții pe fiecare card: mișcarea răspunde doar acțiunii utilizatorului (§13).
- Nu popover-uri sau meniuri plutitoare în plugin (ZIndex fragil în dock-uri): tot ce se deschide se deschide **inline**, împingând conținutul.

### 1.6 Ierarhia containerelor

Exact trei niveluri, în ambele medii:

1. **Fundal** (`bg`): dock-ul, pagina.
2. **Suprafață** (`surface`): carduri, bara laterală, bara de sus, composer-ul, bulele utilizatorului în light.
3. **Ridicat** (`raised`): chip-uri, controlul segmentat, input-urile în light, hover pe rânduri, bula utilizatorului în dark.

Un element nu sare două niveluri (un chip nu stă direct pe `bg` decât în antet). Bordura de 1 px (`border`) separă nivelurile în dark; în light o face și umbra discretă (§4.3).

---

## 2. Tokens

### 2.1 Neutre (valorile din brief, păstrate)

| Token (CSS / Luau) | Rol | Dark | Light |
|---|---|---|---|
| `--bg` / `bg` | fundal pagină, dock, input în dark | `#0B0F14` | `#F6F8FA` |
| `--surface` / `surface` | card, bară, composer | `#121821` | `#FFFFFF` |
| `--raised` / `raised` | chip, segmented, hover rând, input în light | `#1A2230` | `#F0F3F7` |
| `--border` / `border` | contur implicit (1 px) | `#26303F` | `#D6DCE5` |
| `--text` / `text` | text principal | `#E6EDF3` | `#0F172A` |
| `--text-2` / `text2` | text secundar, meta, placeholder | `#8B98A9` | `#5B6675` |

Contrast (WCAG 2.1, calculat): `text` pe `bg`/`surface`/`raised` = 16,3 / 15,1 / 13,5 (dark), 16,8 / 17,9 / 16,0 (light). `text2` = 6,6 / 6,1 / 5,4 (dark), 5,5 / 5,8 / 5,2 (light). Toate ≥ 4,5:1, deci `text2` poate fi și placeholder, și meta, și text de rând secundar.

### 2.2 Neutre derivate (rafinare)

| Token | Rol | Dark | Light | De ce |
|---|---|---|---|---|
| `--text-3` / `text3` | **doar decorativ** | `#6B7A8F` | `#7B8797` | 3,3–4,4:1 pe suprafețele folosite — sub 4,5 peste tot, deci **nu poate purta text**. În Luau îl folosește doar bara de scroll (`Theme.scroll`); în panou nu este folosit deloc (`.auth__foot` a trecut pe `--text-2`), dar rămâne definit în `:root` pentru paritatea cheilor cu `Theme.PALETTES` |
| `--border-strong` / `borderStrong` | contur la hover pe card, separator subsol | `#3A4657` | `#B8C2CF` | vizibil fără să concureze cu textul |
| `--border-input` / `borderInput` | conturul câmpurilor | `#66768C` | `#7B8797` | 3,5 / 3,7:1 față de fundalul câmpului: limita unui control trebuie să aibă ≥ 3:1 (WCAG 1.4.11) |
| `--focus` / `focus` | inel de focus (2 px) | `#60A5FA` | `#1D4ED8` | 7,0 / 6,7:1 pe suprafață; albastru, ca să nu se confunde cu „în lucru” (verde) |
| `--overlay` | fundal semitransparent sub dialogul de confirmare | `rgba(11,15,20,.55)` | `rgba(15,23,42,.35)` | numai web |
| `--shadow-1` | card în light | fără | `0 1px 2px rgba(15,23,42,.06)` | dark-ul separă prin bordură, nu prin umbră |
| `--shadow-2` | element deschis inline, drawer | fără | `0 8px 24px rgba(15,23,42,.12)` | |

### 2.3 Roluri de culoare (accente)

Un accent are patru fețe. Numele din brief (`primar`, `info`, `atenție`, `pericol`, `Claude`, `Codex`) devin șapte roluri (`neutral` în plus), fiecare cu:

- `fill` — umplere: butonul primar, culoarea de bază a rolului (valorile din brief, identice în ambele teme);
- `text` — text pe `surface`/`raised`: ≥ 4,5:1;
- `mark` — punct, șină, contur de stare: ≥ 3:1 (obiect grafic informativ, WCAG 1.4.11);
- `soft` — fundal de chip/banner: `fill` la 14 % peste `surface`, cu textul rolului deasupra (≥ 4,5:1 verificat).

În dark, culorile din brief sunt suficient de luminoase: `text = mark = fill`. În light, `#4ADE80` pe alb are 1,7:1 și `#FBBF24` 1,7:1 — nu pot fi text sau punct; de aceea light primește nuanțe închise pentru `text` și `mark`, iar `fill` rămâne cel din brief (butonul primar arată la fel în ambele teme).

| Rol | `--<rol>` / `<rol>` (fill) | `--<rol>-text` dark | light | `--<rol>-mark` dark | light | `--<rol>-soft` dark | light |
|---|---|---|---|---|---|---|---|
| `primary` | `#4ADE80` | `#4ADE80` (10,2) | `#166534` (7,1) | `#4ADE80` | `#15803D` (5,0) | `#1A342E` | `#E6FAED` |
| `info` | `#60A5FA` | `#60A5FA` (7,0) | `#1D4ED8` (6,7) | `#60A5FA` | `#2563EB` (5,2) | `#1D2C3F` | `#E9F2FE` |
| `warning` | `#FBBF24` | `#FBBF24` (10,7) | `#92400E` (7,1) | `#FBBF24` | `#B45309` (5,0) | `#332F21` | `#FEF6E0` |
| `danger` | `#F87171` | `#F87171` (6,4) | `#B91C1C` (6,5) | `#F87171` | `#DC2626` (4,8) | `#32242C` | `#FEEBEB` |
| `claude` | `#D97757` | `#D97757` (5,7) | `#9A4322` (6,6) | `#D97757` | `#C4643F` (4,0) | `#2E2529` | `#FAECE7` |
| `codex` | `#10A37F` | `#22B38C` (6,7) | `#0B7A5F` (5,3) | `#10A37F` (5,6) | `#0B7A5F` | `#122B2E` | `#DEF2ED` |
| `neutral` | `#8B98A9` | `text2` | `text2` | `#6B7A8F` (4,1) | `#6B7A8F` (4,4) | `#232A34` | `#EFF1F3` |

În paranteze: contrastul pe `surface`. Textul rolului pe fundalul `soft`: dark 7,7 / 5,6 / 8,0 / 5,3 / 4,8 / 5,6 / 4,9; light 6,5 / 5,9 / 6,6 / 5,6 / 5,7 / 4,6 / 5,2 — toate ≥ 4,5:1. Codex dark folosește `#22B38C` ca text pentru că `#10A37F` pe `raised` ajunge la 5,0 și pe `codex-soft` sub 4,5.

Două token-uri speciale:

| Token | Rol | Dark | Light |
|---|---|---|---|
| `--on-primary` / `onPrimary` | text pe butonul primar | `#06210F` (9,8 pe `#4ADE80`) | `#06210F` |
| `--completed-mark` / `completedMark` | șina/punctul „finalizat” (primar mai șters, cf. brief) | `#35895A` (4,1) | `#3F8F5E` (4,0) |

### 2.4 Butonul primar și hover

| Token | Dark | Light |
|---|---|---|
| `--primary-hover` / `primaryHover` | `#5CE58F` (mai deschis: în dark, hover luminează) | `#3ED474` (mai închis: în light, hover întunecă) |
| `--primary-active` / `primaryActive` | `#6FEB9C` | `#34C96A` |
| conturul butonului primar | fără | 1 px `primary-mark` (`#15803D`, 5,0:1 pe alb) — limita butonului trebuie să existe pe alb |

`on-primary` rămâne ≥ 7,9:1 pe toate cele trei stări.

### 2.5 Lista completă (referință pentru `Theme.PALETTES` și pentru `:root`)

Ordinea și numele sunt normative; `Theme.spec.luau` verifică că `dark` și `light` au exact același set de chei.

```
bg surface raised border borderStrong borderInput text text2 text3 focus
primary primaryText primaryMark primarySoft primaryHover primaryActive onPrimary completedMark
info infoText infoMark infoSoft
warning warningText warningMark warningSoft
danger dangerText dangerMark dangerSoft
claude claudeText claudeMark claudeSoft
codex codexText codexMark codexSoft
neutral neutralText neutralMark neutralSoft
```

(`neutralText` = valoarea lui `text2`, duplicată, ca `Theme.roleTokens("neutral")` să funcționeze uniform.) Web adaugă `--overlay`, `--shadow-1`, `--shadow-2`, `--sans`, `--mono`, `--secondary-hover`.

`--secondary-hover` **nu este o culoare de paletă**, ci un alias derivat pentru hover-ul butonului secundar: `bg` în dark, `border`
în light (`{ dark = "bg", light = "border" }` în `Theme.hover`, `var(--bg)` / `var(--border)` în `:root`). Ambele medii folosesc
aceleași două valori; nu se adaugă o cheie în `Theme.PALETTES` pentru el.

---

## 3. Tipografie

### 3.1 Familii

| Mediu | Rol | Familie |
|---|---|---|
| web | interfață | `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif` (`--sans`) |
| web | identificatori | `ui-monospace, "Cascadia Mono", Consolas, "SF Mono", Menlo, monospace` (`--mono`) |
| Studio | titluri, butoane, chip-uri | `Enum.Font.GothamMedium` (`Theme.fonts.ui`) |
| Studio | corp, meta | `Enum.Font.Gotham` (`Theme.fonts.body`) |
| Studio | identificatori, argumente | `Enum.Font.Code` (`Theme.fonts.mono`) |

Fontul de sistem este o alegere, nu o lipsă: panoul se deschide pe telefon și pe al doilea monitor, fără CDN (CSP), și trebuie să arate „de-al casei” lângă Studio. Personalitatea vine din scară, greutăți și spațiu, nu dintr-o familie exotică.

### 3.2 Scara (identică în ambele medii)

| Treaptă | Mărime | Interlinie web | Greutate web | Studio | Unde |
|---|---|---|---|---|---|
| `xs` | 12 | 16 | 400 | Gotham / Code | meta, timp scurs, id-uri, căi, versiune |
| `sm` | 13 | 18 | 400 | Gotham | corp dens: rânduri, chip-uri (500), mesaje de tool |
| `md` | 14 | 20 | 400 / 500 | Gotham / GothamMedium | corp: mesaje, titlu de card (500), butoane (500) |
| `lg` | 16 | 22 | 600 | GothamMedium | titlu de secțiune, numele workspace-ului |
| `xl` | 20 | 26 | 600 | GothamMedium | titlul paginii (panou), titlul ferestrei de sesiune |

Greutăți web: 400 corp, 500 etichete și butoane, 600 titluri. Nimic la 700. Studio nu are 500/600 în Gotham, de aceea GothamMedium ține locul ambelor.

### 3.3 Reguli

- Lungimea unei linii de text curent ≤ 80 de caractere: coloana de conținut a panoului are `max-width: 72ch` pentru mesaje și jurnal; fluxul unei sesiuni are `max-width: 64ch`.
- Sentence case peste tot („Sesiune nouă”, nu „Sesiune Nouă”, nu „SESIUNE NOUĂ”).
- Cifre tabulare pe orice se împrospătează (timp scurs, contoare): web `font-variant-numeric: tabular-nums`; în Studio, textul de timp are lățime fixă (`Size.X.Offset = 64`) și aliniere la dreapta.
- Trunchiere: o singură linie → `text-overflow: ellipsis` / `TextTruncate = AtEnd`; text multi-linie → `Theme.fit(text, limit)` (adaugă `…`), niciodată tăiere în mijlocul unui caracter UTF-8 (`Store.prefix` este sigur pe octeți).
- Identificatorii lungi se scurtează cu sens: `device_id` la 8 caractere (`ab12cd34`), `job_id` la 12, căile din jurnal la ultimele 3 segmente cu `…` în față când depășesc 48 de caractere.
- `RichText = false` pe fiecare `TextLabel`/`TextButton`/`TextBox`; în panou tot textul provenit din date intră cu `textContent`.

---

## 4. Spațiere, raze, elevație

### 4.1 Spațiere (scara 4/8/12/16/24)

| Token | px | Folosire |
|---|---|---|
| `xs` | 4 | între chip-uri, între punct și text, între avatarele suprapuse (−6 este excepția vizuală) |
| `sm` | 8 | între rânduri, între butoanele unui grup, padding orizontal de chip |
| `md` | 12 | padding de card, padding de banner, între titlul secțiunii și conținut, padding orizontal de buton |
| `lg` | 16 | padding-ul dock-ului și al coloanei de conținut la ≤ 400 px, între carduri |
| `xl` | 24 | între secțiuni, padding-ul coloanei la desktop |

Dock-ul plugin: padding 16 lateral, 12 sus; secțiuni la 24 una de alta; înălțimi de control 32 (buton, segmented, input), 36 (butonul primar „Sesiune nouă”), 28 (buton mic: „Panou”, „Eliberează”, „Alte workspace-uri ▾”), 20 (chip). Panou: aceleași înălțimi; pe ecran ≤ 400 px țintele tactile cresc la 40 px (butoane) fără să schimbe textul.

### 4.2 Raze

| Token | px | Elemente |
|---|---|---|
| `control` | 6 | buton, input, textarea, chip, segmented, tab (interior), rândul selectat |
| `card` | 10 | card, banner, secțiune deschisă inline, drawer, cardul de login |
| `pill` | 999 | avatar, punct, contorul din titlul secțiunii |

Trei raze, nu una: raza spune ce este obiectul.

### 4.3 Elevație

| Nivel | Dark | Light |
|---|---|---|
| 0 fundal | `bg` | `bg` |
| 1 suprafață | `surface` + bordură 1 px `border` | `surface` + bordură 1 px `border` + `--shadow-1` |
| 2 ridicat | `raised` + bordură 1 px `border` | `raised` (fără umbră; stă pe suprafață) |
| deschis inline (lista de workspace-uri, secțiunea Avansat) | `surface` + bordură `border-strong` | `surface` + `--shadow-2` |

Umbre doar în light și doar la nivelul 1–2; în dark, bordura face toată treaba.

---

## 5. Stări interactive

| Stare | Buton primar | Buton secundar | Buton ghost | Buton danger | Card acționabil | Rând | Input |
|---|---|---|---|---|---|---|---|
| implicit | `primary` / `on-primary` | `raised` / `text`, bordură `border` | transparent / `text2` | `danger-soft` / `danger-text`, bordură `danger-mark` la 40 % | `surface`, bordură `border`, șină | transparent | `bg` (dark) / `surface` (light), bordură `border-input` |
| hover | `primary-hover` | `bg` (dark) / `#E6EAF0` (light), bordură `border-strong` | `raised` / `text` | `danger-mark` la 20 %, text `danger-text` | bordură `border-strong` | `raised` | bordură `border-strong` |
| apăsat | `primary-active` | ca hover, fără tranziție | ca hover | ca hover | — | — | — |
| focus (tastatură) | inel 2 px `focus`, offset 2 px | idem | idem | idem | idem | idem | bordură `focus` 2 px (fără offset) |
| dezactivat | opacitate 0,45, cursor `not-allowed`, fără hover | idem | idem | idem | — | — | opacitate 0,6, text `text3` |
| în lucru (după click) | textul devine verbul la gerunziu („Se trimite…”) și butonul e dezactivat | idem | — | idem | — | — | — |

Reguli:

- Hover nu schimbă niciodată culoarea textului unui buton primar sau danger; schimbă doar fundalul.
- Focus-ul de tastatură este vizibil pe orice element focusabil, în ambele teme, cu același inel (`:focus-visible`; în Studio: `Theme.focusRing` pentru `TextBox`, iar butoanele au `Selectable = true`).
- Dezactivat = opacitate, nu culoare gri separată (rămâne recognoscibil ce ar fi fost butonul).
- Web: `cursor: pointer` doar pe elemente care fac ceva; cardurile acționabile sunt `<button class="card">` sau conțin un `<button>` care le acoperă.
- Studio: `Theme.enabled(button, allowed)` setează `Active`, `Selectable`, atributul `EnabledAction` și transparența 0,45 (text, contur și, pentru primar, fundal); `Theme.click` ignoră click-urile când `EnabledAction` este fals.

---

## 6. Paleta de stări

Fiecare stare are trei lucruri: cheie (cod), etichetă (română, aceeași în plugin și panou) și rol de culoare (§2.3). Culoarea nu apare niciodată singură: lângă punct sau șină stă mereu eticheta.

### 6.1 Sesiune (`store.windows[*].state`, `sessions[*].state`)

| Cheie | Etichetă | Rol | Șina cardului | Chip | Note |
|---|---|---|---|---|---|
| `draft` | Draft | `neutral` | `neutral-mark` | `neutral` | doar în plugin (fereastră fără job) |
| `sending` | Se trimite | `info` | `info-mark` | `info` | tranzitoriu |
| `uncertain` | Trimitere neconfirmată | `warning` | `warning-mark` | `warning` | cere confirmare pentru reluare |
| `queued` | În coadă | `info` | `info-mark` | `info` | |
| `running` | În lucru | `primary` | `primary-mark` | `primary` | |
| `waiting_approval` | Cere aprobare | `warning` | `warning-mark` | `warning` | și când `#approvals > 0` sau `pending_approval` |
| `completed` | Finalizat | `completed` | `completed-mark` | `neutral` (text `text2`) | „primar mai șters” |
| `failed` | Eroare | `danger` | `danger-mark` | `danger` | |
| `cancelled` | Oprit | `neutral` | `neutral-mark` | `neutral` | |
| `lost` | Job indisponibil | `danger` | `danger-mark` | `danger` | daemon repornit / hub pierdut |

Etichete tranzitorii din view (nu stări): „Se oprește” (`cancelling`), „Eliberez claims” (`releasing`).

### 6.2 Hub (`store.hub.status`, `hub.status` în `/v1/status`)

| Cheie | Etichetă scurtă (antet) | Text complet (banner / subsol) | Rol punct | Banner |
|---|---|---|---|---|
| `connecting` | Se conectează | „Se conectează la hub…” | `neutral` | nu |
| `pending` | Așteaptă aprobarea | „Așteaptă aprobarea adminului · dispozitiv ab12cd34” | `warning` | da, `warning`, persistent |
| `approved` | Hub conectat | „Hub conectat” | `primary` | nu |
| `offline` | Hub offline | „Hub offline: <eroare>. Sesiunile tale merg local; claims-urile sunt doar locale până revine.” | `danger` | da, `danger`, persistent |
| `revoked` | Acces revocat | „Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou.” | `danger` | da, `danger`, persistent |
| `disabled` | Hub dezactivat | „Hub dezactivat (daemon pornit cu --no-hub).” | `neutral` | nu (apare doar în subsol) |

Daemon (subsol plugin): „Daemon · 127.0.0.1:34871 · la zi”, „Daemon · se conectează…”, „Daemon · nu răspunde (reîncerc în N s): <motiv>” (text `danger-text`), „Daemon deconectat la cerere. Joburile deja trimise pot continua; nu sunt retrimise automat.”, „Daemon · 127.0.0.1:34871 · actualizare în curs”, „Daemon · 127.0.0.1:34871 · actualizare 1.0.1 disponibilă (se instalează când nu rulează sesiuni)”. Sufixul de actualizare se adaugă doar când daemon-ul este conectat și compatibil.

### 6.3 Dispozitive și prezență (panou)

| Cheie | Etichetă | Rol |
|---|---|---|
| `pending` | În așteptare | `warning` |
| `approved` | Aprobat | `primary` |
| `revoked` | Revocat | `danger` |
| online (`last_seen` ≤ 15 s) | Online | `primary` (punct) |
| offline | Offline | `neutral` (punct `neutral-mark`, avatar la opacitate 0,55) |

### 6.4 Provider și tip

| Cheie | Etichetă | Rol chip |
|---|---|---|
| `claude` | Claude Code | `claude` |
| `codex` | Codex | `codex` |
| `studio` | Studio | `neutral` |
| `terminal` | Terminal | `neutral` |
| CLI găsit / negăsit | „CLI găsit” / „CLI negăsit” | punct `primary-mark` / `neutral-mark` |

### 6.5 Mesaje din flux (`message.kind`)

| Kind | Autor afișat | Aspect | Șină |
|---|---|---|---|
| `user` (utilizator) | Tu / „Tu · trimitere” / „<dev> · terminal” | bulă `raised` (dark) / `surface` cu bordură (light), aliniată la dreapta, max 85 % lățime | fără |
| `text` (notă locală de deschidere a ferestrei) | Status | se desenează ca `notice` | `neutral-mark` |
| `assistant` | Claude Code / Codex | fără fundal, autor în `claude-text` / `codex-text`, corp `text` | fără |
| `tool` | Instrument | corp `text2`, `sm`, numele toolului în mono | `info-mark` |
| `claim` | Coordonare | corp `text2`, `sm` | `warning-mark` |
| `approval` | Propunere | corp `text`, `sm` | `warning-mark` |
| `error` | Eroare / Trimitere neconfirmată | corp `danger-text`, `sm` | `danger-mark` |
| `notice` (status) | Status | corp `text2`, `sm` | `neutral-mark` |

### 6.6 Grupele proiectului (panou, fila Proiect)

Grupele funcționale (`graphics`, `assets`, `audio`, `ui`, `scripts_server`, `scripts_client`, `scripts_shared`, `networking`, `data`, `physics`, `gameplay`, `settings`, `other`) **nu** primesc culori proprii: o paletă categorială de 13 culori este zgomot. Bara de proporție a fiecărei grupe este `neutral-mark`; grupa selectată trece pe `primary-mark`. Intrările ținute de un claim primesc un chip `warning` cu numele deținătorului.

---

## 7. Catalog de componente

Reguli comune:

- **Rolul** este singura variabilă de culoare a unei componente. Web: atributul `data-role="primary|info|warning|danger|claude|codex|neutral|completed"` pe element; un singur bloc CSS (§9.2) traduce rolul în `--role-fill/--role-text/--role-mark/--role-soft`, iar componentele folosesc doar aceste patru variabile. Luau: `Theme.roleTokens(role)` întoarce aceleași patru chei; componentele memorează rolul în atributul `Role` și se repictează cu `Theme.recolor(instance, role)`.
- **Starea unei sesiuni** nu este rol: se traduce în rol prin `ROLE_OF_STATE` (JS) / `Theme.SESSION_ROLE` (Luau), tabelul din §6.1.
- Fiecare componentă are aceeași anatomie în ambele medii; wireframe-urile din §10–§11 folosesc doar aceste piese.
- Text în componente: sentence case, fără punct final pe etichete, cu punct pe propoziții (stări goale, bannere).

### 7.1 `card`

Container acționabil (sesiune, aprobare, dispozitiv în așteptare, workspace în bara laterală). Șina de stare pe stânga, conținut cu padding 12, rază 10.

```
┌▌─────────────────────────────────────┐
│▌ Titlu 14/500                de 4 min│  ← rând titlu + meta (dreapta, 12, text-2)
│▌ [Claude] [Studio]  (◯) ana · În lucru│ ← chip-uri + proprietar + etichetă de stare
└──────────────────────────────────────┘
```

| | Web | Luau |
|---|---|---|
| Clase / funcție | `.card` (mereu `<div>` în panou), `.card--clickable`, `.card--open` (conținut extins dedesubt), `.card--selected`, `.card__rail`, `.card__toggle`, `.card__head`, `.card__title`, `.card__meta`, `.card__state`, `.card__chips`, `.card__owner`, `.card__body`, `.card__actions` | `Theme.card(parent, options?) -> card` |
| Rol | `data-role` | `options.role`; `card.setRole(role)` |
| Hover | bordură `border-strong` (doar `--clickable`) | idem, prin `Theme.hover` intern |
| Focus | inel `focus` (pe `.card__toggle`) | `Selectable = true` |

În panou cardul acționabil **nu** poate fi `<button class="card">`: fluxul deschis conține butonul „Închide fluxul”, iar
butoanele imbricate sunt HTML invalid. Forma implementată este `<div class="card card--clickable">` cu un
`<button class="card__toggle" aria-expanded>` care acoperă antetul (`.card__head` cu titlul și meta) și rândul de chip-uri;
corpul extins (`.card__body`) și acțiunile (`.card__actions`) stau în afara butonului. În plugin cardul rămâne un `TextButton`,
pentru că acolo nu există imbricare de butoane.

`Theme.card(parent, { name?, role? = "neutral", clickable? = false, padding? = 12 })` întoarce `{ root, rail, body, setRole }`: `root` este `TextButton` (Text = "", `AutoButtonColor = false`) când `clickable`, altfel `Frame`; `ClipsDescendants = true` ca șina să urmeze colțurile; `rail` este `Frame` 3 × 100 % ancorat stânga, pictat `<rol>Mark`; `body` este `Frame` transparent cu `UIPadding` (12, 12 stânga + 3 pentru șină) și `UIListLayout` vertical cu pas 6, `AutomaticSize = Y`; `root.AutomaticSize = Y`. Click-ul se leagă din view cu `Theme.click(owner, card.root, fn)`.

Conținut normativ pentru cardul de sesiune (identic plugin/panou): rând 1 = titlul (prompt-ul scurtat la 60 de caractere sau numele ferestrei) + timp scurs; rând 2 = chip provider + chip tip + chip-uri suplimentare + avatar 16 + nume proprietar („tu” pentru sine) + `·` + eticheta stării. Rândul 2 se construiește cu `UIListLayout.Wraps = true` (proprietate nativă, nu o componentă nouă), deci la 320 px trece pe a doua linie în loc să fie tăiat; în panou `.card__chips` are `flex-wrap: wrap`. Claims-urile nu apar pe card (sunt în secțiunea lor); numărul de aprobări în așteptare apare ca chip `warning` „1 aprobare”.

### 7.2 `chip`

Etichetă scurtă, statică (provider, tip, stare, contor). 20 px, rază 6, padding 0 8, text 12/500 `<rol>Text` pe `<rol>Soft`.

| Web | Luau |
|---|---|
| `.chip` + `data-role`; `.chip--count` (contor numeric, `tabular-nums`) | `Theme.chip(parent, text, role? = "neutral") -> TextLabel` (`AutomaticSize = X`, `UICorner 6`, `UIPadding 0 8`, atribut `Component = "chip"`) |

Chip-urile nu sunt butoane și nu au hover. Un chip nu conține mai mult de 3 cuvinte.

### 7.3 `dot`

Punct de stare de 8 px (10 px în antet), rază 999, fundal `<rol>Mark`. Stă mereu la 4 px stânga de o etichetă text; niciodată singur, cu excepția rândului de avatare (prezență), unde eticheta este numele de lângă.

| Web | Luau |
|---|---|
| `.dot` + `data-role`; `.dot--lg` | `Theme.dot(parent, role, size? = 8) -> Frame` |

### 7.4 `avatar` și `avatar-row`

Avatar rotund din `rbxthumb://type=AvatarHeadShot&id=<userId>&w=48&h=48` (plugin) sau `GET /hub/avatar?user=<id>&size=48` (panou). Fallback: inițialele (`Theme.initials`) pe `raised`, text `text2`. Mărimi: 16 (proprietar pe card), 24 (rând de prezență), 28 (antet plugin), 32 (bara de sus a panoului, rând de dispozitiv), 40 (ecranul pending, cardul de login după autentificare).

| Web | Luau |
|---|---|
| `.avatar`, `.avatar--16/--24/--28/--32/--40`, `.avatar__img`, `.avatar__initials`, `.avatar--ring` (inel 2 px `surface`), `.avatar--offline` (opacitate 0,55); `img` cu `alt="<nume>"`, `onerror` → ascunde imaginea | `Theme.avatar(parent, identity?, options? = { size = 24, ring = false }) -> Frame` |
| `.avatar-row` (flex, avatare suprapuse `margin-left: -6px`, primul „tu”), `.avatar-row__names` (text 13), `.avatar-row__more` (chip `+3`) | `Theme.avatarRow(parent, members, options? = { size = 24, max = 6 }) -> { root, set(members) }` |

`Theme.avatar` construiește `Frame` (pill, `raised`) → `TextLabel` inițiale → `ImageLabel` deasupra (`Image = Theme.avatarUrl(userId)`, `ScaleType = Crop`, `BackgroundTransparency = 1`, pill). Dacă imaginea nu se încarcă, inițialele rămân vizibile dedesubt: nu e nevoie de detecție. `identity = nil` → inițiala „?” (cont necunoscut). Numele merge în `Name` (`Avatar_<name>`) pentru inspecție.

`avatarRow`: membrii sortați cu `me` primul, apoi online înaintea celor offline, apoi după `lastSeen` descrescător (nume ca
departajare); aceeași cascadă în panou (`byPresence` din `renderStack` și din lista de nume). Peste `max` se adaugă chip „+N”.

**Contorul apare o singură dată pe ecran.** În plugin secțiunea „Workspace” nu are chip de contor, deci numărul stă în rândul de
prezență: „tu, ana, mihai · 3 prezenți” (nume scurtate la 16 caractere, contor prin `Theme.countLabel`). În panou, secțiunea
„Prezenți” are deja chip-ul de contor, deci rândul de nume rămâne doar cu numele: „tu, ana, mihai” (primele trei).

### 7.5 `button`

Patru feluri, o singură formă: înălțime 32 (sm 28, lg 36), rază 6, padding 0 12, text 14/500 (sm: 13), centrat. Lățimea urmează conținutul pe web (`--block` pentru toată lățimea) și este dată explicit în Luau (implicit toată lățimea containerului).

| Fel | Când | Web | Luau `options.kind` |
|---|---|---|---|
| primar | o singură acțiune principală pe ecran („Sesiune nouă”, „Trimite”, „Permite”, „Intră”, „Aprobă”) | `.button.button--primary` | `"primary"` |
| secundar | acțiuni obișnuite („Refuză”, „Oprește”, „Înapoi”, „Folosește alt cod”) | `.button.button--secondary` (implicit) | `"secondary"` |
| ghost | acțiuni discrete în rânduri și antete („Panou”, „Eliberează”, „Alte workspace-uri ▾”, „Temă: Auto”) | `.button.button--ghost` | `"ghost"` |
| danger | acțiuni distructive cu confirmare („Revocă”, „Uită”, „Deconectează daemon-ul”, „Închide definitiv”) | `.button.button--danger` | `"danger"` |

Modificatori web: `.button--sm`, `.button--lg`, `.button--block`, `[disabled]`, `[aria-busy="true"]` (textul devine gerunziu, cursor `progress`).

`Theme.button(parent, text, options? = { kind = "secondary", size = "md", name?, width? }) -> TextButton`: setează fonturile și culorile felului, `UICorner 6`, `UIStroke` (secundar: `border`; danger: `dangerMark` la transparență 0,6; primar în light: `primaryMark`; ghost: fără), `AutoButtonColor = false`, `Selectable = true`, `RichText = false`, hover și apăsat prin `Theme.hover` (conectat pe instanță, fără owner: `Destroy()` deconectează). Activarea se leagă cu `Theme.click(owner, button, fn)`. Dezactivarea: `Theme.enabled(button, allowed)`.

Textul unui buton: verb la imperativ, ≤ 3 cuvinte, același cuvânt pe tot fluxul (butonul „Permite” → mesajul „Permis”; „Revocă” → chip „Revocat”).

### 7.6 `segmented`

Alegere exclusivă între 2–3 valori scurte (provider, mod de înrolare, temă). Container `raised` cu bordură `border`, rază 6, înălțime 32; elementul selectat: fundal `surface`, text `text`, bordură `border-strong`; neselectat: transparent, `text2`.

| Web | Luau |
|---|---|
| `.segmented` (`role="radiogroup"`, `aria-label`), `.segmented__item` (`<button role="radio" aria-checked>`); săgețile stânga/dreapta mută selecția | `Theme.segmented(parent, items, value, onChange) -> { root, buttons, set(value) }`, `items = { { key = "claude", text = "Claude Code" }, … }`; `onChange(key)` este apelat doar la schimbare |

Diferența față de `tab`: segmented alege o **valoare** (rămâne pe același ecran); tab alege un **ecran**.

### 7.7 `row`

Rând de listă fără chenar: text principal la stânga, meta la dreapta, acțiune opțională la capăt. Înălțime 32 (o linie) sau auto (două linii), padding 6 12, rază 6 la hover (`raised`) doar dacă rândul e acționabil. Separarea între rânduri: spațiu 4, nu linie.

```
Workspace.Map.Zone3                 tu · de 4 min   [Eliberează]
StarterGui.Shop                    ana · de 1 min
```

| Web | Luau |
|---|---|
| `.row`, `.row--two-line`, `.row--mono` (primarul în mono), `.row--clickable`, `.row__primary`, `.row__secondary`, `.row__action`, `.row__lead` (dot sau avatar în față) | `Theme.row(parent, options? = { name?, primary = "", secondary = "", mono = false, lines = 1, lead? = "dot"|"avatar", action? = { text, kind = "ghost" } }) -> { root, primary, secondary, action?, lead?, set(primary, secondary) }` |

Când textul principal nu încape, se trunchiază la capăt; meta are prioritate (lățime fixă 96 px pe web, 96 în Luau). Cu `lines = 2`, meta trece sub primar, aliniată stânga, `text2`.

### 7.8 `empty`

Starea goală a unei secțiuni: propoziție scurtă care spune ce va apărea aici și ce poate face utilizatorul, opțional un buton ghost. Aliniată stânga, `text2` 13, padding 12, fundal `raised` doar în panou (în plugin, fără fundal: dock-ul e deja aglomerat). Fără ilustrații, fără chenar punctat.

| Web | Luau |
|---|---|
| `.empty`, `.empty__text`, `.empty__hint` (12, `text3`), `.empty__action` | `Theme.empty(parent, text, options? = { hint?, action? = { text } }) -> { root, label, hint?, action? }` |

Textele normative sunt în §12.

### 7.9 `banner`

Mesaj de stare la nivel de ecran, cu șină de rol, fundal `<rol>Soft`, rază 10, padding 12, text 13 `<rol>Text`; opțional un buton ghost (acțiune) și `×` (închide) la bannerele temporare. Apare o singură dată pe ecran, sub antet.

| Web | Luau |
|---|---|
| `.banner` + `data-role`, `.banner__rail`, `.banner__text`, `.banner__action`, `.banner__close`; `role="status"` (`aria-live="polite"`); pentru `danger`: `role="alert"` | `Theme.banner(parent, options? = { role = "neutral", name? }) -> { root, rail, label, action?, show(role, text, seconds?), hide(), visible }` |

`show(role, text, seconds?)`: repictează șina, fundalul și textul; cu `seconds` se ascunde singur (`task.delay` cu contor de generație, ca un `show` mai nou să nu fie anulat de un `hide` vechi). Bannerul de „Actualizare aplicată” folosește `seconds = 5`.

### 7.10 `section`

Bloc vertical cu titlu 16/600, contor opțional (chip `--count`) și subtitlu 12 `text2`; corpul este o listă cu pas 8. Fără chenar. Varianta pliabilă (`Avansat`, `Alte workspace-uri`) are titlul ca buton ghost cu `▸`/`▾` în față și corpul într-un container `surface` cu bordură `border-strong` (light: `--shadow-2`).

```
Sesiuni                                    2
Ale tale
[card]
[card]
```

| Web | Luau |
|---|---|
| `.section`, `.section__header`, `.section__title` (`<h2>`), `.section__count`, `.section__subtitle`, `.section__body`, `.section--collapsible`, `.section--open`; header-ul pliabil e `<button aria-expanded>` | `Theme.section(parent, title, options? = { name?, collapsible = false, open = false }) -> { root, header, title, count, body, setCount(n?), subtitle(text?), setOpen(open), open }` |

`setCount(nil)` ascunde contorul; `subtitle("Ale tale")` adaugă un rând de subtitlu între header și corp (folosit pentru grupările „Ale tale” / „În workspace” — al doilea grup este o secțiune fără titlu, doar cu subtitlu).

### 7.11 `tab`

Navigare între ecrane în același container (Activitate / Proiect / Dispozitive în panou; Prezenți / Sesiuni în vizualizarea altui workspace din plugin). Text 14/500, padding 8 12, linie de 2 px `primary-mark` sub fila activă, `text2` pe cele inactive, `text` pe activă.

| Web | Luau |
|---|---|
| `.tabs` (`role="tablist"`), `.tab` (`<button role="tab" aria-selected aria-controls>`), `.tabpanel` (`role="tabpanel"`, `hidden` când e inactiv); săgeți stânga/dreapta + Home/End între file | `Theme.tab(parent, items, value, onChange) -> { root, buttons, set(value) }` (aceeași formă ca `segmented`) |

### 7.12 `input` și `textarea`

Câmp text pe o linie (cod de acces, căutare în proiect, numele sesiunii) sau pe mai multe (prompt). Înălțime 32 / min 72, rază 6, padding 0 10 (textarea 8 10), fundal `bg` în dark și `surface` în light, bordură `border-input`, text 14, placeholder `text2`. Focus: bordura devine `focus` de 2 px.

| Web | Luau |
|---|---|
| `.input`, `.input--mono`, `.textarea`; grupate în `.field` cu `.field__label` (`<label for>`), `.field__hint`, `.field__error` (13, `danger-text`, `aria-describedby`) | `Theme.input(parent, options? = { placeholder = "", height = 32, mono = false, name? }) -> TextBox`; `Theme.textarea(parent, options? = { placeholder = "", height = 72, name? }) -> TextBox` (`MultiLine`, `TextWrapped`, `TextYAlignment = Top`) |

Ambele atașează inelul de focus intern (`Theme.focusRing`), `ClearTextOnFocus = false`, `RichText = false`. Textul unui `TextBox` nu se rescrie cât timp are focus (`IsFocused()`), ca poll-ul să nu mănânce ce tastează utilizatorul.

### 7.13 `bubble`

Un mesaj din fluxul unei sesiuni. Șapte feluri (§6.5), toate în aceeași listă verticală cu pas 12.

```
                                   ┌───────────────────────┐
                                   │ Fă luminile mai calde │   ← user: bulă raised, dreapta, ≤ 85 %
                                   │ în Zone3.             │
                                   └───────────────────────┘
Claude Code                                                    ← assistant: autor în claude-text, 12/500
Am citit Lighting și găsesc trei surse…                          corp 14, fără fundal
▌ get_studio_state · 0,3 s                                     ← tool: șină info, 13 text-2, tool în mono
▌ Claim acordat: Workspace.Map.Zone3                           ← claim: șină warning
▌ Eroare: daemon-ul a raportat…                                ← error: șină danger, danger-text
```

| Web | Luau |
|---|---|
| `.flow` (lista), `.bubble`, `.bubble--user/--assistant/--tool/--claim/--approval/--error/--notice`, `.bubble__author`, `.bubble__body` (`white-space: pre-wrap`, `overflow-wrap: anywhere`), `.bubble__rail` | `Theme.bubble(parent, message, provider?) -> { root, author, body, set(message) }` |

`set(message)` schimbă textul fără să recreeze instanța (fluxul se actualizează la fiecare poll; recrearea ar face scroll-ul să sară). Textul lung este deja limitat de `Store.MAX_TEXT`; bula nu mai trunchiază.

### 7.14 Elemente doar web

- `.select` — `<select>` nativ stilizat ca un buton secundar (workspace-ul pe ecran îngust).
- `.topbar`, `.sidebar`, `.content`, `.shell` — structura din §11.3.
- `.wordmark` — „Studio Harness” 16/600 precedat de marca produsului: un inel de 12 px (`border: 2px solid var(--primary-mark)`) cu un punct de 4 px în centru, desenat în CSS (fără imagine), continuitatea favicon-ului 0.8 în paleta 1.0.
- `.kbd` — nu există: nu afișăm scurtături de tastatură în 1.0.

---

## 8. `Theme.luau` — specificația API (1.0.0)

### 8.1 Structura modulului

```lua
-- Studio Harness Theme 1.0.0 — sistemul de design al pluginului.
-- Nivelul de modul nu atinge globale Roblox (Color3, Enum, Instance, UDim2, settings, TweenService):
-- datele și funcțiile pure rulează în luau.exe (Theme.spec.luau). Tot ce creează instanțe stă în funcții.
local Theme = {}
Theme.VERSION = "1.0.0"
-- 1. date (PALETTES, SPACE, RADIUS, TEXT, FONT_NAMES, etichete, mapări)
-- 2. funcții pure (resolveMode, token, roleTokens, contrastRatio, elapsed, clock, initials, shortId,
--    avatarUrl, fit, countLabel, sessionState, messageRole, placeLine, hubLine)
-- 3. runtime: temă (apply, install, color, paint, recolor, tween, hover, focusRing, enabled)
-- 4. runtime: legături (connect, click, cleanup)
-- 5. runtime: constructori de bază (new, frame, label, input, textarea, scroll, list, padding, stroke, corner, spacer)
-- 6. runtime: componente (card, chip, dot, avatar, avatarRow, button, segmented, row, empty, banner, section, tab, bubble)
return Theme
```

Ordinea este normativă: un cititor găsește datele sus și componentele jos. Fiecare funcție are un comentariu de o linie în română. Fără `print`, fără `HttpService` (identitatea și avatarele vin din `BridgeController`; `Theme.avatarUrl` doar formatează un URL `rbxthumb://`).

### 8.2 Date

```lua
Theme.PALETTES = {
	dark  = { bg = "#0B0F14", surface = "#121821", raised = "#1A2230", border = "#26303F", borderStrong = "#3A4657",
		borderInput = "#66768C", text = "#E6EDF3", text2 = "#8B98A9", text3 = "#6B7A8F", focus = "#60A5FA",
		primary = "#4ADE80", primaryText = "#4ADE80", primaryMark = "#4ADE80", primarySoft = "#1A342E",
		primaryHover = "#5CE58F", primaryActive = "#6FEB9C", onPrimary = "#06210F", completedMark = "#35895A",
		info = "#60A5FA", infoText = "#60A5FA", infoMark = "#60A5FA", infoSoft = "#1D2C3F",
		warning = "#FBBF24", warningText = "#FBBF24", warningMark = "#FBBF24", warningSoft = "#332F21",
		danger = "#F87171", dangerText = "#F87171", dangerMark = "#F87171", dangerSoft = "#32242C",
		claude = "#D97757", claudeText = "#D97757", claudeMark = "#D97757", claudeSoft = "#2E2529",
		codex = "#10A37F", codexText = "#22B38C", codexMark = "#10A37F", codexSoft = "#122B2E",
		neutral = "#8B98A9", neutralText = "#8B98A9", neutralMark = "#6B7A8F", neutralSoft = "#232A34" },
	light = { bg = "#F6F8FA", surface = "#FFFFFF", raised = "#F0F3F7", border = "#D6DCE5", borderStrong = "#B8C2CF",
		borderInput = "#7B8797", text = "#0F172A", text2 = "#5B6675", text3 = "#7B8797", focus = "#1D4ED8",
		primary = "#4ADE80", primaryText = "#166534", primaryMark = "#15803D", primarySoft = "#E6FAED",
		primaryHover = "#3ED474", primaryActive = "#34C96A", onPrimary = "#06210F", completedMark = "#3F8F5E",
		info = "#60A5FA", infoText = "#1D4ED8", infoMark = "#2563EB", infoSoft = "#E9F2FE",
		warning = "#FBBF24", warningText = "#92400E", warningMark = "#B45309", warningSoft = "#FEF6E0",
		danger = "#F87171", dangerText = "#B91C1C", dangerMark = "#DC2626", dangerSoft = "#FEEBEB",
		claude = "#D97757", claudeText = "#9A4322", claudeMark = "#C4643F", claudeSoft = "#FAECE7",
		codex = "#10A37F", codexText = "#0B7A5F", codexMark = "#0B7A5F", codexSoft = "#DEF2ED",
		neutral = "#8B98A9", neutralText = "#5B6675", neutralMark = "#6B7A8F", neutralSoft = "#EFF1F3" },
}
Theme.SPACE = { xs = 4, sm = 8, md = 12, lg = 16, xl = 24 }
Theme.RADIUS = { control = 6, card = 10, pill = 999 }
Theme.TEXT = { xs = 12, sm = 13, md = 14, lg = 16, xl = 20 }
Theme.FONT_NAMES = { ui = "GothamMedium", body = "Gotham", mono = "Code" } -- rezolvate în Enum.Font de apply()
Theme.ROLES = { "neutral", "primary", "info", "warning", "danger", "claude", "codex", "completed" }
Theme.names = { claude = "Claude Code", codex = "Codex" }
Theme.kinds = { studio = "Studio", terminal = "Terminal" }
Theme.states = { draft = "Draft", sending = "Se trimite", uncertain = "Trimitere neconfirmată", queued = "În coadă",
	running = "În lucru", waiting_approval = "Cere aprobare", completed = "Finalizat", failed = "Eroare",
	cancelled = "Oprit", lost = "Job indisponibil" }
Theme.SESSION_ROLE = { draft = "neutral", sending = "info", uncertain = "warning", queued = "info", running = "primary",
	waiting_approval = "warning", completed = "completed", failed = "danger", cancelled = "neutral", lost = "danger" }
Theme.hubStates = { connecting = "Se conectează", pending = "Așteaptă aprobarea", approved = "Hub conectat",
	offline = "Hub offline", revoked = "Acces revocat", disabled = "Hub dezactivat" }
Theme.HUB_ROLE = { connecting = "neutral", pending = "warning", approved = "primary", offline = "danger",
	revoked = "danger", disabled = "neutral" }
Theme.MESSAGE_ROLE = { text = "neutral", assistant = "neutral", tool = "info", claim = "warning", approval = "warning",
	error = "danger", notice = "neutral" }
```

### 8.3 Funcții pure (testate în `Theme.spec.luau`)

| Semnătură | Întoarce | Reguli |
|---|---|---|
| `Theme.resolveMode(themeName: string?) -> "dark" \| "light"` | modul pentru numele temei Studio | `"Dark"` (indiferent de majuscule) → `"dark"`; orice altceva, inclusiv `nil` → `"light"` |
| `Theme.token(name: string, mode?: string) -> string` | hex-ul token-ului în modul dat (implicit `Theme.mode`) | nume necunoscut → `error("Token necunoscut: " .. name)`; erorile de tastare ies în spec, nu în Studio |
| `Theme.roleTokens(role: string) -> { fill: string, text: string, mark: string, soft: string }` | numele token-urilor (nu valorile) | `"completed"` → `{ fill = "primary", text = "text2", mark = "completedMark", soft = "neutralSoft" }`; rol necunoscut → `neutral` |
| `Theme.contrastRatio(hexA: string, hexB: string) -> number` | raportul WCAG 2.1 (≥ 1) | folosit de spec pentru a impune §2 |
| `Theme.elapsed(since: number?, now?: number) -> string` | „de 4 s” / „de 3 min” / „de 2 h” / „de 3 zile” | `since = nil` → `""`; `now` implicit `os.time()`; negativ → „de 0 s” |
| `Theme.clock(at: number?) -> string` | „14:02” (`os.date("%H:%M")`) | `nil` → „--:--” |
| `Theme.initials(name: string?) -> string` | 1–2 caractere majuscule | primele litere ale primelor două cuvinte; un singur cuvânt → primele două litere; gol/`nil` → „?”; sigur pe UTF-8 (nu taie un caracter la jumătate) |
| `Theme.shortId(id: string?, length?: number) -> string` | prefixul id-ului (implicit 8) | `nil` sau gol → „—” |
| `Theme.avatarUrl(userId: number?, size?: number) -> string?` | `rbxthumb://type=AvatarHeadShot&id=<id>&w=<s>&h=<s>` | `size` ∈ {48, 60, 100, 150}, implicit 48; `userId` lipsă sau ≤ 0 → `nil` |
| `Theme.fit(text: string?, limit: number) -> string` | textul ≤ `limit` octeți, cu `…` la capăt dacă a fost scurtat | aceeași tăiere sigură pe octeți ca `Store.prefix`; `nil` → `""` |
| `Theme.countLabel(n: number, singular: string, plural: string) -> string` | „1 sesiune”, „3 sesiuni”, „20 de sesiuni” | pluralul românesc: `n == 1` singular; 2–19 plural; ≥ 20 → `n .. " de " .. plural` |
| `Theme.sessionState(window: table) -> (key: string, label: string, role: string)` | starea afișată a unei ferestre | `#approvals > 0` sau `pendingApproval` → `waiting_approval`; `cancelling` → cheia curentă, eticheta „Se oprește”; `releasing` → „Eliberez claims”; stare necunoscută → eticheta brută, rol `neutral` |
| `Theme.messageRole(kind: string?, provider?: string) -> string` | rolul unei bule | `assistant` cu `provider` → `"claude"`/`"codex"`; altfel `Theme.MESSAGE_ROLE[kind]` sau `neutral` |
| `Theme.placeLine(workspace: table?, identity: table?) -> string` | meta-linia workspace-ului | `nil` sau `key == "local"` → „Fișier nepublicat”; `creatorId == identity.userId` → „place 1291603 · al tău”; `creatorType == "Group"` → „place 1291603 · grup 555”; altfel „place 1291603 · utilizator 555” |
| `Theme.hubLine(hub: table?) -> (text: string, role: string)` | textul complet pentru banner/subsol (§6.2) | `pending` include `dispozitiv <shortId(deviceId)>`; `offline` include `hub.error` scurtat la 80 |

### 8.4 Runtime: tema

| Semnătură | Efect |
|---|---|
| `Theme.mode: string` | modul curent; `"dark"` până la primul `apply` |
| `Theme.colors: { [string]: Color3 }` | token → `Color3`, construit de `apply` |
| `Theme.fonts: { ui: Enum.Font, body: Enum.Font, mono: Enum.Font }` | construit de `apply` |
| `Theme.apply(mode: string) -> string` | construiește `colors` și `fonts`, setează `Theme.mode`, repictează toate instanțele înregistrate prin `paint` (tabel cu chei slabe: instanțele distruse dispar singure). Idempotent; întoarce modul aplicat |
| `Theme.install(owner: table, onChange?: (mode: string) -> ()) -> string` | citește `settings().Studio.Theme.Name` prin `pcall` (eșec → `"dark"`), apelează `apply`, conectează `settings().Studio.ThemeChanged` prin `Theme.connect(owner, …)`: la schimbare → `apply(resolveMode(settings().Studio.Theme.Name))`, apoi `onChange(mode)`. `Main` îl apelează o singură dată, înainte de a construi orice view, cu `onChange = function() app:sync(nil) end` (view-urile își recalculează culorile dinamice în `update`) |
| `Theme.color(token: string) -> Color3` | din `Theme.colors`; token necunoscut → `error` |
| `Theme.paint(instance: Instance, roles: { [property: string]: string }) -> Instance` | setează fiecare proprietate (`BackgroundColor3`, `TextColor3`, `PlaceholderColor3`, `ImageColor3`, `ScrollBarImageColor3`, `Color` pentru `UIStroke`) la `Theme.color(token)` acum și memorează maparea pentru `apply`. Un `paint` ulterior pe aceeași instanță înlocuiește maparea (așa se schimbă rolul unei șine fără să pierzi re-tematizarea) |
| `Theme.recolor(instance: Instance, role: string)` | repictează o componentă după `instance:GetAttribute("Component")` (`chip`, `dot`, `rail`, `banner`, `bubble`) cu `roleTokens(role)`; setează atributul `Role` |
| `Theme.tween(instance: Instance, properties: table, seconds?: number)` | `TweenService:Create(instance, TweenInfo.new(seconds or 0.14, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), properties):Play()`; anulează tween-ul anterior al aceleiași instanțe (tabel slab) |
| `Theme.hover(button: GuiButton, tokens: { normal: string, hover: string, pressed?: string, stroke?: UIStroke, strokeNormal?: string, strokeHover?: string })` | `MouseEnter`/`MouseLeave`/`MouseButton1Down`/`MouseButton1Up` → `tween(BackgroundColor3)` și, dacă `stroke` e dat, `tween(stroke.Color)` (cardurile: fundal neschimbat, contur `border` → `borderStrong`); ignoră când `EnabledAction` este fals; conectat pe instanță (fără owner: `Destroy()` deconectează) |
| `Theme.focusRing(textBox: TextBox, stroke: UIStroke)` | `Focused` → `stroke` pictat `focus`, `Thickness = 2`; `FocusLost` → `borderInput`, 1 |
| `Theme.enabled(button: GuiButton, allowed: boolean)` | ca în 0.8 (`Active`, `Selectable`, atribut `EnabledAction`), transparență 0,45 pe text și contur; pentru `kind == "primary"` (atribut `Kind`) și pe fundal |

Mecanismul de re-tematizare pe scurt: **totul ce are culoare trece prin `paint`**. Nicio componentă nu scrie `BackgroundColor3 = Theme.colors.x` direct; astfel `apply` poate repicta orice, iar view-urile nu au cod special pentru `ThemeChanged` în afară de `app:sync(nil)`.

### 8.5 Runtime: legături (neschimbate față de 0.8)

| Semnătură | Efect |
|---|---|
| `Theme.connect(owner, signal, callback) -> RBXScriptConnection` | conectează și înregistrează în `owner.connections`; callback-ul nu rulează după `owner.destroyed` |
| `Theme.click(owner, button, callback) -> RBXScriptConnection` | `Theme.enabled(button, true)` + `Activated` filtrat prin `EnabledAction` |
| `Theme.cleanup(owner)` | `destroyed = true`, deconectează tot |

`owner` este orice tabel cu `connections = {}` și `destroyed = false` (view-urile).

### 8.6 Runtime: constructori de bază

| Semnătură | Întoarce / reguli |
|---|---|
| `Theme.new(className, properties?, parent?) -> Instance` | neschimbat |
| `Theme.frame(parent, name, token? = "bg") -> Frame` | `Size = fromScale(1, 1)`, `BorderSizePixel = 0`, pictat `BackgroundColor3 = token`; `token = "transparent"` → `BackgroundTransparency = 1` |
| `Theme.label(parent, text, options?) -> TextLabel` | `options = { size = "md" \| număr, token = "text", font = "body" \| "ui" \| "mono", height? (nil → AutomaticSize Y), wrap = true, truncate = false, align = "left" \| "right" \| "center", name? }`; `RichText = false`, `TextYAlignment = Top` când `height == nil`, altfel `Center` |
| `Theme.input(parent, options?) -> TextBox` / `Theme.textarea(parent, options?) -> TextBox` | §7.12 |
| `Theme.scroll(parent, name) -> ScrollingFrame` | ca în 0.8, `ScrollBarThickness = 4`, culoarea barei `text3`, `AutomaticCanvasSize = Y` |
| `Theme.list(parent, gap? = 8, horizontal? = false) -> UIListLayout` | `SortOrder = LayoutOrder`; orizontal → `FillDirection = Horizontal`, `VerticalAlignment = Center` |
| `Theme.padding(parent, x, y?) -> UIPadding` | `y` implicit `x` |
| `Theme.stroke(parent, token? = "border", thickness? = 1) -> UIStroke` | `ApplyStrokeMode = Border`, pictat `Color = token` |
| `Theme.corner(parent, radius) -> UICorner` | `radius` din `Theme.RADIUS` sau număr |
| `Theme.spacer(parent, height) -> Frame` | transparent, pentru distanțele dintre secțiuni când `UIListLayout` nu ajunge |

Semnăturile 0.8 `label(parent, text, height, size, color)` și `button(parent, text, height, accent)` dispar; tabelul de migrare este în §8.9.

### 8.7 Runtime: componente

Semnăturile din §7, cu tipurile complete:

```lua
Theme.card(parent: Instance, options: { name: string?, role: string?, clickable: boolean?, padding: number? }?)
	-> { root: GuiObject, rail: Frame, body: Frame, setRole: (role: string) -> () }
Theme.chip(parent: Instance, text: string, role: string?) -> TextLabel
Theme.dot(parent: Instance, role: string, size: number?) -> Frame
Theme.avatar(parent: Instance, identity: { userId: number?, name: string? }?, options: { size: number?, ring: boolean? }?) -> Frame
Theme.avatarRow(parent: Instance, members: { table }, options: { size: number?, max: number? }?)
	-> { root: Frame, set: (members: { table }) -> () }
Theme.button(parent: Instance, text: string, options: { kind: string?, size: string?, name: string?, width: UDim? }?) -> TextButton
Theme.segmented(parent: Instance, items: { { key: string, text: string } }, value: string, onChange: (key: string) -> ())
	-> { root: Frame, buttons: { [string]: TextButton }, set: (value: string) -> () }
Theme.row(parent: Instance, options: { name: string?, primary: string?, secondary: string?, mono: boolean?, lines: number?,
	lead: string?, action: { text: string, kind: string? }? }?)
	-> { root: Frame, primary: TextLabel, secondary: TextLabel, action: TextButton?, lead: GuiObject?, set: (primary: string, secondary: string?) -> () }
Theme.empty(parent: Instance, text: string, options: { hint: string?, action: { text: string }? }?)
	-> { root: Frame, label: TextLabel, hint: TextLabel?, action: TextButton? }
Theme.banner(parent: Instance, options: { role: string?, name: string? }?)
	-> { root: Frame, rail: Frame, label: TextLabel, show: (role: string, text: string, seconds: number?) -> (), hide: () -> (), visible: boolean }
Theme.section(parent: Instance, title: string, options: { name: string?, collapsible: boolean?, open: boolean? }?)
	-> { root: Frame, header: GuiObject, title: TextLabel, count: TextLabel, body: Frame,
	     setCount: (n: number?) -> (), subtitle: (text: string?) -> (), setOpen: (open: boolean) -> (), open: boolean }
Theme.tab(parent: Instance, items: { { key: string, text: string } }, value: string, onChange: (key: string) -> ())
	-> { root: Frame, buttons: { [string]: TextButton }, set: (value: string) -> () }
Theme.bubble(parent: Instance, message: { author: string, text: string, kind: string? }, provider: string?)
	-> { root: Frame, author: TextLabel, body: TextLabel, set: (message: table) -> () }
```

Reguli de construcție comune:

- Fiecare `root` are `LayoutOrder = 0` și `Name = options.name or "<Componentă>"`; view-ul setează `LayoutOrder`.
- `AutomaticSize = Y` pe tot ce conține text variabil; lățimea este `UDim2.new(1, 0, 0, h)` (umple containerul) dacă nu se cere altfel.
- Toate instanțele de text: `RichText = false`, `TextWrapped = true` (sau `TextTruncate = AtEnd` când `truncate`), `TextXAlignment = Left` implicit.
- Componentele nu memorează `owner` și nu apelează `store`: sunt pure ca structură; view-ul leagă evenimentele cu `Theme.click`/`Theme.connect`.
- Atributele `Component`, `Role`, `Kind`, `EnabledAction` sunt singurele metadate pe instanțe.

### 8.8 Interzis în `Theme.luau`

`HttpService`, `settings()` în afara lui `install`, `RichText = true`, `print`, `wait()`, `spawn`, culori literale în componente (numai token-uri), text hard-codat în componente (textul vine din view; excepțiile: „?” la inițiale, „—” la id lipsă, „+N” în `avatarRow`).

### 8.8.1 Extensii față de specificația inițială (implementate în faza 5A)

Regula din §15 („nimic în plus fără actualizarea documentului”) cere să fie listate aici:

| Adăugire | Ce face |
|---|---|
| `Theme.font(name) -> Enum.Font` | accesor care garantează construirea paletei (`ensureColors`) înainte de a întoarce fontul; `nil` → `body` |
| `Theme.TRANSIENT` | `{ cancelling = "Se oprește", releasing = "Eliberez claims" }` — etichetele tranzitorii folosite de `sessionState` |
| `Theme.OLD_HUB_ERROR` | „Hub-ul rulează o versiune mai veche.” — text public, ca `hubLine` și store-ul să nu îl dubleze |
| token pe moduri în `Theme.paint` | o valoare poate fi `{ dark = "bg", light = "surface" }`, rezolvată la fiecare repictare (folosit de `input`/`textarea`, §7.12, și de hover-ul butonului secundar) |
| `Theme.hover(button, tokens)` | acceptă în plus `fadeNormal`/`fadeHover` (butonul ghost și rândul acționabil, care nu au fundal în repaus) și `textNormal`/`textHover` |
| `Theme.row(parent, options)` | acceptă `clickable` (întoarce și `row.button`), `identity` (pentru `lead = "avatar"`) și `role` (pentru `lead = "dot"`) |
| `Theme.banner(parent, options)` | acceptă `action` (buton ghost în corp) și `close` (butonul `×` din colț) |
| `Theme.avatar(parent, identity, options)` | acceptă `offline` — opacitate 0,55 (transparență 0,45) pe fundal, inițiale și imagine (§6.3) |
| `Theme.button(parent, text, options)` | `width` acceptă și `"auto"` (`AutomaticSize.X`), pe lângă un `UDim` |
| `Theme.elapsed` | scrie „de 1 zi” la singular (restul: „de N zile”) |
| `Theme.empty` | în plugin nu are padding lateral (nu are fundal acolo), doar 4 px sus/jos; hintul folosește `text2`, nu `text3` |
| hover-ul butonului secundar | `bg` în dark și `border` în light (§5 cere `#E6EAF0` în light, care nu este token normativ în §2.5) |
| `Theme.buttonText(auto: boolean, fixedWidth: number?) -> (truncate: boolean, pad: number)` | geometria textului dintr-un buton: `auto` (lățime după text) nu trunchiază și păstrează padding-ul normal; orice lățime fixă trunchiază `AtEnd`; o lățime dată explicit sub 96 px primește padding 4, restul 12. Regula era deja în §3.3 („textele nu se trunchiază brutal”) și §7.5, dar stătea în corpul componentei; scoasă ca funcție pură, este testabilă fără Studio |
| `Theme.bannerPad(close: boolean?) -> number` | retragerea din dreapta a corpului unui banner: 12 normal, `12 + 28 + 4 = 44` când bannerul are butonul `×`, ca textul să nu treacă pe sub el (§7.9) |

### 8.9 Migrare 0.8 → 1.0 (pentru fazele 5–6)

| 0.8 | 1.0 |
|---|---|
| `Theme.background` / `panel` / `border` / `muted` / `text` | `Theme.paint(x, { BackgroundColor3 = "bg" \| "surface" })`, `"border"`, `"text2"`, `"text"` |
| `Theme.accent` (text) / `Theme.amber` / `Theme.error` | `"primaryText"` / `"warningText"` / `"dangerText"` (text) și `"…Mark"` (șine, puncte) |
| `Theme.font` | `Theme.fonts.body` (corp), `Theme.fonts.ui` (titluri, butoane), `Theme.fonts.mono` (id-uri) |
| `Theme.label(parent, text, height, size, color)` | `Theme.label(parent, text, { height = height, size = size, token = "text2" })` |
| `Theme.button(parent, text, height, true)` | `Theme.button(parent, text, { kind = "primary", size = "lg" })` |
| `Theme.button(parent, text, height)` | `Theme.button(parent, text)` (secundar) |
| `Theme.input(parent, placeholder, height, multiline)` | `Theme.input(parent, { placeholder = … })` / `Theme.textarea(parent, { placeholder = … })` |
| `Theme.stateColor(window)` | `local _, label, role = Theme.sessionState(window)`; `card.setRole(role)`; `Theme.recolor(chip, role)` |
| `Theme.authorColor(kind)` | `Theme.messageRole(kind, provider)` + `Theme.bubble` |
| `Theme.frame(parent, name, Theme.panel)` | `Theme.frame(parent, name, "surface")` |

Fazele 5 (Theme) și 6 (view-uri) schimbă API-ul împreună; faza 7B verifică compilarea și rularea pe bundle-ul complet.

### 8.10 `Theme.spec.luau` (faza 5A)

Rulat ca `SessionStore.spec.luau`: modulul și spec-ul înfășurate într-un singur fișier temporar, `spec(Theme)` întoarce numărul de teste trecute. Testele minime:

1. `PALETTES.dark` și `PALETTES.light` au exact cheile din §2.5, toate hex de 7 caractere.
2. Contrast: pentru fiecare mod, `text` și `text2` pe `bg`/`surface`/`raised` ≥ 4,5; `<rol>Text` pe `surface`, `raised` și `<rol>Soft` ≥ 4,5 pentru toate rolurile; `<rol>Mark`, `completedMark`, `neutralMark` și `borderInput` pe `surface` ≥ 3; `onPrimary` pe `primary`, `primaryHover`, `primaryActive` ≥ 4,5; `focus` pe `surface` ≥ 3.
3. `resolveMode`: `"Dark"`, `"dark"`, `"Light"`, `nil`, `"Altceva"`.
4. `elapsed` cu `now` fix: 0, 59, 60, 3599, 3600, 2 zile, `nil`, negativ.
5. `initials`: „ellob” → „EL”, „Ana Maria” → „AM”, „ș” → „Ș”, „” → „?”.
6. `shortId`, `avatarUrl` (0, nil, 12345, size 100), `fit` (limită sub un caracter multi-octet), `countLabel` (1, 3, 20, 21).
7. `sessionState` pentru fiecare cheie din `Theme.states`, plus aprobări în așteptare, `cancelling`, `releasing`, stare necunoscută.
8. `roleTokens` pentru fiecare rol din `Theme.ROLES` întoarce chei existente în paletă; `messageRole`, `placeLine`, `hubLine` pe cazurile din §8.3.
9. Geometria textului: `buttonText` (lățime „auto”, lățime fixă mare, lățime fixă sub 96 px, valoare de alt tip) și `bannerPad` (cu și fără `×`).
10. Modulul se încarcă în `luau.exe` fără globale Roblox (testul există prin simplul fapt că spec-ul rulează).

---

## 9. CSS — structura foii de stil din `panel/index.html`

Un singur `<style>` în `<head>`, în această ordine de blocuri (specificitatea crește de sus în jos; niciun `!important`; selectori de clasă, fără selectori de tip pentru componente, ca `.section` și `button` să nu se anuleze reciproc):

1. token-uri (`:root`, dark) — §9.1
2. maparea rolurilor — §9.2
3. reset și bază (`*`, `html`, `body`, titluri, `code`, `a`, focus) — §9.3
4. componente (§7), în ordinea catalogului
5. layout (`.shell`, `.topbar`, `.sidebar`, `.content`, `.auth`) — §9.4
6. responsive — §9.5
7. `prefers-reduced-motion` — §9.6

### 9.1 Token-uri

```css
:root {
  color-scheme: light;
  --bg: #F6F8FA; --surface: #FFFFFF; --raised: #F0F3F7;
  --border: #D6DCE5; --border-strong: #B8C2CF; --border-input: #7B8797;
  --text: #0F172A; --text-2: #5B6675; --text-3: #7B8797; --focus: #1D4ED8;
  --primary: #4ADE80; --primary-text: #166534; --primary-mark: #15803D; --primary-soft: #E6FAED;
  --primary-hover: #3ED474; --primary-active: #34C96A; --on-primary: #06210F; --completed-mark: #3F8F5E;
  --info: #60A5FA; --info-text: #1D4ED8; --info-mark: #2563EB; --info-soft: #E9F2FE;
  --warning: #FBBF24; --warning-text: #92400E; --warning-mark: #B45309; --warning-soft: #FEF6E0;
  --danger: #F87171; --danger-text: #B91C1C; --danger-mark: #DC2626; --danger-soft: #FEEBEB;
  --claude: #D97757; --claude-text: #9A4322; --claude-mark: #C4643F; --claude-soft: #FAECE7;
  --codex: #10A37F; --codex-text: #0B7A5F; --codex-mark: #0B7A5F; --codex-soft: #DEF2ED;
  --neutral: #8B98A9; --neutral-text: #5B6675; --neutral-mark: #6B7A8F; --neutral-soft: #EFF1F3;
  --overlay: rgba(15, 23, 42, .35);
  --shadow-1: 0 1px 2px rgba(15, 23, 42, .06); --shadow-2: 0 8px 24px rgba(15, 23, 42, .12);
  --primary-border: var(--primary-mark); --secondary-hover: var(--border);
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "SF Mono", Menlo, monospace;
  --radius-control: 6px; --radius-card: 10px;
  --duration: 140ms;
}
:root[data-theme="dark"] { /* blocul dark, identic cu cel din @media */ }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #0B0F14; --surface: #121821; --raised: #1A2230;
    --border: #26303F; --border-strong: #3A4657; --border-input: #66768C;
    --text: #E6EDF3; --text-2: #8B98A9; --text-3: #6B7A8F; --focus: #60A5FA;
    --primary-text: #4ADE80; --primary-mark: #4ADE80; --primary-soft: #1A342E;
    --primary-hover: #5CE58F; --primary-active: #6FEB9C; --completed-mark: #35895A;
    --info-text: #60A5FA; --info-mark: #60A5FA; --info-soft: #1D2C3F;
    --warning-text: #FBBF24; --warning-mark: #FBBF24; --warning-soft: #332F21;
    --danger-text: #F87171; --danger-mark: #F87171; --danger-soft: #32242C;
    --claude-text: #D97757; --claude-mark: #D97757; --claude-soft: #2E2529;
    --codex-text: #22B38C; --codex-mark: #10A37F; --codex-soft: #122B2E;
    --neutral-text: #8B98A9; --neutral-mark: #6B7A8F; --neutral-soft: #232A34;
    --overlay: rgba(11, 15, 20, .55);
    --shadow-1: none; --shadow-2: none;
    --primary-border: transparent; --secondary-hover: var(--bg);
  }
}
```

Regulile de temă: light este definit complet pe `:root`; dark redefinește doar ce se schimbă, o dată sub `@media (prefers-color-scheme: dark)` cu garda `:root:not([data-theme="light"])` și o dată sub `:root[data-theme="dark"]` (același bloc, copiat; comutatorul câștigă în ambele direcții). JS setează `document.documentElement.dataset.theme` din `localStorage["studioHarness.theme"]` ∈ {`light`, `dark`} sau îl șterge pentru `auto`. `<meta name="color-scheme" content="light dark">`. `body { background: var(--bg); color: var(--text); }` explicit.

### 9.2 Maparea rolurilor

```css
[data-role="primary"]   { --role-fill: var(--primary);  --role-text: var(--primary-text);  --role-mark: var(--primary-mark);  --role-soft: var(--primary-soft); }
[data-role="info"]      { --role-fill: var(--info);     --role-text: var(--info-text);     --role-mark: var(--info-mark);     --role-soft: var(--info-soft); }
[data-role="warning"]   { --role-fill: var(--warning);  --role-text: var(--warning-text);  --role-mark: var(--warning-mark);  --role-soft: var(--warning-soft); }
[data-role="danger"]    { --role-fill: var(--danger);   --role-text: var(--danger-text);   --role-mark: var(--danger-mark);   --role-soft: var(--danger-soft); }
[data-role="claude"]    { --role-fill: var(--claude);   --role-text: var(--claude-text);   --role-mark: var(--claude-mark);   --role-soft: var(--claude-soft); }
[data-role="codex"]     { --role-fill: var(--codex);    --role-text: var(--codex-text);    --role-mark: var(--codex-mark);    --role-soft: var(--codex-soft); }
[data-role="completed"] { --role-fill: var(--primary);  --role-text: var(--text-2);        --role-mark: var(--completed-mark); --role-soft: var(--neutral-soft); }
[data-role="neutral"], .chip, .dot, .card, .banner, .bubble
                        { --role-fill: var(--neutral);  --role-text: var(--neutral-text);  --role-mark: var(--neutral-mark);  --role-soft: var(--neutral-soft); }
```

Ultimul selector dă implicitul `neutral` componentelor fără `data-role`; blocurile cu `data-role` vin **după** el în fișier, ca să câștige la specificitate egală. Componentele folosesc apoi doar `var(--role-*)`: `.chip { background: var(--role-soft); color: var(--role-text); }`, `.card__rail, .banner__rail, .bubble__rail { background: var(--role-mark); }`, `.dot { background: var(--role-mark); }`. JS: `el.dataset.role = ROLE_OF_STATE[state] || "neutral"`.

```js
const ROLE_OF_STATE = { draft: "neutral", sending: "info", uncertain: "warning", queued: "info", running: "primary",
  waiting_approval: "warning", completed: "completed", failed: "danger", cancelled: "neutral", lost: "danger" };
const ROLE_OF_DEVICE = { pending: "warning", approved: "primary", revoked: "danger" };
const LABEL_OF_STATE = { draft: "Draft", sending: "Se trimite", uncertain: "Trimitere neconfirmată", queued: "În coadă",
  running: "În lucru", waiting_approval: "Cere aprobare", completed: "Finalizat", failed: "Eroare", cancelled: "Oprit", lost: "Job indisponibil" };
```

### 9.3 Bază

```css
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--text); font: 400 14px/1.45 var(--sans);
       font-variant-numeric: tabular-nums; min-height: 100vh; }
h1, h2, h3, p { margin: 0; }
h1 { font-size: 20px; line-height: 26px; font-weight: 600; }
h2 { font-size: 16px; line-height: 22px; font-weight: 600; }
code, .mono { font-family: var(--mono); font-size: 12px; }
a { color: var(--info-text); }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; border-radius: var(--radius-control); }
.input:focus-visible, .textarea:focus-visible { outline: none; border-color: var(--focus); box-shadow: 0 0 0 1px var(--focus); }
[hidden] { display: none !important; }   /* singurul !important: starea `hidden` nu se negociază */
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
```

### 9.4 Layout

```css
.shell { display: grid; grid-template-rows: auto auto 1fr; min-height: 100vh; }   /* topbar, notices, body */
.topbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; min-height: 52px;
          padding-block: 6px; padding-inline: 16px; min-width: 0;
          background: var(--surface); border-bottom: 1px solid var(--border); }
.topbar__name { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topbar__spacer { flex: 1; }
.body { display: grid; grid-template-columns: 1fr; min-width: 0; grid-row: 3; }  /* rândul 1fr și când .notices e gol */
.sidebar { display: none; }                          /* apare de la 900 px */
.content { padding: 16px; max-width: 960px; width: 100%; }
.content__header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.auth { min-height: 100vh; display: grid; place-items: center; padding: 16px; }
.auth__card { width: min(400px, 100%); padding: 24px; }
```

Cardul de login/pending folosește `.card` (fără șină, `data-role` absent, `.card--static`). Bara laterală este `<nav aria-label="Workspace-uri">`; conținutul este `<main>`. Bara de sus se poate înfășura pe două rânduri (de aceea `min-height`, nu o înălțime fixă), iar bannerele stau în `.notices`, pe rândul 2 al grilei.

### 9.5 Responsive

| Interval | Reguli |
|---|---|
| ≤ 560 px | `.topbar__name { max-width: 96px; }`; din JS se scurtează eticheta temei („Auto” / „Luminos” / „Întunecat”) și badge-ul de așteptare (rămâne doar numărul), textul complet rămânând în `aria-label` |
| ≤ 400 px | `.content { padding: 12px 16px; }`, `.button { min-height: 40px; }` și `.button--sm { min-height: 32px; }` (țintă tactilă), `.card__head { flex-wrap: wrap; }` cu `.card__meta` pe rândul 2, `.row { flex-wrap: wrap; }` cu `.row__secondary { flex-basis: 100%; text-align: left; }` (`--two-line` automat) și `.row__action { flex-basis: 100%; justify-content: flex-end; }`, `.tabs` ocupă toată lățimea cu filele egale (`flex: 1`, `padding-inline: 6px`), `.topbar { gap: 8px; padding-inline: 12px; }`, `.topbar__name { display: none; }`, `.wordmark { font-size: 15px; }`; rândul de grupă trece pe două linii: `.group-row .row__primary { flex-basis: 100%; }`, `.group-row .row__secondary { flex-basis: auto; }`, `.group-row .bar { width: calc(var(--bar, 0) * 160px); max-width: 160px; }` |
| 401–560 px | `.group-row .row__primary { flex-basis: 120px; }` și `.group-row .bar { width: calc(var(--bar, 0) * 120px); max-width: 120px; }`: peste 400 px rândul de grupă redevine o singură linie, iar eticheta de 180 px plus bara de 240 px nu ar încăpea — fără această treaptă fila Proiect derulează orizontal, contrar propoziției de mai jos |
| 401–899 px | ca mai sus, fără forțarea celor două linii; `.content { max-width: 720px; margin-inline: auto; }` |
| ≥ 900 px | `.body { grid-template-columns: 240px 1fr; }`, `.sidebar { display: block; border-right: 1px solid var(--border); background: var(--surface); padding: 16px 12px; }`, `.select--workspace { display: none; }`, `.content { padding: 24px; }` |
| ≥ 1400 px | fila Proiect: `.project { grid-template-columns: 360px 1fr; }` (grupele stânga, detaliul dreapta), cu `.group-row .row__primary { flex-basis: 140px; }` și `.group-row .bar { … * 100px; max-width: 100px; }` pentru coloana îngustă; fără grupă selectată și fără căutare, detaliul lipsește, deci `.project--single { grid-template-columns: 1fr; }` redă listei toată lățimea (`.row__primary` 180 px, bara 240 px), în loc să lase jumătate de ecran gol |

Hintul unui control segmentat primește `.segmented + .field__hint { margin-top: 8px; }`. Nicio lățime nu produce scroll orizontal: `img, svg { max-width: 100%; }`, `.bubble__body, .row__primary { overflow-wrap: anywhere; }`, iar singurele elemente cu `overflow-x: auto` sunt blocul de argumente al unei aprobări și lista de intrări din Proiect când căile depășesc coloana.

### 9.6 Mișcare

```css
.button, .card, .row--clickable, .tab, .segmented__item, .input, .banner, .select
  { transition: background-color var(--duration), border-color var(--duration), color var(--duration), opacity var(--duration); }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; animation: none !important; } }
```

Acesta este al doilea și ultimul `!important` (reducerea mișcării trebuie să câștige peste orice).

### 9.7 Lista claselor (referință rapidă)

`card card--clickable card--open card--selected card--static card__rail card__title card__meta card__chips card__owner card__body card__toggle card__head card__state card__actions` · `chip chip--count class-chips` · `dot` · `avatar avatar--16 avatar--24 avatar--32 avatar--40 avatar--ring avatar--offline avatar__img avatar__initials avatar-row avatar-row__names avatar-row__more avatar-row__stack` · `button button--primary button--secondary button--ghost button--danger button--sm button--block` · `segmented segmented__item` · `row row--two-line row--mono row--clickable row--selected row__lead row__primary row__secondary row__action row__stack row__sub row__confirm` · `empty entries list-more` · `banner banner--leaving banner__rail banner__text banner__close notices` · `section section__header section__title section__count section__subtitle section__body section__body--rows` · `tabs tab tab__badge tabpanel` · `field field__label field__hint field__error input input--mono select select--workspace` · `flow flow__end bubble bubble--user bubble--assistant bubble--tool bubble--claim bubble--approval bubble--error bubble--notice bubble__author bubble__body bubble__rail` · `status-line kv group-row ws-card__line ws-card__count project--single` · `wordmark mono muted small sr-only` · `shell topbar topbar__spacer topbar__name topbar__me body sidebar sidebar__title sidebar__list sidebar__foot content content__header content__place auth auth__card auth__title auth__text auth__foot project project__meta project__search bar`.

Scoase față de schița inițială (reguli moarte, panoul nu le mai are): `dot--lg`, `avatar--28`, `button--lg`, `empty__text`, `empty__hint`, `empty__action`, `banner__action`, `section--collapsible`, `section--open`, `textarea`, `args`.

Singura clasă fără nicio regulă de stil este `js-theme`, marcajul celor trei butoane de temă (login, pending, bara de sus), pe care JS-ul le ține sincronizate. Prefixul `js-` este rezervat exclusiv pentru astfel de cârlige de comportament: nu apare niciodată într-un selector din foaia de stil.

Nimic altceva nu primește clasă nouă fără să fie adăugat aici și în §7.

---

## 10. Wireframe-uri — pluginul Studio

Dock widget „Studio Harness” (`DockWidgetPluginGuiInfo`: stânga, lățime minimă 320, înălțime minimă 480), un singur `ScrollingFrame` vertical cu padding 16 lateral și 12 sus, `UIListLayout` cu pas 24 între secțiuni. Toate cutiile de mai jos sunt desenate la 320 px (44 de coloane ≈ 1 coloană per 7 px). Ordinea secțiunilor este cea din brief §5.3 și nu se schimbă în funcție de stare; secțiunile fără conținut arată starea goală, nu dispar (excepții: Banner și Aprobare, care apar doar când există ceva).

### 10.1 HubView — anatomia completă (hub `approved`, identitate cunoscută)

```text
┌────────────────────────────────────────────┐
│ (el) ellob ●                 v1.0.0 [Panou]│
│                                            │
│ Ball                                       │
│ place 1291603 · al tău                     │
│ (tu(an(mi tu, ana, mihai · 3 prezenți      │
│ Alte workspace-uri ▾                       │
│                                            │
│ ┌────────────────────┬───────────────────┐ │
│ │    Claude Code     │       Codex       │ │
│ └────────────────────┴───────────────────┘ │
│ ┌────────────────────────────────────────┐ │
│ │              Sesiune nouă              │ │
│ └────────────────────────────────────────┘ │
│ ● Claude Code · CLI găsit  ○ Codex · lipsă │
│                                            │
│ Sesiuni                                (2) │
│ Ale tale                                   │
│ ┌▌───────────────────────────────────────┐ │
│ │▌ Lumini mai calde în Zone3     de 4 min│ │
│ │▌ [Claude] [Studio] (tu) tu · În lucru  │ │
│ └────────────────────────────────────────┘ │
│ În workspace                               │
│ ┌▌───────────────────────────────────────┐ │
│ │▌ Refactor NPC                 de 12 min│ │
│ │▌ [Codex] [Terminal] (an) ana · În coadă│ │
│ └────────────────────────────────────────┘ │
│                                            │
│ Aprobare                               (1) │
│ ┌▌───────────────────────────────────────┐ │
│ │▌ multi_edit                            │ │
│ │▌ ServerScriptService.Main · 2 edituri  │ │
│ │▌ Lumini mai calde în Zone3 · Claude    │ │
│ │▌ [      Permite      ]  [   Refuză   ] │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ Claims                                 (2) │
│ Workspace.Map.Zone3        tu · de 4 min   │
│                              [Eliberează]  │
│ StarterGui.Shop           ana · de 1 min   │
│                                            │
│ Jurnal                                     │
│ 14:02  ana · multi_edit                    │
│        StarterGui.Shop                     │
│ 13:58  tu · execute_luau                   │
│        Workspace.Map.Zone3                 │
├────────────────────────────────────────────┤
│ Daemon · 127.0.0.1:34871 · la zi           │
│ Avansat ▸                                  │
└────────────────────────────────────────────┘
```

Legendă: `(el)` avatar 28 px, `(tu(an(mi` avatare de 24 px suprapuse, `●`/`○` puncte de stare cu etichetă, `[Claude]` chip, `(2)` contorul secțiunii (chip `--count`), `▌` șina de stare a cardului, `[Panou]` buton ghost mic.

Rând cu rând:

1. **Antet** (înălțime 44, fără fundal), pe **două linii**: linia 1 (28 px) = `Theme.avatar` 28 + numele Roblox 14/ui + `Theme.dot` hub (10 px, rol din `Theme.HUB_ROLE`); linia 2 (14 px, aliniată la 36 px, sub nume) = eticheta scurtă din `Theme.hubStates`, `text2` 12, vizibilă **doar** când starea nu este `approved`. La dreapta, ancorat pe verticală: „v1.0.0” 12 `text2` + buton ghost sm „Panou”. Un singur rând nu ține la 320 px avatar 28 + nume + punct + etichetă + versiune + buton, iar în Studio nu există tooltip-uri (§14.3).

   „Panou” este dezactivat **numai** când daemon-ul nu este conectat / nu este compatibil sau hub-ul este `disabled`; în `pending`, `offline` și `revoked` rămâne activ, pentru că brief-ul §2.5 cere ca un dispozitiv `pending` să poată deschide panoul ca să vadă ecranul „Așteaptă aprobarea”.
2. **Banner** (numai când este cazul; vezi §10.2): între antet și workspace.
3. **Workspace**: numele 16/600; `Theme.placeLine` 12 mono `text2`; `Theme.avatarRow` 24 px; buton ghost sm „Alte workspace-uri ▾” (ascuns când `#store.workspaces <= 1`).
4. **Acțiune principală**: `Theme.segmented` (Claude Code | Codex, valoarea `store.defaultProvider`) + `Theme.button` primar lg „Sesiune nouă” (dezactivat când daemon-ul nu e conectat sau CLI-ul providerului lipsește; textul rămâne, motivul apare în rândul de dedesubt) + **două** rânduri de 16 px (pas 4), fiecare cu un punct de 8 px și o etichetă 12 `text2`: „Claude Code · CLI găsit”, „Codex · CLI negăsit”. Cele două etichete normative din §6.4 însumează ~250 px și nu încap pe un singur rând în cei 288 px de conținut ai dock-ului la 320 px. Sub ele, hintul cu motivul pentru care „Sesiune nouă” este dezactivat (vizibil doar când există unul).
5. **Sesiuni**: `Theme.section` cu contor = total; subtitlul „Ale tale” (ferestrele locale + sesiunile din terminal ale acestui dispozitiv) și, dedesubt, „În workspace” (sesiunile altora din workspace-ul curent, read-only). Fiecare `Theme.card` clicabil deschide `SessionView` (`store:restore(id)`). Sesiunile minimizate au chip `neutral` „Minimizată”.
6. **Aprobare**: apare doar când o fereastră proprie are `#approvals > 0`; un card `warning` per fereastră (prima aprobare a ei): tool 14/ui mono, argumentele scurtate (`Theme.fit`, 80) 12 mono `text2`, numele sesiunii · provider 12 `text2`, butoane „Permite” (primar) / „Refuză” (secundar) pe același rând (60 % / 40 %). „Permite” este dezactivat până când `ApprovalView` din fereastra sesiunii a putut afișa argumentele complete (`inspectable`), cu hint „Deschide sesiunea pentru argumentele complete.” — regula 0.8 rămâne: nu se aprobă ce nu s-a putut citi integral.
7. **Claims**: `Theme.row` cu `mono = true`, `lines = 2` când există `reason`; meta = deținător · timp; `action` „Eliberează” (ghost sm) doar pe claims-urile proprii (`store:ownsClaim`).
8. **Jurnal**: ultimele 10 intrări ale workspace-ului, `Theme.row` `lines = 2`: „14:02  ana · multi_edit” / căile (mono 12, `text2`, primele 2 căi + „+N”).
9. **Subsol** (bordură sus `border-strong`, padding 12 0): linia daemon-ului 12 `text2` (§6.2) + `Theme.section` pliabil „Avansat”.

### 10.2 Variante de antet și banner

Hub `pending` (dispozitiv nou, așteaptă aprobarea):

```text
┌────────────────────────────────────────────┐
│ (el) ellob ● Așteaptă aprob. v1.0.0 [Panou]│
│                                            │
│ ┌▌───────────────────────────────────────┐ │
│ │▌ Așteaptă aprobarea adminului ·        │ │
│ │▌ dispozitiv ab12cd34                   │ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

Hub `offline` (hub-ul nu răspunde; daemon-ul reîncearcă singur):

```text
┌────────────────────────────────────────────┐
│ (el) ellob ● Hub offline     v1.0.0 [Panou]│
│                                            │
│ ┌▌───────────────────────────────────────┐ │
│ │▌ Hub offline: timeout după 5 s.        │ │
│ │▌ Sesiunile tale merg local; claims-ul  │ │
│ │▌ e doar local până revine.             │ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

Actualizare aplicată (dispare după 5 s):

```text
┌────────────────────────────────────────────┐
│ ┌▌───────────────────────────────────────┐ │
│ │▌ Actualizare aplicată: 1.0.1. Interfața│ │
│ │▌ s-a reîncărcat fără repornire.       ×│ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

Identitate necunoscută (`user_id = 0`): avatarul arată „?”, numele este „Studio”, iar sub antet nu apare banner (nu e o eroare). Daemon neconectat: antetul rămâne, secțiunile Workspace/Sesiuni/Claims/Jurnal arată starea goală „Daemon-ul nu răspunde” (§12.1), butonul „Sesiune nouă” e dezactivat.

### 10.3 „Alte workspace-uri” deschis și vizualizarea altui workspace

```text
┌────────────────────────────────────────────┐
│ Ball                                       │
│ place 1291603 · al tău                     │
│ (tu(an(mi tu, ana, mihai · 3 prezenți      │
│ Alte workspace-uri ▾                       │
│ ┌────────────────────────────────────────┐ │
│ │ Ball (curent)       3 prezenți · 2 ses.│ │
│ │ Arena                1 prezent · 1 ses.│ │
│ │ Lobby v2            0 prezenți · 0 ses.│ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

Lista este o `Theme.section` pliabilă (`open = true`); fiecare rând este un `Theme.row` clicabil (`lead = "dot"` cu rolul `primary` când `membersOnline > 0`, altfel `neutral`). Alegerea unui rând setează `store.viewWorkspace` și secțiunea Workspace devine vizualizare read-only:

```text
┌────────────────────────────────────────────┐
│ ← Înapoi la Ball                           │
│ Arena                    [doar de citit]   │
│ place 88120044 · grup 555                  │
│ (mi mihai · 1 prezent                      │
│                                            │
│ ┌──────────┬───────────┐                   │
│ │ Prezenți │  Sesiuni  │                   │
│ └──────────┴───────────┘                   │
│ ┌▌───────────────────────────────────────┐ │
│ │▌ Terrain Arena                de 30 min│ │
│ │▌ [Claude] [Studio] (mi) mihai · Lucru  │ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

Datele vin din `GET /v1/hub/workspace?key=` prin `BridgeController` (`store.viewWorkspace`, `controller:viewWorkspace(key)`; `nil` sau cheia curentă = înapoi), la 5 s cât timp vizualizarea e deschisă; deschiderea listei cheamă `controller:refreshWorkspaces()`. Când hub-ul nu este aprobat sau cererea eșuează, în locul prezenților și al sesiunilor apare textul dat de `controller:viewError()` (starea goală a vizualizării). Nu există acțiuni pe sesiunile altui workspace; secțiunile Acțiune principală, Aprobare și Claims proprii rămân pentru workspace-ul curent, sub vizualizare. Chip-ul „doar de citit” este `neutral`.

### 10.4 „Avansat” deschis

```text
┌────────────────────────────────────────────┐
│ Daemon · 127.0.0.1:34871 · la zi           │
│ Avansat ▾                                  │
│ ┌────────────────────────────────────────┐ │
│ │  Cod de asociere (doar dacă lipsește)  │ │
│ │ ┌────────────────────────────────────┐ │ │
│ │ │ Lipește codul din daemon.log       │ │ │
│ │ └────────────────────────────────────┘ │ │
│ │  Apasă Enter ca să conectezi daemon-ul │ │
│ │  Studio țintă                          │ │
│ │ ┌────────────────────────────────────┐ │ │
│ │ │          Automat: Ball             │ │ │
│ │ └────────────────────────────────────┘ │ │
│ │  Dispozitiv                            │ │
│ │ ┌────────────────────────────────────┐ │ │
│ │ │ ab12cd34ef567890                   │ │ │
│ │ └────────────────────────────────────┘ │ │
│ │ ┌────────────────────────────────────┐ │ │
│ │ │       Deconectează daemon-ul       │ │ │
│ │ └────────────────────────────────────┘ │ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

Câmpul de cod apare **doar** când `controller:needsToken()` este adevărat — nu există cod valid în setări **sau** daemon-ul l-a
refuzat cu 401. Criteriul este puțin mai larg decât „loader-ul nu a livrat `LocalToken`”: acoperă și cazul unui cod expirat
sau al unei instalări cu alt `--state-dir`. Codul se aplică cu **Enter** (`FocusLost` cu `enterPressed`), iar sub câmp stă
hintul „Apasă Enter ca să conectezi daemon-ul cu codul lipit.”, prefixat cu motivul refuzului când `controller:tokenSource()`
spune de unde venea codul respins.

„Studio țintă” este un buton secundar care ciclează automat → instanța 1 → … → automat: „Automat: <nume>” cu o singură
instanță, „Automat · N instanțe” cu mai multe, „Studio țintă: <nume>” după o alegere manuală și „Niciun Studio conectat” când
lista e goală. Sub el, „Dispozitiv” arată `device_id`-ul complet într-un `TextBox` needitabil, mono, de copiat (cerut de §14.3);
textul nu se rescrie cât timp câmpul este focusat.

„Deconectează daemon-ul” este `danger` și cere confirmare inline: un banner `danger` în corpul secțiunii, cu butoanele
„Deconectează” (danger) / „Înapoi”. După deconectarea manuală, în locul lui apare butonul secundar „Conectează daemon-ul”
(`controller:connect("")`), pentru că oprirea manuală suspendă reconectarea automată.

### 10.5 Stări goale în HubView

```text
┌────────────────────────────────────────────┐
│ Sesiuni                                    │
│ Ale tale                                   │
│ Nicio sesiune încă. Alege providerul și    │
│ apasă „Sesiune nouă”, sau pornește CLI-ul  │
│ din terminal cu pluginul activ.            │
│ În workspace                               │
│ Nimeni altcineva nu lucrează acum în Ball. │
│                                            │
│ Claims                                     │
│ Nicio zonă revendicată. Agenții cer        │
│ hub_claim înainte de a modifica scena.     │
│                                            │
│ Jurnal                                     │
│ Nicio modificare înregistrată încă în      │
│ acest workspace.                           │
└────────────────────────────────────────────┘
```

### 10.6 SessionView — sesiune Studio (Mod S)

Fereastră nativă flotantă (`DockWidgetPluginGuiInfo`: Float, 480 × 740, minim 380 × 640). Antet fix, flux derulabil, composer fix jos. La 480 px cutia are 66 de coloane.

```text
┌──────────────────────────────────────────────────────────────────┐
│ Lumini mai calde în Zone3                          [Nou] [_] [×] │
│ [Claude Code] [În lucru] (tu) tu · Ball / Edit                   │
│ Claims: Workspace.Map.Zone3                                      │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                            ┌──────────────────────────────────┐  │
│                            │ Fă luminile din Zone3 mai calde, │  │
│                            │ fără să atingi Terrain.          │  │
│                            └──────────────────────────────────┘  │
│                                                                  │
│ Claude Code                                                      │
│ Am citit Lighting și cele trei surse din Zone3. Propun să        │
│ scad Brightness la 1.8 și să mut ColorShift_Top spre             │
│ chihlimbar.                                                      │
│ ▌ get_studio_state · 0,3 s                                       │
│ ▌ Claim acordat: Workspace.Map.Zone3                             │
│ ▌ Propunere: multi_edit cere aprobarea ta.                       │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ Scrie următorul pas…                                         │ │
│ │                                                              │ │
│ │                                                              │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────┐ ┌──────────────────────┐  │
│ │              Trimite               │ │       Oprește        │  │
│ └────────────────────────────────────┘ └──────────────────────┘  │
│ Doar această sesiune. Modificările cer claim și aprobare.        │
└──────────────────────────────────────────────────────────────────┘
```

- **Antet** (înălțime 88 + 24 pentru claims): titlul este un `Theme.input` 20/600 fără bordură până la hover (redenumire inline, `TextEditable = not terminal`); butoane ghost sm „Nou” (fereastră nouă cu același provider), „_” (minimizează), „×” (închide, cu confirmare inline). Rândul 2: chip provider (rol `claude`/`codex`), chip stare (rol din `Theme.sessionState`), avatar 16 + proprietar, `·` ținta — „<nume Studio> / Edit” când este legată, „țintă aleasă de daemon” pentru o sesiune din terminal fără instanță fixată, numele mașinii (sau „altă mașină”) pentru sesiunea altui developer. Butonul ghost „Țintă nealeasă” apare **doar** în sesiunile proprii din Studio, cât timp `window.studioId` este nil, și dispare după legare; schimbarea ulterioară a țintei rămâne în „Avansat” (§10.4, §12.3). Rândul 3: „Claims: …” 12 mono `text2` (sau „Fără claims. Modificările cer hub_claim.”).
- **Flux** (`Theme.scroll` + `Theme.list(12)`): `Theme.bubble` per mesaj, `set(message)` la update, fără recreare; urmărirea automată a capătului rămâne ca în 0.8 (`followChat`).
- **Composer** (`surface`, bordură sus): `Theme.textarea` 72 + rând de butoane: „Trimite” primar (60 %) / „Oprește” secundar (40 %); hint 12 `text2` (textele din §12.3). Când `request.state == "uncertain"`, „Trimite” devine „Reia aceeași cerere” și cere confirmarea inline din 0.8 într-un banner `warning` cu „Reia aceeași cerere” primar / „Înapoi” secundar.
- **Confirmare de închidere**: înlocuiește composer-ul cu un `Theme.banner` `warning` + două butoane („Anulează și închide” danger / „Înapoi” secundar). Textele 0.8 rămân.

### 10.7 SessionView — sesiune din terminal (Mod T) și sesiunea altui developer

```text
┌──────────────────────────────────────────────────────────────────┐
│ Terminal B                                              [_] [×]  │
│ [Codex] [Terminal] [În lucru] (tu) tu · Ball / Edit              │
│ Claims: Workspace.Map.Zone3, StarterGui.Shop                     │
├──────────────────────────────────────────────────────────────────┤
│ Tu                                                               │
│ Repară Shop-ul: butonul Cumpără nu răspunde.                     │
│                                                                  │
│ Codex                                                            │
│ Verific StarterGui.Shop.Buy și scriptul lui client…              │
│ ▌ script_read · StarterGui.Shop.Buy.LocalScript                  │
│ ▌ Claim acordat: StarterGui.Shop                                 │
├──────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────┐ ┌────────────────────────────┐  │
│ │           Oprește            │ │     Eliberează claims      │  │
│ └──────────────────────────────┘ └────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

Terminal: fără composer și fără „Nou”; titlul nu e editabil; „Oprește” secundar, „Eliberează claims” secundar (dezactivat fără claims). Sesiunea altui developer (`window.mine == false`): antetul primește chip `neutral` „doar de citit” între chip-uri, meta devine „ana · PC-ANA” (numele mașinii proprietarului, sau „altă mașină” când lipsește), nu există butoane în subsol, aprobările apar în flux ca bule `approval` („multi_edit așteaptă aprobarea lui ana.”), iar `ApprovalView` nu se afișează.

### 10.8 ApprovalView

Cardul de aprobare din fereastra sesiunii (rol `warning`, șină, `surface`). Sub 820 px lățime stă sub flux (30 % flux / 70 % aprobare); peste, alături (50/50), ca în 0.8.

```text
┌▌─────────────────────────────────────────────────────────────┐
│▌ Modificarea propusă                     1 din 2 în așteptare│
│▌ multi_edit · doar acest apel                                │
│▌                                                             │
│▌ ┌──────────────────────────────────────────────────────────┐│
│▌ │ Cod Luau:                                                ││
│▌ │ local lighting = game:GetService("Lighting")             ││
│▌ │ lighting.Brightness = 1.8                                ││
│▌ │                                                          ││
│▌ │ Instrument: multi_edit                                   ││
│▌ │                                                          ││
│▌ │ Argumente complete:                                      ││
│▌ │ {                                                        ││
│▌ │   "file_path": "ServerScriptService.Main",               ││
│▌ │   "edits": [ … ]                                         ││
│▌ │ }                                                        ││
│▌ └──────────────────────────────────────────────────────────┘│
│▌ Cod integral. Derulează înainte de decizie.                 │
│▌ ┌────────────────────────────────────┐ ┌───────────────────┐│
│▌ │              Permite               │ │       Refuză      ││
│▌ └────────────────────────────────────┘ └───────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

- Titlul 16/600 `warningText`; meta dreapta 12 `text2` („1 din 2 în așteptare” când `#approvals > 1`).
- Detaliul: tool în mono 14 + „doar acest apel” 12 `text2`.
- Argumentele: `Theme.scroll` cu fundal `bg` (dark) / `raised` (light), bordură `border`, `TextBox` mono 13 needitabil, `RichText = false`; formatarea JSON indentată din 0.8 rămâne. Regulile `inspectable`/`measure` din 0.8 rămân neschimbate: „Permite” este activ doar când textul complet a încăput în TextBox.
- Hint 12 `text2`: „Cod integral. Derulează înainte de decizie.” / „Afișare incompletă: numai Refuză.”
- Butoane: „Permite” primar (60 %) / „Refuză” secundar (40 %); când apelul nu mai e activ: titlul „Apelul nu mai este activ”, „Refuză” devine „Închide”.
- Cardul din HubView (§10.1 rândul 6) este forma compactă a aceluiași obiect, cu aceleași etichete; decizia luată într-un loc dispare din amândouă.

---

## 11. Wireframe-uri — panoul web

Un singur fișier, fără CDN, CSP neschimbat. Trei ecrane de acces (login, pending, shell) și trei file. Lățimi de referință: 400 px (48 de coloane) și desktop ≥ 1100 px (100 de coloane). Între 401 și 899 px, layout-ul este cel îngust cu coloană mai lată (conținutul are `max-width: 720px`); de la 900 px apare bara laterală.

### 11.1 Login

```text
┌────────────────────────────────────────────────┐
│                                                │
│                                                │
│      ┌──────────────────────────────────┐      │
│      │ ◎ Studio Harness                 │      │
│      │                                  │      │
│      │ Deschide panoul din pluginul     │      │
│      │ Studio Harness (butonul „Panou”).│      │
│      │ Se autentifică singur.           │      │
│      │                                  │      │
│      │ sau lipește codul                │      │
│      │ ┌──────────────────────────────┐ │      │
│      │ │ cod de dispozitiv sau admin  │ │      │
│      │ └──────────────────────────────┘ │      │
│      │ ┌──────────────────────────────┐ │      │
│      │ │            Intră             │ │      │
│      │ └──────────────────────────────┘ │      │
│      │ Codul rămâne doar în acest       │      │
│      │ browser.                         │      │
│      └──────────────────────────────────┘      │
│                                                │
│      Temă: Auto                                │
└────────────────────────────────────────────────┘
```

La desktop, același card (lățime 400, centrat vertical și orizontal pe `bg`). `◎` este marca (`.wordmark`). Câmpul: `.input.input--mono` cu `autocomplete="off"`, `spellcheck="false"`; butonul „Intră” primar `--block`; la cod refuzat apare `.field__error` sub câmp: „Codul nu a fost acceptat. Verifică-l sau deschide panoul din plugin (butonul Panou).”; la cod revocat: „Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou.”. Cu `#device=` în URL, ecranul nu se vede: panoul salvează codul, curăță URL-ul și trece direct la pending/shell.

### 11.2 Pending

```text
┌────────────────────────────────────────────────┐
│                                                │
│      ┌──────────────────────────────────┐      │
│      │ ◎ Studio Harness                 │      │
│      │                                  │      │
│      │ (el)  Dispozitivul tău așteaptă  │      │
│      │       aprobarea                  │      │
│      │                                  │      │
│      │ Un admin îl aprobă din fila      │      │
│      │ Dispozitive. Pagina se           │      │
│      │ actualizează singură.            │      │
│      │                                  │      │
│      │ Dispozitiv   ab12cd34            │      │
│      │ Roblox       ellob               │      │
│      │ Mașină       PC-ELLOB            │      │
│      │                                  │      │
│      │ ● În așteptare · verific la 5 s  │      │
│      │                                  │      │
│      │ Folosește alt cod                │      │
│      └──────────────────────────────────┘      │
└────────────────────────────────────────────────┘
```

Punctul este `warning`; cele trei rânduri sunt `.row` cu primarul 12 `text-2` și secundarul mono (id) / text (nume, mașină). „Folosește alt cod” este buton ghost care șterge codul din `localStorage` și revine la login. Când dispozitivul devine `approved`, pagina trece la shell fără reîncărcare; când devine `revoked`, textul se schimbă în mesajul de revocare (`danger`) și rămâne butonul de alt cod.

### 11.3 Shell (desktop) — fila Activitate

```text
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ◎ Studio Harness                                  Temă: Auto   (el) ellob [admin]  [2 în așteptare]│
├────────────────────────────────────────────────────────────────────────────────────────────────────┤
│Workspace-uri             Ball                                    place 1291603 · al tău            │
│                          ┌───────────┬──────────┬──────────────┐                                   │
│┌▌────────────────────┐   │ Activitate│ Proiect  │ Dispozitive  │                                   │
││▌ Ball               │   └───────────┴──────────┴──────────────┘                                   │
││▌ (tu(an(mi 3 · 2 ses│                                                                             │
│└─────────────────────┘   Prezenți                                          3                       │
│ Arena                    (tu(an(mi  tu, ana, mihai                                                 │
│ (mi 1 · 1 ses                                                                                      │
│                          Sesiuni live                                      2                       │
│ Lobby v2                 ┌▌─────────────────────────────────────────────────────────┐              │
│ nimeni · 0 ses           │▌ Lumini mai calde în Zone3                de 4 min       │              │
│                          │▌ [Claude Code] [Studio] (tu) tu · În lucru               │              │
│                          └──────────────────────────────────────────────────────────┘              │
│                          ┌▌─────────────────────────────────────────────────────────┐              │
│                          │▌ Refactor NPC                            de 12 min       │              │
│                          │▌ [Codex] [Terminal] (an) ana · În coadă                  │              │
│                          └──────────────────────────────────────────────────────────┘              │
│                                                                                                    │
│                          Claims                                            2                       │
│                          Workspace.Map.Zone3               tu · de 4 min                           │
│                          StarterGui.Shop                  ana · de 1 min                           │
│                                                                                                    │
│                          Jurnal                                                                    │
│                          14:02  ana · multi_edit · StarterGui.Shop                                 │
│                          13:58  tu · execute_luau · Workspace.Map.Zone3                            │
│hub a1b2c3 · v1.0.0       13:41  mihai · insert_asset · Workspace.Arena                             │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Bara de sus** (`.topbar`, 52 px, `surface`, bordură jos): `.wordmark`; la dreapta butonul ghost „Temă: Auto” (ciclează Auto → Luminos → Întunecat, `aria-label="Temă: Auto"`), avatar 32 + nume + chip `neutral` „admin” când e cazul, chip `warning` „2 în așteptare” (buton: sare la fila Dispozitive) doar pentru admin și doar când `pending_devices > 0`.
- **Bara laterală** (`.sidebar`, 240 px, `surface`, bordură dreapta): titlul „Workspace-uri” 16/600; un `.card` per workspace (`--clickable`, șina `primary-mark` pe cel selectat, `neutral-mark` pe restul; rândul 2: `.avatar-row` 16 px + „3 · 2 ses” cu `title="3 prezenți, 2 sesiuni active"`); în subsol id-ul hub-ului scurt și versiunea, 12 `text-2` mono.
- **Conținut** (`.content`, padding 24, `max-width: 960px`): antet cu numele workspace-ului 20/600 și `placeLine` 12 mono `text-2` la dreapta; `.tabs` cu trei file; sub ele `.tabpanel`-ul activ.
- **`placeLine` are aceeași semnificație în ambele medii** (`Theme.placeLine`): „place 1291603” plus creatorul locului — „· al tău” doar când `creator_id` este contul tău Roblox, altfel „· grup 555” / „· utilizator 555”; fișierul nepublicat dă „Fișier nepublicat”. Prezența ta în workspace se spune separat, prin chip-ul `primary` „ești aici” de lângă linia mono (`#ws-here`), ca „al tău” să nu însemne două lucruri diferite.
- **Activitate**: patru `.section` în ordinea Prezenți (avatar-row 24 + nume; contorul stă în chip-ul secțiunii, §7.4), Sesiuni live (carduri, `--clickable`; click deschide fluxul inline, §11.7), Claims (rânduri mono), Jurnal (rânduri: oră mono · developer · tool · căi; ultimele 200, cu „Arată mai mult” la fiecare 50). Nu există butoane de acțiune pe sesiuni sau claims în panou (panoul e de citit, cu excepția filei Dispozitive și a modului de înrolare).
- **Claims și jurnal sunt mereu pe două linii** (`.row__stack` + `.row__sub`), și pe desktop: calea (sau tool-ul și căile) pe primul rând, deținătorul/autorul, motivul și expirarea pe al doilea. Pe o linie, o cale reală (`Workspace.Map.Zone3.Lamp1.PointLight`) plus motivul și expirarea se trunchiază în ambele capete; wireframe-ul de mai sus arată forma scurtă, nu o a doua variantă de layout.

### 11.4 Activitate la 400 px

```text
┌────────────────────────────────────────────────┐
│ ◎ Studio Harness        (el) [2 în aștept.]    │
├────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────┐   │
│ │ Ball · 3 prezenți · 2 sesiuni           ▾ │  │
│ └──────────────────────────────────────────┘   │
│ ┌───────────┬──────────┬────────────────────┐  │
│ │ Activitate│ Proiect  │ Dispozitive        │  │
│ └───────────┴──────────┴────────────────────┘  │
│                                                │
│ Prezenți                                   3   │
│ (tu(an(mi  tu, ana, mihai                      │
│                                                │
│ Sesiuni live                               2   │
│ ┌▌───────────────────────────────────────────┐ │
│ │▌ Lumini mai calde în Zone3     de 4 min    │ │
│ │▌ [Claude Code] [Studio]                    │ │
│ │▌ (tu) tu · În lucru                        │ │
│ └────────────────────────────────────────────┘ │
│ ┌▌───────────────────────────────────────────┐ │
│ │▌ Refactor NPC                 de 12 min    │ │
│ │▌ [Codex] [Terminal]                        │ │
│ │▌ (an) ana · În coadă                       │ │
│ └────────────────────────────────────────────┘ │
│                                                │
│ Claims                                     2   │
│ Workspace.Map.Zone3                            │
│                         tu · de 4 min          │
│ StarterGui.Shop                                │
│                        ana · de 1 min          │
│                                                │
│ Jurnal                                         │
│ 14:02  ana · multi_edit                        │
│        StarterGui.Shop                         │
└────────────────────────────────────────────────┘
```

La ≤ 899 px bara laterală devine `.select` sub bara de sus (textul opțiunii: „Ball · 3 prezenți · 2 sesiuni”); numele utilizatorului dispare din bara de sus (rămân avatarul și chip-ul de așteptare, scurtat); cardurile trec rândul 2 pe două linii. Rândurile de claims și jurnal sunt pe două linii la orice lățime (§11.3), deci aici nu se schimbă nimic la ele. Nimic nu derulează orizontal; căile lungi se rup cu `overflow-wrap: anywhere`.

### 11.5 Proiect (desktop)

```text
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ◎ Studio Harness                                  Temă: Auto   (el) ellob [admin]  [2 în așteptare]│
├────────────────────────────────────────────────────────────────────────────────────────────────────┤
│Workspace-uri             Ball                                    place 1291603 · al tău            │
│                          ┌───────────┬──────────┬──────────────┐                                   │
│┌▌────────────────────┐   │ Activitate│ Proiect  │ Dispozitive  │                                   │
││▌ Ball               │   └───────────┴──────────┴──────────────┘                                   │
││▌ (tu(an(mi 3 · 2 ses│                                                                             │
│└─────────────────────┘   Harta proiectului · 1 384 instanțe · scanată 14:02 de ellob               │
│ Arena                    ┌────────────────────────────────────────────────────────────┐            │
│ (mi 1 · 1 ses            │ Caută după nume, clasă sau cale                            │            │
│                          └────────────────────────────────────────────────────────────┘            │
│ Lobby v2                                                                                           │
│ nimeni · 0 ses           Grupe                                                                     │
│                          Grafică            212  ████████████                                      │
│                          Asseturi           498  ██████████████████████████                        │
│                          Interfață          131  ███████                                           │
│                          Scripturi server    44  ██                                                │
│                          Scripturi client    27  ██                                                │
│                          Module partajate    19  █                                                 │
│                          Rețea               12  █                                                 │
│                          Date                58  ███                                               │
│                          Fizică              90  █████                                             │
│                          Gameplay            21  █                                                 │
│                          Setări              14  █                                                 │
│                          Altele             258  █████████████                                     │
│                                                                                                    │
│                          Grafică · 212 · PointLight 96, SpotLight 40, Atmosphere 1…                │
│                          Lighting.Atmosphere                    Atmosphere                         │
│                          Workspace.Map.Zone3.Lamp1.PointLight   PointLight   [tu]                  │
│                          Workspace.Map.Zone3.Lamp2.PointLight   PointLight   [tu]                  │
│                          Workspace.Map.Zone4.Lamp1.PointLight   PointLight                         │
│hub a1b2c3 · v1.0.0       Arată mai mult (208)                                                      │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Antetul filei: „Harta proiectului · N instanțe · scanată HH:MM de <developer>” 13 `text-2` (+ chip `warning` „trunchiat la 20 000” când `truncated`). Căutarea (`.input`, 320 px) filtrează intrările grupei selectate și, fără grupă selectată, toate grupele (afișează primele 50 de potriviri cu grupa lor).
- Grupele: `.row--clickable` cu eticheta din `project_map` (13), contorul (mono, `tabular-nums`, aliniat dreapta) și bara de proporție (`.bar`, înălțime 6, `neutral-mark`; selectată → `primary-mark`, rândul → fundal `raised`). Lățimea barei = count / max(count) × 240 px.
- Detaliul grupei (sub listă; pe desktop poate sta în a doua coloană când lățimea ≥ 1400): titlul „Grafică · 212 · clasele principale…”, filtre pe clasă ca chip-uri `neutral` clicabile (`aria-pressed`), lista intrărilor `.row--mono` (cale, clasă, chip `warning` cu deținătorul când calea e ținută de un claim), paginată la 50 cu „Arată mai mult (N)”.
- Datele: `GET /hub/project?key=` la 30 s sau când `project.digest` din panel-data se schimbă.
- A doua coloană (≥ 1400 px) apare doar când detaliul are ce arăta: fără grupă selectată și fără căutare, `#project-body` primește clasa `project--single` și lista ocupă toată lățimea (altfel lista ar sta în 360 px, cu restul ecranului gol).

### 11.6 Dispozitive (desktop, admin)

```text
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ◎ Studio Harness                                  Temă: Auto   (el) ellob [admin]  [2 în așteptare]│
├────────────────────────────────────────────────────────────────────────────────────────────────────┤
│Workspace-uri             Ball                                    place 1291603 · al tău            │
│                          ┌───────────┬──────────┬──────────────┐                                   │
│┌▌────────────────────┐   │ Activitate│ Proiect  │ Dispozitive  │                                   │
││▌ Ball               │   └───────────┴──────────┴──────────────┘                                   │
││▌ (tu(an(mi 3 · 2 ses│                                                                             │
│└─────────────────────┘   Înrolare       ┌──────────────┬───────────┐                               │
│ Arena                                   │ Cu aprobare  │ Deschisă  │                               │
│ (mi 1 · 1 ses                           └──────────────┴───────────┘                               │
│                                                                                                    │
│ Lobby v2                 În așteptare                                        2                     │
│ nimeni · 0 ses           ┌▌─────────────────────────────────────────────────────────────────┐      │
│                          │▌ (mi) mihai · PC-MIHAI                        acum 2 min         │      │
│                          │▌ ab12cd34 · 1.0.0 · Ball                                         │      │
│                          │▌ [   Aprobă   ]  [ Revocă ]                                      │      │
│                          └──────────────────────────────────────────────────────────────────┘      │
│                          ┌▌─────────────────────────────────────────────────────────────────┐      │
│                          │▌ (?) Studio · LAPTOP-7                        acum 9 min         │      │
│                          │▌ 9f8e7d6c · 1.0.0 · fără workspace                               │      │
│                          │▌ [   Aprobă   ]  [ Revocă ]                                      │      │
│                          └──────────────────────────────────────────────────────────────────┘      │
│                                                                                                    │
│                          Toate dispozitivele                                 5                     │
│                          ● (el) ellob · PC-ELLOB     Aprobat    ab12cd34  Ball  acum       [Revocă]│
│                          ● (an) ana · PC-ANA         Aprobat    77aa11bb  Ball  acum       [Revocă]│
│                          ○ (mi) mihai · PC-MIHAI     În aștept. ab12cd34  —     acum 2 min [Aprobă]│
│hub a1b2c3 · v1.0.0       ○ (io) ion · PC-ION         Revocat    c0ffee12  —    ieri [Aprobă] [Uită]│
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Înrolare** (admin): `.segmented` „Cu aprobare | Deschisă” → `POST /hub/admin/enrollment`; sub el, 12 `text-2`: „Deschisă: orice dispozitiv nou este aprobat automat.” / „Cu aprobare: dispozitivele noi așteaptă un click aici.”
- **În așteptare** (admin, doar când `n > 0`): `.card` `warning` per dispozitiv: avatar 24 + nume Roblox (sau „Studio” + avatar „?”) · mașină, timp de la `first_seen`; id scurt mono · versiune · workspace; „Aprobă” primar sm, „Revocă” secundar sm. După click butonul trece pe `aria-busy` („Se aprobă…”) și rândul dispare la următorul poll.
- **Toate dispozitivele**: `.row` cu `lead` = punct online/offline, avatar 16, nume · mașină, chip de stare (§6.3), id scurt mono, workspace, ultima activitate (`elapsed`), acțiuni ghost sm după stare: `approved` → „Revocă” (danger, cere confirmare inline: rândul se extinde cu „Revoci accesul lui ana? Sesiunile și claims-urile ei se eliberează.” + „Revocă” danger / „Înapoi”); `pending` → „Aprobă”; `revoked` → „Aprobă”, „Uită” (danger, confirmare). Propriul dispozitiv nu are „Revocă” (adminul nu se poate tăia singur din panou).
- **Non-admin**: fără Înrolare și În așteptare; secțiunea „Dispozitivul tău” cu un singur rând (stare, id scurt, mașină, versiune) și textul „Doar adminul vede și aprobă celelalte dispozitive.”

### 11.7 Dispozitive la 400 px și fluxul unei sesiuni deschis

```text
┌────────────────────────────────────────────────┐
│ Înrolare                                       │
│ ┌────────────────────┬───────────────────┐     │
│ │    Cu aprobare     │     Deschisă      │     │
│ └────────────────────┴───────────────────┘     │
│                                                │
│ În așteptare                               2   │
│ ┌▌───────────────────────────────────────────┐ │
│ │▌ (mi) mihai · PC-MIHAI                     │ │
│ │▌ ab12cd34 · 1.0.0 · Ball · acum 2 min      │ │
│ │▌ [    Aprobă    ]  [  Revocă  ]            │ │
│ └────────────────────────────────────────────┘ │
│                                                │
│ Toate dispozitivele                        5   │
│ ● (el) ellob · PC-ELLOB          Aprobat       │
│   ab12cd34 · Ball · acum        [Revocă]       │
│ ○ (io) ion · PC-ION              Revocat       │
│   c0ffee12 · ieri        [Aprobă] [Uită]       │
└────────────────────────────────────────────────┘
```

```text
┌────────────────────────────────────────────────┐
│ Sesiuni live                               2   │
│ ┌▌───────────────────────────────────────────┐ │
│ │▌ Lumini mai calde în Zone3     de 4 min    │ │
│ │▌ [Claude Code] [Studio]                    │ │
│ │▌ (tu) tu · În lucru                        │ │
│ │▌────────────────────────────────────────── │ │
│ │▌ Tu                                        │ │
│ │▌ Fă luminile din Zone3 mai calde.          │ │
│ │▌ Claude Code                               │ │
│ │▌ Am citit Lighting și cele trei surse…     │ │
│ │▌ ▌ get_studio_state · 0,3 s                │ │
│ │▌ ▌ Claim acordat: Workspace.Map.Zone3      │ │
│ │▌                            Închide fluxul │ │
│ └────────────────────────────────────────────┘ │
└────────────────────────────────────────────────┘
```

Click pe un card de sesiune → `.card--open`: sub rândul 2 apare `.flow` (`GET /hub/session?job=`, apoi evenimente noi la fiecare poll de 2 s, `aria-live="polite"`), `max-height: 360px`, derulabil, cu buton ghost „Închide fluxul”. Un singur card deschis la un moment dat; deschiderea altuia îl închide pe primul. La desktop, același mecanism (nu drawer): mai puține stări de layout, aceeași lectură pe telefon și pe monitor.

---

## 12. Stări goale și mesaje (texte normative, română cu diacritice)

Vocea interfeței: propoziții scurte, verb activ, fără scuze, fără semne de exclamare. O stare goală spune ce va apărea aici și ce poate face utilizatorul acum. O eroare spune ce s-a întâmplat și care este pasul următor. Butonul își păstrează numele pe tot fluxul.

Tabelele de mai jos sunt **exhaustive** pentru textele scrise de plugin și de panou: stări goale, bannere, linii de stare, note de sesiune, etichete de control și confirmări. Nu se normează aici (și nu încalcă §15): datele venite de la hub sau din Studio (nume de jocuri și de developeri, căi, clase, mesajele agenților), textele de demonstrație din `?demo=1` — acoperite de bannerul „Date de exemplu. Nimic de aici nu vine de la un hub real.” — și mesajele de eroare ale hub-ului, fixate în `STUDIO_HUB_CONTRACT.md` §3.1 și afișate ca atare. Orice text nou apărut în cod se adaugă aici, în aceeași fază.

### 12.1 Plugin — stări goale

| Loc | Text | Acțiune (opțional) |
|---|---|---|
| Sesiuni · Ale tale | „Nicio sesiune încă. Alege providerul și apasă „Sesiune nouă”, sau pornește CLI-ul din terminal cu pluginul activ.” | — |
| Sesiuni · În workspace | „Nimeni altcineva nu lucrează acum în <workspace>.” | — |
| Sesiuni · alt workspace (vizualizare) | „Nicio sesiune activă în <workspace>.” | — |
| Prezenți (alt workspace) | „Nimeni prezent acum.” | — |
| Claims | „Nicio zonă revendicată. Agenții cer hub_claim înainte de a modifica scena.” | — |
| Jurnal | „Nicio modificare înregistrată încă în acest workspace.” | — |
| Workspace (fișier nepublicat) | numele fișierului + „Fișier nepublicat. Publică locul ca să apară în hub pentru ceilalți.” | — |
| Workspace (daemon neconectat) | „Daemon-ul nu răspunde. Pornește CLI-ul cu pluginul Studio Harness sau Start-Daemon.cmd.” | — |
| Alte workspace-uri | „Doar acest workspace este folosit acum.” (butonul e ascuns; textul apare doar dacă lista a fost deschisă înainte) | — |
| Flux (sesiune fără mesaje) | „Scrie primul pas mai jos. Agentul vede selecția ta din Studio.” (Mod S) / „Sesiunea nu a trimis încă niciun eveniment.” (Mod T) | — |
| Aprobare (fără argumente afișabile) | „Afișarea integrală depășește limita locală. Numai Refuză este disponibil.” (0.8, neschimbat) | Refuză |
| Aprobare · argumente prea mari / absente | „Argumentele nu pot fi afișate.” / „Fără argumente.” | — |
| Antet · daemon în curs de conectare | „Se conectează la daemon…” | — |
| „Sesiune nouă” la limita de ferestre | „Limita este de 32 de ferestre. Închide o sesiune inactivă înainte de a crea alta.” | — |
| Avansat · „Studio țintă” | „Studio țintă: <nume>” / „Automat: <nume>” / „Automat · N instanțe” / „Niciun Studio conectat” | — |
| Alte workspace-uri · rândul workspace-ului deschis | sufixul „· curent” pe rândul workspace-ului în care ești | — |
| Card de sesiune cu aprobări în așteptare | chip `warning` „N aprobări” | Permite / Refuză |

### 12.2 Plugin — bannere și linii de stare

| Situație | Rol | Text |
|---|---|---|
| hub `pending` | `warning` | „Așteaptă aprobarea adminului · dispozitiv ab12cd34” |
| hub `offline` | `danger` | „Hub offline: <eroare>. Sesiunile tale merg local; claims-urile sunt doar locale până revine.” |
| hub `revoked` | `danger` | „Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou.” |
| hub versiune veche | `danger` | „Hub-ul rulează o versiune mai veche. Actualizează hub-ul ca să apari în workspace.” |
| actualizare aplicată | `primary`, 5 s | „Actualizare aplicată: <versiune>. Interfața s-a reîncărcat fără repornire.” |
| loader nou instalat | `info` | „Loader nou instalat. Repornește Studio o dată, când ai timp.” |
| bundle căzut la metamorfoză | `danger` | „Interfața nouă nu a pornit: <eroare>. Rulează versiunea anterioară.” |
| daemon subsol | — | „Daemon · 127.0.0.1:34871 · la zi” / „Daemon · se conectează…” / „Daemon · nu răspunde (reîncerc în N s): <motiv>” / „Daemon deconectat la cerere. Joburile deja trimise pot continua; nu sunt retrimise automat.” / „… · actualizare în curs” / „… · actualizare <versiune> disponibilă (se instalează când nu rulează sesiuni)” / „… · actualizare: <eroare>” |
| deconectare daemon (confirmare) | `danger` | „Deconectezi daemon-ul? Sesiunile deschise rămân, dar nu mai primesc mesaje până la reconectare.” + „Deconectează” / „Înapoi” |

Actualizarea doar **disponibilă** rămâne numai în subsol (§6.2): nu cere nimic de la utilizator, iar un banner ar repeta
același mesaj pe același ecran. Bannerele rămase pentru actualizare sunt „Actualizare aplicată” (`primary`, 5 s, cu `×`) și
„Loader nou instalat” (`info`). Un singur banner pe ecran: starea hub-ului are prioritate, apoi actualizările.

### 12.2.1 Plugin — legătura cu daemon-ul (linia de stare și câmpul de cod)

Texte scrise de `BridgeController` (`controller:status(text, eroare)`) sub antet și în „Avansat”, plus cele două linii ale loader-ului. Nu sunt bannere: nu concurează cu §12.2, care ține starea hub-ului.

| Situație | Text |
|---|---|
| daemon absent sau sub 1.0 (HubView, blochează „Sesiune nouă”) | „Pornește daemon-ul 1.0 sau mai nou (terminal cu pluginul Claude Code sau Start-Daemon.cmd).” |
| daemon absent sau sub 1.0 (controller, blochează trimiterea) | „Daemon vechi sau lipsă: pornește daemon-ul 1.0 sau mai nou din terminalul cu pluginul Claude Code sau din Start-Daemon.cmd. Trimiterea este blocată.” |
| daemon-ul nu răspunde la HTTP | „Daemon-ul local nu a răspuns. Verifică pornirea lui și permisiunea HTTP pentru 127.0.0.1.” |
| răspuns care nu este JSON | „Răspuns JSON invalid de la daemon.” |
| cerere pe care pluginul nu o poate serializa | „Cererea nu poate fi codificată JSON.” |
| refuz fără mesaj propriu al daemon-ului | „Cererea a fost respinsă.” |
| una–două erori consecutive, înainte de deconectare | „Legătura locală este întreruptă temporar; verific din nou…” |
| poll fără `bridge_id` în răspuns | „Daemon-ul nu a identificat originea evenimentelor.” |
| niciun cod local livrat de loader | „Lipsește codul local: rulează Install-Studio-Plugin.cmd sau lipește codul din daemon.log în Avansat.” |
| câmpul de cod atins, dar gol | „Introdu codul local al daemon-ului, fără spații. Nu parola contului.” |
| cod refuzat (401), prefix după sursa lui | „Codul livrat la instalare a fost refuzat. ” / „Codul salvat în setări a fost refuzat. ” / „Codul lipit a fost refuzat. ” + hintul câmpului |
| câmpul de cod: placeholder și hint | „Lipește codul din daemon.log” / „Apasă Enter ca să conectezi daemon-ul cu codul lipit.” |
| „Panou” fără daemon conectat | „Panoul se deschide prin daemon: pornește-l și așteaptă conectarea.” |
| „Panou” refuzat de daemon | „Panoul nu s-a deschis: <eroare>” |
| identitate refuzată cu 4xx (nu deconectează) | „Identitatea nu a fost acceptată de daemon: <eroare>” |
| „Alte workspace-uri”, listare eșuată | „Lista workspace-urilor nu a putut fi citită: <eroare>” |
| „Alte workspace-uri”, hub indisponibil (503) | „Hub-ul nu este disponibil (stare: <status>).” |
| Studio țintă neconfirmat | „Studio-ul țintă nu a fost confirmat de daemon: <eroare>” |
| răspuns fără job valid | „Răspunsul nu identifică un job valid.” |
| inventar: scanare eșuată / daemon prea vechi | „scanarea a eșuat: <eroare>” / „daemon-ul nu primește inventarul (necesită 0.7)” |
| fereastra nativă nu poate fi creată (`Main`) | „Fereastra nativă nu a putut fi creată: <eroare>” |
| loader: codul livrat nu s-a putut salva (Output) | „Studio Harness: codul local livrat nu a putut fi salvat în setări; se folosește doar în această sesiune.” |
| butonul din bara Studio (tooltip) | „Deschide hubul sesiunilor” |

### 12.3 Plugin — hint-urile composer-ului (SessionView)

| Condiție | Text |
|---|---|
| poate trimite | „Doar această sesiune. Modificările cer claim și aprobare.” |
| în coadă | „În coadă. Daemon-ul lucrează pe rând.” |
| se oprește | „Anulare în curs, doar pentru acest job.” |
| neconfirmat | „Fără retry automat; reluarea cere confirmare.” |
| CLI lipsă | „CLI-ul oficial <provider> nu a fost găsit de daemon.” |
| daemon deconectat | „Daemon-ul nu răspunde. Mesajul rămâne ca draft.” |
| țintă lipsă | „Alege Studio-ul țintă în Avansat sau deschide locul din identitate.” |

### 12.3.1 Plugin — note, autori și confirmări în fereastra sesiunii

Bule `notice`/`error` din flux (`Store:message`), numele autorului unei bule proprii și confirmările inline din SessionView și ApprovalView.

| Situație | Titlu | Corp |
|---|---|---|
| fereastră nouă | „Sesiune nouă” | „Context separat pentru această fereastră. Scrie un mesaj; nimic nu se trimite înainte de Trimite.” |
| daemon deconectat (linia de stare a store-ului) | — | „Daemon deconectat. Poți deschide ferestre draft.” |
| daemon repornit (alt `bridge_id`) | „Daemon repornit” | „Referințele remote vechi au fost invalidate. Istoricul și draftul local sunt păstrate. Nimic nu este retrimis automat; următoarea trimitere începe o conversație remote nouă.” |
| fereastră oglindită prin hub | „Sesiune a altui developer” | „Fereastră doar de citit, oglindită prin hub. Oprirea, claims-urile și aprobările aparțin developerului ei.” |
| fereastră de sesiune din terminal | „Sesiune din terminal” | „Fereastră doar de citit. Comenzile se dau în terminalul developerului; aici vezi promptul, instrumentele și claims-urile.” |
| job încheiat | „Status” | „Sesiunea din terminal s-a încheiat.” / „Cererea s-a încheiat.” |
| job anulat | „Status” | „Sesiunea a fost oprită din Studio.” / „Cererea a fost anulată.” |
| job pierdut | „Status” | „Developerul nu mai răspunde hub-ului; sesiunea revine dacă se reconectează.” (remote) / „Jobul nu mai există în daemon; nu este reluat automat.” |
| trimitere eșuată | „Trimitere neconfirmată” | „<eroare>” + „Nu retrimit automat. Poți confirma explicit reluarea aceleiași cereri, cu același ID.” (neconfirmată) sau „Nimic nu este retrimis automat.” |
| decizie de aprobare trimisă | „Decizia ta” | „Aprobat: <instrument>” / „Refuzat: <instrument>” |
| aprobare expirată (409) | „Aprobare expirată” | „Apelul nu mai este activ.” |
| decizie neconfirmată | „Decizie neconfirmată” | „<eroare>” + „Nu este retrimisă automat.” |
| claims eliberate | „Claims” | rezultatul eliberării |
| eliberare neconfirmată | „Eliberare neconfirmată” | „<eroare>”; linia de stare spune „Eliberarea nu a fost confirmată: <eroare>” |
| anulare neconfirmată | „Anulare neconfirmată” | „<eroare>” |
| închidere blocată de daemon | „Închidere anulată” | „Așteaptă reconectarea daemon-ului, apoi apasă din nou × pentru a anula jobul înainte de închidere.” |
| închidere amânată | „Închidere în așteptare” | „Aștept identificarea cererii trimise înainte de anulare. Pentru o trimitere neconfirmată, reluarea se face numai cu confirmarea ta explicită.” |
| prea multe aprobări în fereastră | „Prea multe aprobări” | „Oprește această cerere și cere pași mai mici.” |

Autorul unei bule proprii: „Tu”, „Tu · neconfirmat” (trimitere fără răspuns), „Tu · respins” (refuzată de daemon), „Tu · neconfirmat înainte de restart” (după un handoff). Un text tăiat local primește pe linie nouă sufixul „[Afișare locală limitată.]”.

Blocaje la „Trimite” (`Store:canSend`, afișate ca hint sub composer, §12.3): „Pornește daemon-ul 1.0 sau mai nou (terminal cu pluginul Claude Code sau Start-Daemon.cmd).”, „Sesiunea nu există.”, „Sesiune din terminal: doar de citit.”, „Cererea acestei ferestre nu s-a încheiat.”, „Rezolvă aprobarea acestei ferestre.”, „Studio-ul țintă nu mai este conectat; verifică MCP în Studio sau alege altul în Avansat.”

Confirmări inline în SessionView (banner + două butoane, niciodată un dialog): reluare — „Reiei exact mesajul original, cu același ID. Daemon-ul deduplică cererea; dacă prima încercare nu a ajuns, o va trimite acum. Draftul nou nu este trimis.”; închidere cu job activ — „Închizi definitiv această fereastră? Jobul ei trebuie anulat înainte de ștergere. Celelalte sesiuni rămân active.”; închidere simplă — „Închizi definitiv această fereastră și istoricul ei local? Folosește Minimizează dacă vrei să revii.”

Aprobare (ApprovalView): antetul argumentelor complete este „Instrument: <instrument>”, cu secțiunile „Cod Luau:” și „Argumente complete:”; argumente absente — „Argumentele complete nu pot fi afișate. Refuză apelul.”; argumente peste limita locală — „Argumentele depășesc limita de afișare. Aprobarea este dezactivată; refuză și cere un apel mai mic.”

### 12.4 Panou — stări goale

| Loc | Text | Acțiune |
|---|---|---|
| Bara laterală (fără workspace-uri) | „Niciun workspace încă. Deschide un joc în Studio cu pluginul instalat și va apărea aici.” | — |
| Prezenți | „Nimeni prezent acum.” (când nici tu nu ești în workspace; când ești singur, rândul arată doar „tu”) | — |
| Sesiuni live | „Nicio sesiune activă. Pornește una din plugin sau din terminal.” | — |
| Claims | „Nicio zonă revendicată în acest workspace.” | — |
| Jurnal | „Nicio modificare înregistrată încă.” | — |
| Flux sesiune | „Sesiunea nu a trimis încă niciun eveniment.” | — |
| Proiect (fără hartă) | „Harta proiectului apare după prima scanare din Studio (aproximativ 30 s după conectare).” | — |
| Proiect (căutare fără rezultat) | „Nicio intrare pentru „<termen>”. Încearcă numele clasei sau o parte din cale.” | „Șterge căutarea” (ghost) |
| Proiect (grupă goală) | „Grupa nu are intrări în această scanare.” | — |
| Dispozitive · În așteptare | „Niciun dispozitiv în așteptare.” | — |
| Dispozitive · Toate | „Niciun dispozitiv înregistrat.” (posibil doar pe un hub nou) | — |
| Dispozitive (non-admin) | „Doar adminul vede și aprobă celelalte dispozitive.” | — |
| Conținut fără workspace selectat | „Niciun workspace selectat. Deschide un joc în Studio cu pluginul instalat sau alege unul din listă.” | — |
| Bara laterală · opțiunea din `select` | „Niciun workspace încă” | — |
| Jurnal filtrat pe un autor | „Nicio modificare de la <nume> în acest workspace.” | „Toți” (chip) |
| Proiect · filtru de clasă fără rezultat | „Nicio intrare de clasa <Clasă> în această grupă.” | „Arată toate clasele” (ghost) |
| Proiect · scanare goală | „Scanarea nu a găsit nicio instanță în acest loc.” | — |

### 12.4.1 Panou — stări de încărcare și erori de citire (tranzitorii)

Aceleași reguli de voce; apar doar cât durează o cerere sau o cădere și dispar singure.

| Loc | Text |
|---|---|
| Ecranul de așteptare · titlu/linie (`connecting`) | „Se conectează la hub…” / „Verific codul salvat în acest browser.” / „Se conectează…” |
| Ecranul de așteptare · titlu/linie (`down`) | „Hub-ul nu răspunde” / „Se reîncearcă la 5 s. Verifică adresa hub-ului sau conexiunea.” / „Offline · reîncerc la 5 s” |
| Ecranul de așteptare · linie (`pending` / `revoked`) | „În așteptare · verific la 5 s” / „Revocat · verific la 5 s” |
| Login, hub inaccesibil | „Hub-ul nu răspunde. Încearcă din nou.” |
| Flux sesiune, în curs de încărcare | „Se încarcă fluxul…” |
| Flux sesiune, cerere eșuată | „Fluxul nu s-a putut încărca. Se reîncearcă la 2 s.” |
| Flux sesiune, sesiunea a expirat din hub (banner `warning`, 6 s) | „Sesiunea nu mai este în hub: fluxul s-a închis.” |
| Fila Dispozitive, listare eșuată (banner `danger`, 6 s) | „Lista de dispozitive nu s-a putut încărca: <eroare>. Se reîncearcă.” |
| Proiect, harta completă se încarcă | „Se încarcă harta completă…” |
| Proiect, hub-ul are doar sumarul | „Harta completă nu este disponibilă încă; hub-ul are doar sumarul.” |
| Proiect, căutare fără grupă selectată | „Rezultate din toate grupele încărcate; alege o grupă pentru lista completă.” |
| Liste paginate | „Arată mai mult (N)” |
| Proiect, chip lângă antet cât se cere harta completă | „se încarcă harta…” |
| Proiect, hub-ul are doar sumarul (chip `info`) | „doar sumarul” |
| Ecranul de așteptare · titlu (`revoked`) | „Dispozitivul a fost revocat” |
| Titlul paginii în `?demo=1` | „Studio Harness — Panou (date de exemplu)” |

### 12.4.2 Panou — etichete permanente de control

Texte care stau pe ecran indiferent de date: titluri, etichete de câmp, butoane de bară și `aria-label`-uri citite de cititorul de ecran.

| Loc | Text |
|---|---|
| Login · explicație | „Deschide panoul din pluginul Studio Harness (butonul „Panou”). Se autentifică singur.” |
| Login · subsolul cardului | „Codul rămâne doar în acest browser.” |
| Pending · titlu și explicație | „Dispozitivul tău așteaptă aprobarea” / „Un admin îl aprobă din fila Dispozitive. Pagina se actualizează singură.” |
| Bara de sus · butonul de temă | „Temă: Auto” / „Temă: Luminos” / „Temă: Întunecat” (sub 560 px doar cuvântul temei) |
| Bara de sus · `aria-label` al butonului de temă | „Schimbă tema (acum: automată)” / „… luminoasă)” / „… întunecată)” |
| Bara de sus · badge-ul de admin | „N în așteptare” (sub 560 px doar numărul); `aria-label` „N dispozitive în așteptare · deschide fila Dispozitive”, `title` „N dispozitive în așteptare” |
| Fila Dispozitive cu dispozitive în așteptare | `aria-label` „Dispozitive · N dispozitive în așteptare” |
| Bara laterală | `aria-label` „Workspace-uri”; eticheta select-ului îngust „Workspace” |
| Activitate · filtrul de jurnal | `aria-label` „Filtrează jurnalul după developer” |
| Proiect · căutare | etichetă ascunsă „Caută în proiect”, placeholder „Caută după nume, clasă sau cale”, hint „Caută în grupa selectată sau în toate grupele.” |
| Proiect · antetul hărții | „Harta proiectului · N instanțe · scanată HH:MM de <developer>”, chip mono „· inventar <digest>”, chip `warning` „trunchiat la 20 000” |
| Proiect · secțiuni | „Grupe”; filtrul de clasă are `aria-label` „Filtrează după clasă” |
| Dispozitive · înrolare | `aria-label` „Modul de înrolare” |
| Fluxul unei sesiuni · lista de fapte | „Proprietar”, „Început”, „Ultima activitate”, „Claims”, „Job” |
| Claims · coloana din dreapta | „expiră în <timp>” / „expirat” |

### 12.5 Panou — erori și confirmări

| Situație | Unde | Text |
|---|---|---|
| cod refuzat | `.field__error` | „Codul nu a fost acceptat. Verifică-l sau deschide panoul din plugin (butonul Panou).” |
| cod revocat | `.field__error` / pending | „Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou.” |
| hub inaccesibil (fetch eșuat) | banner `danger` sub bara de sus, persistent cât durează | „Hub-ul nu răspunde. Se reîncearcă la 2 s.” (dispare singur când revine) |
| răspuns 401 după login | revenire la login + `.field__error` | „Sesiunea a expirat sau codul s-a schimbat. Lipește codul din nou.” |
| acțiune admin eșuată | banner `danger`, 6 s | „Nu s-a putut aplica: <eroare>. Încearcă din nou.” |
| revocare | inline în rând | „Revoci accesul lui <nume>? Sesiunile și claims-urile lui se eliberează.” + „Revocă” / „Înapoi” |
| uitare | inline în rând | „Uiți dispozitivul <id>? Va putea reveni ca dispozitiv nou, în așteptare.” + „Uită” / „Înapoi” |
| înrolare deschisă | sub segmented | „Deschisă: orice dispozitiv nou este aprobat automat.” |
| înrolare cu aprobare | sub segmented | „Cu aprobare: dispozitivele noi așteaptă un click aici.” |
| `?demo=1` | banner `info`, persistent | „Date de exemplu. Nimic de aici nu vine de la un hub real.” |

### 12.6 Glosar de acțiuni (aceleași cuvinte peste tot)

| Acțiune | Buton | Confirmare / rezultat |
|---|---|---|
| deschide panoul | Panou | — |
| creează sesiune | Sesiune nouă | fereastra se deschide |
| trimite prompt | Trimite | „Se trimite…” → mesajul apare în flux |
| oprește job | Oprește | „Se oprește” → „Oprit” |
| aprobă tool | Permite | bula `approval` dispare |
| refuză tool | Refuză | bula `approval` dispare |
| eliberează claim | Eliberează | rândul dispare |
| aprobă dispozitiv | Aprobă | „Se aprobă…” → chip „Aprobat” |
| revocă dispozitiv | Revocă | confirmare → „Se revocă…” → chip „Revocat” |
| uită dispozitiv | Uită | confirmare → „Se uită…” → rândul dispare |
| autentificare | Intră | „Se verifică…” → panoul intră |
| schimbă codul | Folosește alt cod | revine la login |
| deconectează daemon | Deconectează daemon-ul | confirmare → „Deconectează” |
| închide sesiune | × → „Închide definitiv” / „Anulează și închide” | — |
| minimizează | _ | chip „Minimizată” în hub |
| închide fluxul | Închide fluxul | cardul se strânge |
| revenire | Înapoi | — |

Fără „OK”, „Anulare” generică, „Submit”, „Da/Nu”: fiecare buton spune ce face.

---

## 13. Micro-interacțiuni

| Regulă | Web | Studio |
|---|---|---|
| Durată | 140 ms (`--duration`), `ease-out` implicit | `Theme.tween` 0,14 s, Quad Out |
| Ce se animă | doar `background-color`, `border-color`, `color`, `opacity` pe hover/apăsat/focus | doar `BackgroundColor3` la hover/apăsat |
| Ce nu se animă niciodată | poziții, dimensiuni, apariția cardurilor, încărcarea paginii, punctele de stare (fără puls), barele de proporție | idem; fără `TweenPosition`/`TweenSize` |
| Deschidere inline (flux, Avansat, lista de workspace-uri) | instant, fără slide; conținutul apare și împinge ce e dedesubt | idem |
| Banner temporar | apare instant, dispare după 5 s cu `opacity` 140 ms | `show(role, text, 5)` |
| Poll fără pâlpâit | actualizare **în loc**: fiecare card/rând are o cheie (`job_id`, `path`, `device_id`); DOM-ul se reconciliază, nu se reconstruiește; textele se schimbă doar dacă diferă | view-urile păstrează `entries[id]`, `claimRows[key]`, `messageViews[message]` (ca 0.8) și apelează `set(...)`; niciodată `Destroy` + recreare pentru același obiect |
| Timp scurs | recalculat local la fiecare secundă din `started`/`since`, nu din poll | `Theme.elapsed` la fiecare `update` (poll la 1 s) |
| Scroll | fluxul urmărește capătul doar dacă utilizatorul era la capăt (toleranță 60 px) | `followChat` (0.8) |
| Focus după acțiune | după „Aprobă” pe un card de dispozitiv, focusul trece pe următorul card sau pe titlul secțiunii; după „Închide fluxul”, pe cardul sesiunii | după „Permite/Refuză”, focusul rămâne pe fereastra sesiunii |
| Buton în lucru | `aria-busy="true"` + text la gerunziu; nu se dezactivează vizual complet (opacitate 0,7) | `Theme.enabled(button, false)` + text la gerunziu |
| Confirmări | inline, în locul acțiunii; niciodată `window.confirm` | inline (banner + două butoane) |
| Sunete, vibrații, notificări de sistem | niciuna | niciuna |
| Poll fără randare inutilă | răspunsul `/hub/panel-data` se compară ca JSON cu `hub.now` neutralizat; identic → pasul de randare se sare complet (timpii scurși se recalculează local, la secundă). Se randează doar fila vizibilă; celelalte la activarea filei | view-urile se actualizează la fiecare `sync`, dar scriu o proprietate doar când valoarea diferă |

---

## 14. Accesibilitate

### 14.1 Comun

- Contrast: text ≥ 4,5:1, obiecte grafice informative (puncte, șine, conturul câmpurilor) ≥ 3:1, verificate pentru fiecare token din §2 (`Theme.spec.luau` impune valorile paletei; panoul folosește aceeași paletă).
- Culoarea nu este niciodată singurul purtător de informație: punct + etichetă, șină + chip de stare, avatar estompat + „Offline”.
- Mărimea minimă a textului: 12 px, și doar pentru meta; corpul e 13–14.
- Țintele: ≥ 32 px înălțime (40 pe ecran ≤ 400 px); butoanele ghost mici au `padding` care le duce la 28 px vizual, dar zona de click rămâne 32.
- Texte trunchiate: valoarea completă rămâne disponibilă (`title` pe web; în Studio, cardul de sesiune deschide fereastra cu titlul complet).

### 14.2 Web

- Structură: `<header>` (topbar), `<nav aria-label="Workspace-uri">`, `<main>`, `<h1>` (numele workspace-ului), `<h2>` (titlurile secțiunilor); un singur `<h1>` per ecran.
- File: `role="tablist"` / `role="tab"` / `role="tabpanel"`, `aria-selected`, `aria-controls`, `tabindex` roving, săgeți + Home/End.
- Segmented: `role="radiogroup"` / `role="radio"` / `aria-checked`, săgeți.
- Bannere: `role="status"` (`aria-live="polite"`); `danger` → `role="alert"`.
- Fluxul deschis: `aria-live="polite"`, `aria-relevant="additions"`.
- Butoane cu text mereu (fără butoane doar-iconiță; `×` primește `aria-label="Închide"`).
- Avatare: containerul primește `aria-hidden="true"` când numele este scris alături (carduri de sesiune, rânduri de claim/jurnal/dispozitiv, detaliile sesiunii, rândul de prezenți cu nume listate) și `role="img"` + `aria-label="<nume>"` doar când stă singur (bara de sus, stiva din cardul de workspace). `<img>`-ul din interior are mereu `alt=""`; la eroare sau 204 se ascunde și rămân inițialele.
- Focus vizibil (`:focus-visible`) pe tot ce e focusabil; ordinea tab-ului urmează ordinea vizuală (fără `tabindex` > 0).
- Câmpuri: `<label for>`, erorile legate prin `aria-describedby`, `aria-invalid="true"` la refuz.
- `prefers-reduced-motion` respectat (§9.6); `prefers-color-scheme` respectat cu comutator manual salvat.
- Zoom 200 %: layout-ul rămâne cel de ≤ 400 px, fără pierdere de conținut.
- Limba: `<html lang="ro">`.

### 14.3 Studio

- Toate butoanele `Selectable = true` (navigare cu gamepad/tastatură a Studio-ului), `Active` corect; `NextSelectionDown/Up` nu se setează manual (Studio le deduce), dar ordinea `LayoutOrder` urmează ordinea de citire.
- `TextBox`: inel de focus vizibil (`Theme.focusRing`), `ClearTextOnFocus = false`, textul nu se rescrie cât timp e focusat.
- Tooltip-uri: nu există în Studio pentru pluginuri; de aceea eticheta stării hub-ului apare ca text pe a doua linie a antetului (sub nume, aliniată la 36 px) când starea nu e `approved`, iar id-urile scurte sunt afișate integral în Avansat (`device_id` complet, mono, selectabil într-un `TextBox` needitabil).
- Textele nu depind de lățime: la 320 px nimic nu se suprapune (verificat în §10); peste 320 cardurile se lărgesc, textul nu crește.
- Contrastul în Studio este cel al paletei proprii (dock-ul își pictează fundalul), independent de culorile temei Studio; singurul lucru citit din Studio este numele temei.

---

## 15. Lista de verificare pentru fazele 4–6 (și pentru revizia 8C)

Design:

- [ ] Fiecare culoare din cod este un token din §2.5 (grep după `#` în CSS și după `fromHex`/`fromRGB` în Luau: doar în `PALETTES` / `:root`).
- [ ] Contrastele din `Theme.spec.luau` trec; panoul folosește exact aceleași valori.
- [ ] Șina de stare există pe carduri, bannere și bulele tool/claim/approval/error/notice, cu rolurile din §6.
- [ ] Verdele primar apare doar pe butonul primar, „În lucru”, „Hub conectat”/„Online”, fila activă și grupa selectată.
- [ ] Fără majuscule spațiate, fără mono în afara identificatorilor, fără umbre în dark, fără animații decorative.
- [ ] Raze: 6 pe controale, 10 pe carduri, 999 pe avatare și puncte; nimic altceva.

Componente:

- [ ] Numele claselor CSS și ale funcțiilor `Theme.*` sunt cele din §7/§9.7/§8.7; nimic în plus fără actualizarea acestui document.
- [ ] `Theme.luau` se încarcă în `luau.exe`; nicio globală Roblox la nivel de modul.
- [ ] `RichText = false` pe orice instanță de text; `textContent` pentru orice date în panou.
- [ ] Toate stările goale din §12 există și au textul exact.
- [ ] Toate etichetele de stare vin din tabelele §6 (nicio etichetă inventată în view-uri).

Ecrane:

- [ ] HubView are toate cele 9 rânduri din §10.1, în ordine; SessionView și ApprovalView respectă §10.6–§10.8.
- [ ] Panoul: login, pending, shell cu bara laterală, Activitate, Proiect, Dispozitive, la 400 px și desktop, dark și light, fără scroll orizontal.
- [ ] Tema Studio: schimbarea temei în Studio repictează pluginul fără repornire; comutatorul panoului câștigă peste `prefers-color-scheme` în ambele direcții.
- [ ] Tastatură: tot fluxul de login → aprobare dispozitiv → deschidere flux se poate face fără mouse, cu focus vizibil.
- [ ] Poll-ul nu pâlpâie: cardurile își păstrează identitatea DOM/instanță între actualizări.
