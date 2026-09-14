-- Studio Harness Theme 1.0.0 — sistemul de design al pluginului (design/DESIGN.md §2–§8).
-- Nivelul de modul nu atinge globale Roblox (Color3, Enum, Instance, UDim2, settings, TweenService):
-- datele și funcțiile pure rulează în luau.exe (tests/Theme.spec.luau). Tot ce creează instanțe stă în funcții.
local Theme = {}
Theme.VERSION = "1.0.0"

-- 1. Date -----------------------------------------------------------------------------------------------------------

-- Paleta completă (DESIGN.md §2.5): cele două moduri au exact același set de chei, verificat în spec.
Theme.PALETTES = {
	dark = { bg = "#0B0F14", surface = "#121821", raised = "#1A2230", border = "#26303F", borderStrong = "#3A4657",
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
-- Etichete tranzitorii ale unei ferestre (DESIGN.md §6.1): nu sunt stări, doar texte peste starea curentă.
Theme.TRANSIENT = { cancelling = "Se oprește", releasing = "Eliberez claims" }
-- Textul stării hub-ului „versiune veche” (identic cu `Store.OLD_HUB_ERROR`, fără require de SessionStore).
Theme.OLD_HUB_ERROR = "Hub-ul rulează o versiune mai veche."

-- Modul curent și tabelele construite de `apply` (goale până la primul apel; niciun Color3 la nivel de modul).
Theme.mode = "dark"
Theme.colors = {}
Theme.fonts = {}

local ROLE_SET = {}
for _, role in ipairs(Theme.ROLES) do ROLE_SET[role] = true end
local AVATAR_SIZES = { [48] = true, [60] = true, [100] = true, [150] = true }
local BUTTON_HEIGHT = { sm = 28, md = 32, lg = 36 }
local BUTTON_TEXT = { sm = "sm", md = "md", lg = "md" }
local MORE_WIDTH = 34 -- lățimea rezervată chip-ului „+N” din rândul de prezență
local BUTTON_NARROW = 96 -- sub această lățime dată explicit, butonul primește padding mic (§7.5)
local CLOSE_WIDTH = 28 -- lățimea butonului „×” din colțul bannerului (§7.9)
-- Majusculele care lipsesc din `string.upper` (care lucrează pe octeți): diacriticele românești și vecinii lor.
local UPPER = { ["ă"] = "Ă", ["â"] = "Â", ["î"] = "Î", ["ș"] = "Ș", ["ț"] = "Ț", ["ş"] = "Ş", ["ţ"] = "Ţ",
	["á"] = "Á", ["à"] = "À", ["é"] = "É", ["è"] = "È", ["í"] = "Í", ["ó"] = "Ó", ["ö"] = "Ö", ["ú"] = "Ú",
	["ü"] = "Ü", ["ä"] = "Ä", ["ç"] = "Ç", ["ñ"] = "Ñ", ["ø"] = "Ø", ["å"] = "Å" }

-- 2. Funcții pure ---------------------------------------------------------------------------------------------------

-- Tăiere sigură pe octeți: nu lasă un caracter UTF-8 rupt la capăt (aceeași regulă ca `Store.prefix`).
local function cut(text, limit)
	if #text <= limit then return text end
	local stop = limit
	while stop > 0 do
		local byte = string.byte(text, stop + 1)
		if not byte or byte < 128 or byte >= 192 then break end
		stop -= 1
	end
	return string.sub(text, 1, stop)
end

-- Primele `count` caractere UTF-8 ale unui text (fără să taie un caracter la jumătate).
local function firstChars(text, count)
	local ok, stop = pcall(utf8.offset, text, count + 1)
	if ok and stop then return string.sub(text, 1, stop - 1) end
	return text
end

-- Majuscula unui singur caracter (ASCII prin `string.upper`, diacriticele din tabel).
local function upperChar(character)
	return UPPER[character] or (string.upper(character))
end

local function luminanceChannel(value)
	value /= 255
	if value <= 0.03928 then return value / 12.92 end
	return ((value + 0.055) / 1.055) ^ 2.4
end

local function luminance(hex)
	local text = (string.gsub(tostring(hex or ""), "^#", ""))
	local red = tonumber(string.sub(text, 1, 2), 16) or 0
	local green = tonumber(string.sub(text, 3, 4), 16) or 0
	local blue = tonumber(string.sub(text, 5, 6), 16) or 0
	return 0.2126 * luminanceChannel(red) + 0.7152 * luminanceChannel(green) + 0.0722 * luminanceChannel(blue)
end

-- Numele temei Studio → modul paletei: doar „Dark” (indiferent de majuscule) este întuneric.
function Theme.resolveMode(themeName)
	if type(themeName) == "string" and string.lower(themeName) == "dark" then return "dark" end
	return "light"
end

-- Hex-ul unui token în modul cerut (implicit modul curent); un nume greșit iese în spec, nu în Studio.
function Theme.token(name, mode)
	local palette = Theme.PALETTES[mode] or Theme.PALETTES[Theme.mode] or Theme.PALETTES.dark
	local value = palette[name]
	if not value then error("Token necunoscut: " .. tostring(name), 2) end
	return value
end

-- Cele patru fețe ale unui rol, ca nume de token-uri (DESIGN.md §2.3); rolul necunoscut devine `neutral`.
function Theme.roleTokens(role)
	if role == "completed" then return { fill = "primary", text = "text2", mark = "completedMark", soft = "neutralSoft" } end
	if type(role) ~= "string" or not ROLE_SET[role] then role = "neutral" end
	return { fill = role, text = role .. "Text", mark = role .. "Mark", soft = role .. "Soft" }
end

-- Raportul de contrast WCAG 2.1 dintre două culori hex (≥ 1); spec-ul impune pragurile din §2.
function Theme.contrastRatio(hexA, hexB)
	local first, second = luminance(hexA), luminance(hexB)
	if first < second then first, second = second, first end
	return (first + 0.05) / (second + 0.05)
end

-- Timpul scurs, pe scara cerută de interfață: „de 4 s”, „de 3 min”, „de 2 h”, „de 3 zile”.
function Theme.elapsed(since, now)
	if type(since) ~= "number" then return "" end
	local current = if type(now) == "number" then now else os.time()
	local seconds = math.max(0, math.floor(current) - math.floor(since))
	if seconds < 60 then return "de " .. seconds .. " s" end
	if seconds < 3600 then return "de " .. math.floor(seconds / 60) .. " min" end
	if seconds < 86400 then return "de " .. math.floor(seconds / 3600) .. " h" end
	local days = math.floor(seconds / 86400)
	return if days == 1 then "de 1 zi" else "de " .. days .. " zile"
end

-- Ora unui moment („14:02”); fără moment, cratimele ocupă același loc.
function Theme.clock(at)
	if type(at) ~= "number" then return "--:--" end
	local ok, text = pcall(os.date, "%H:%M", math.floor(at))
	return if ok and type(text) == "string" then text else "--:--"
end

-- Inițialele unui nume, pentru avatarul fără imagine: „Ana Maria” → „AM”, „ellob” → „EL”, lipsă → „?”.
function Theme.initials(name)
	local text = if type(name) == "string" then name else ""
	local words = {}
	for word in string.gmatch(text, "%S+") do
		table.insert(words, word)
		if #words == 2 then break end
	end
	if #words == 0 then return "?" end
	if #words == 2 then return upperChar(firstChars(words[1], 1)) .. upperChar(firstChars(words[2], 1)) end
	local first = firstChars(words[1], 1)
	local both = firstChars(words[1], 2)
	if both == first then return upperChar(first) end
	return upperChar(first) .. upperChar(string.sub(both, #first + 1))
end

-- Prefixul unui identificator lung (device_id la 8, job_id la 12); lipsă → „—”.
function Theme.shortId(id, length)
	local text = if type(id) == "string" then id else ""
	if text == "" then return "—" end
	return cut(text, if type(length) == "number" and length > 0 then math.floor(length) else 8)
end

-- URL-ul imaginii de profil Roblox; fără cont cunoscut nu există imagine (avatarul arată inițialele).
function Theme.avatarUrl(userId, size)
	local id = if type(userId) == "number" then math.floor(userId) else nil
	if not id or id <= 0 then return nil end
	local pixels = if type(size) == "number" and AVATAR_SIZES[math.floor(size)] then math.floor(size) else 48
	return "rbxthumb://type=AvatarHeadShot&id=" .. id .. "&w=" .. pixels .. "&h=" .. pixels
end

-- Text scurtat la `limit` octeți, cu „…” când a fost tăiat; tăierea nu rupe niciodată un caracter UTF-8.
function Theme.fit(text, limit)
	local value = if type(text) == "string" then text else ""
	local stop = if type(limit) == "number" and limit > 0 then math.floor(limit) else 0
	if #value <= stop then return value end
	return cut(value, stop) .. "…"
end

-- Pluralul românesc al unui contor: „1 sesiune”, „3 sesiuni”, „20 de sesiuni”.
function Theme.countLabel(n, singular, plural)
	local count = if type(n) == "number" then math.floor(n) else 0
	if count == 1 then return "1 " .. singular end
	local rest = math.abs(count) % 100
	if math.abs(count) >= 20 and (rest == 0 or rest >= 20) then return count .. " de " .. plural end
	return count .. " " .. plural
end

-- Starea afișată a unei ferestre: cheia, eticheta din §6.1 și rolul de culoare.
function Theme.sessionState(window)
	local data = if type(window) == "table" then window else {}
	local key = if type(data.state) == "string" and data.state ~= "" then data.state else "draft"
	local approvals = if type(data.approvals) == "table" then #data.approvals else 0
	if approvals > 0 or data.pendingApproval then key = "waiting_approval" end
	local label, role = Theme.states[key], Theme.SESSION_ROLE[key]
	if not label then label, role = key, "neutral" end
	if data.cancelling then label = Theme.TRANSIENT.cancelling
	elseif data.releasing then label = Theme.TRANSIENT.releasing end
	return key, label, role or "neutral"
end

-- Rolul de culoare al unei bule din flux; răspunsul agentului poartă culoarea providerului.
function Theme.messageRole(kind, provider)
	if kind == "assistant" and (provider == "claude" or provider == "codex") then return provider end
	return Theme.MESSAGE_ROLE[kind] or "neutral"
end

-- Meta-linia workspace-ului: „place 1291603 · al tău” / „· grup 555” / „· utilizator 555”.
function Theme.placeLine(workspace, identity)
	if type(workspace) ~= "table" then return "Fișier nepublicat" end
	local key = if type(workspace.key) == "string" and workspace.key ~= "" then workspace.key else nil
	if not key or key == "local" then return "Fișier nepublicat" end
	local placeId = math.floor(tonumber(workspace.placeId) or 0)
	local head = if placeId > 0 then "place " .. placeId else key
	local creatorId = math.floor(tonumber(workspace.creatorId) or 0)
	if creatorId <= 0 then return head end
	local userId = if type(identity) == "table" then math.floor(tonumber(identity.userId) or 0) else 0
	if userId > 0 and creatorId == userId then return head .. " · al tău" end
	if workspace.creatorType == "Group" then return head .. " · grup " .. creatorId end
	return head .. " · utilizator " .. creatorId
end

-- Textul complet al stării hub-ului pentru banner/subsol (DESIGN.md §6.2, §12.2) și rolul lui de culoare.
function Theme.hubLine(hub)
	local data = if type(hub) == "table" then hub else {}
	local status = data.status
	if status == "approved" then return "Hub conectat", "primary" end
	if status == "pending" then
		return "Așteaptă aprobarea adminului · dispozitiv " .. Theme.shortId(data.deviceId), "warning"
	end
	if status == "offline" then
		local reason = if type(data.error) == "string" and data.error ~= "" then data.error else "hub-ul nu răspunde"
		if string.sub(reason, 1, #Theme.OLD_HUB_ERROR) == Theme.OLD_HUB_ERROR then
			return "Hub-ul rulează o versiune mai veche. Actualizează hub-ul ca să apari în workspace.", "danger"
		end
		reason = (string.gsub(cut(reason, 80), "[%.%s]+$", ""))
		return "Hub offline: " .. reason .. ". Sesiunile tale merg local; claims-urile sunt doar locale până revine.", "danger"
	end
	if status == "revoked" then return "Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou.", "danger" end
	if status == "disabled" then return "Hub dezactivat (daemon pornit cu --no-hub).", "neutral" end
	return "Se conectează la hub…", "neutral"
end

-- Geometria textului unui buton (§3.3, §7.5): o lățime fixă se trunchiază la capăt cu „…”, „auto” își urmează textul.
-- Padding-ul lateral este 12, dar un buton îngust dat explicit (ex. „×” de 28 px) primește 4, ca textul să încapă.
function Theme.buttonText(auto, fixedWidth)
	if auto == true then return false, Theme.SPACE.md end
	local narrow = type(fixedWidth) == "number" and fixedWidth > 0 and fixedWidth < BUTTON_NARROW
	return true, (if narrow then Theme.SPACE.xs else Theme.SPACE.md)
end

-- Padding-ul din dreapta al corpului unui banner (§7.9): cu „×” în colț textul se retrage cu lățimea butonului plus
-- spațiile, altfel prima linie trece pe sub el.
function Theme.bannerPad(close)
	return if close then Theme.SPACE.md + CLOSE_WIDTH + Theme.SPACE.xs else Theme.SPACE.md
end

-- 3. Runtime: tema --------------------------------------------------------------------------------------------------

local painted = setmetatable({}, { __mode = "k" })
local tweens = setmetatable({}, { __mode = "k" })
local strokeBase = setmetatable({}, { __mode = "k" })
local tweenService = nil

-- Un token poate fi dat și pe moduri (`{ dark = "bg", light = "surface" }`): se rezolvă la fiecare repictare.
local function tokenName(value)
	if type(value) == "table" then return value[Theme.mode] or value.dark or value.light end
	return value
end

local function repaint(instance, roles)
	for property, value in pairs(roles) do
		local color = Theme.colors[tokenName(value)]
		if color then pcall(function() instance[property] = color end) end
	end
end

local function ensureColors()
	if next(Theme.colors) == nil then Theme.apply(Theme.mode) end
end

-- Construiește culorile și fonturile modului și repictează tot ce a trecut prin `paint`. Idempotent.
function Theme.apply(mode)
	local palette = Theme.PALETTES[mode]
	if not palette then mode, palette = "dark", Theme.PALETTES.dark end
	Theme.mode = mode
	local colors = {}
	for name, hex in pairs(palette) do colors[name] = Color3.fromHex(hex) end
	Theme.colors = colors
	Theme.fonts = { ui = Enum.Font[Theme.FONT_NAMES.ui], body = Enum.Font[Theme.FONT_NAMES.body], mono = Enum.Font[Theme.FONT_NAMES.mono] }
	for instance, roles in pairs(painted) do repaint(instance, roles) end
	return mode
end

-- Culoarea unui token în modul curent; token necunoscut = greșeală de tastare, deci eroare.
function Theme.color(token)
	ensureColors()
	local color = Theme.colors[tokenName(token)]
	if not color then error("Token necunoscut: " .. tostring(token), 2) end
	return color
end

-- Fontul cerut („ui”, „body”, „mono”); fonturile nu depind de mod, dar se construiesc odată cu paleta.
function Theme.font(name)
	ensureColors()
	return Theme.fonts[name or "body"] or Theme.fonts.body
end

-- Leagă proprietățile de culoare ale unei instanțe de token-uri; `apply` le repictează pe toate la schimbarea temei.
function Theme.paint(instance, roles)
	ensureColors()
	local mapping = painted[instance]
	if not mapping then
		mapping = {}
		painted[instance] = mapping
	end
	for property, value in pairs(roles) do
		Theme.color(value) -- validează numele token-ului acum, nu abia la schimbarea temei
		mapping[property] = value
	end
	repaint(instance, mapping)
	return instance
end

local RECOLOR = {
	chip = { BackgroundColor3 = "soft", TextColor3 = "text" },
	dot = { BackgroundColor3 = "mark" },
	rail = { BackgroundColor3 = "mark" },
	banner = { BackgroundColor3 = "soft" },
	bubble = { BackgroundColor3 = "soft" },
	roleText = { TextColor3 = "text" },
	underline = { BackgroundColor3 = "mark" },
}

-- Schimbă rolul unei componente deja construite (șină, chip, punct, banner, bulă) fără să o recreeze.
function Theme.recolor(instance, role)
	instance:SetAttribute("Role", role)
	local map = RECOLOR[instance:GetAttribute("Component")]
	if not map then return instance end
	local tokens = Theme.roleTokens(role)
	local roles = {}
	for property, field in pairs(map) do roles[property] = tokens[field] end
	return Theme.paint(instance, roles)
end

-- Mișcarea din §13: 140 ms, Quad Out, doar culori și transparențe; tween-ul anterior al instanței se anulează.
function Theme.tween(instance, properties, seconds)
	if not tweenService then tweenService = game:GetService("TweenService") end
	local previous = tweens[instance]
	if previous then pcall(function() previous:Cancel() end) end
	local tween = tweenService:Create(instance, TweenInfo.new(seconds or 0.14, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), properties)
	tweens[instance] = tween
	tween:Play()
	return tween
end

-- Hover și apăsat pentru un buton sau un card; conectat pe instanță, deci `Destroy()` deconectează.
function Theme.hover(button, tokens)
	local hovering, pressed = false, false
	local function refresh()
		if button:GetAttribute("EnabledAction") == false then return end
		local active = hovering or pressed
		local goal = {}
		local background = tokens.normal
		if pressed and tokens.pressed then background = tokens.pressed
		elseif active and tokens.hover then background = tokens.hover end
		if background then goal.BackgroundColor3 = Theme.color(background) end
		local fade = if active then tokens.fadeHover else tokens.fadeNormal
		if fade then goal.BackgroundTransparency = fade end
		local textToken = if active then (tokens.textHover or tokens.textNormal) else (tokens.textNormal or tokens.textHover)
		if textToken then goal.TextColor3 = Theme.color(textToken) end
		if next(goal) then Theme.tween(button, goal) end
		if tokens.stroke then
			local strokeToken = if active then (tokens.strokeHover or tokens.strokeNormal) else (tokens.strokeNormal or tokens.strokeHover)
			if strokeToken then Theme.tween(tokens.stroke, { Color = Theme.color(strokeToken) }) end
		end
	end
	button.MouseEnter:Connect(function() hovering = true; refresh() end)
	button.MouseLeave:Connect(function() hovering, pressed = false, false; refresh() end)
	button.MouseButton1Down:Connect(function() pressed = true; refresh() end)
	button.MouseButton1Up:Connect(function() pressed = false; refresh() end)
end

-- Inelul de focus al unui câmp text (§5): conturul devine `focus` de 2 px cât timp se tastează.
function Theme.focusRing(textBox, stroke)
	textBox.Focused:Connect(function()
		Theme.paint(stroke, { Color = "focus" })
		stroke.Thickness = 2
	end)
	textBox.FocusLost:Connect(function()
		Theme.paint(stroke, { Color = "borderInput" })
		stroke.Thickness = 1
	end)
end

-- Activarea unui buton: `EnabledAction` filtrează click-urile, iar dezactivarea este opacitate, nu altă culoare.
function Theme.enabled(button, allowed)
	local ok = allowed == true
	button:SetAttribute("EnabledAction", ok)
	button.Active, button.Selectable = ok, ok
	button.TextTransparency = if ok then 0 else 0.45
	local stroke = button:FindFirstChildWhichIsA("UIStroke")
	if stroke then stroke.Transparency = if ok then (strokeBase[stroke] or 0) else 0.45 end
	if button:GetAttribute("Kind") == "primary" then button.BackgroundTransparency = if ok then 0 else 0.45 end
end

-- 4. Runtime: legături ----------------------------------------------------------------------------------------------

function Theme.connect(owner, signal, callback)
	local connection = signal:Connect(function(...)
		if not owner.destroyed then callback(...) end
	end)
	table.insert(owner.connections, connection)
	return connection
end

function Theme.click(owner, button, callback)
	Theme.enabled(button, true)
	return Theme.connect(owner, button.Activated, function()
		if button:GetAttribute("EnabledAction") then callback() end
	end)
end

function Theme.cleanup(owner)
	owner.destroyed = true
	for _, connection in ipairs(owner.connections) do connection:Disconnect() end
	table.clear(owner.connections)
end

-- Citește tema Studio, o aplică și se reabonează la `ThemeChanged`; `Main` îl apelează o dată, înaintea view-urilor.
function Theme.install(owner, onChange)
	local function currentMode()
		local ok, name = pcall(function() return settings().Studio.Theme.Name end)
		if not ok then return "dark" end
		return Theme.resolveMode(name)
	end
	local mode = Theme.apply(currentMode())
	local ok, studio = pcall(function() return settings().Studio end)
	if ok and studio then
		Theme.connect(owner, studio.ThemeChanged, function()
			local applied = Theme.apply(currentMode())
			if onChange then onChange(applied) end
		end)
	end
	return mode
end

-- 5. Runtime: constructori de bază ----------------------------------------------------------------------------------

local ALIGN = nil -- construit la prima folosire: Enum nu se atinge la nivel de modul.
local function alignment(name)
	if not ALIGN then
		ALIGN = { left = Enum.TextXAlignment.Left, center = Enum.TextXAlignment.Center, right = Enum.TextXAlignment.Right }
	end
	return ALIGN[name or "left"] or ALIGN.left
end

function Theme.new(className, properties, parent)
	local instance = Instance.new(className)
	for property, value in pairs(properties or {}) do instance[property] = value end
	instance.Parent = parent
	return instance
end

function Theme.frame(parent, name, token)
	local frame = Theme.new("Frame", { Name = name or "Frame", Size = UDim2.fromScale(1, 1), BorderSizePixel = 0 }, parent)
	if token == "transparent" then
		frame.BackgroundTransparency = 1
	else
		Theme.paint(frame, { BackgroundColor3 = token or "bg" })
	end
	return frame
end

function Theme.padding(parent, x, y)
	local horizontal, vertical = x or 0, y or x or 0
	return Theme.new("UIPadding", { PaddingLeft = UDim.new(0, horizontal), PaddingRight = UDim.new(0, horizontal),
		PaddingTop = UDim.new(0, vertical), PaddingBottom = UDim.new(0, vertical) }, parent)
end

function Theme.list(parent, gap, horizontal)
	local layout = Theme.new("UIListLayout", { SortOrder = Enum.SortOrder.LayoutOrder, Padding = UDim.new(0, gap or Theme.SPACE.sm) }, parent)
	if horizontal then
		layout.FillDirection = Enum.FillDirection.Horizontal
		layout.VerticalAlignment = Enum.VerticalAlignment.Center
	end
	return layout
end

function Theme.stroke(parent, token, thickness)
	local stroke = Theme.new("UIStroke", { Thickness = thickness or 1, ApplyStrokeMode = Enum.ApplyStrokeMode.Border }, parent)
	Theme.paint(stroke, { Color = token or "border" })
	strokeBase[stroke] = 0
	return stroke
end

function Theme.corner(parent, radius)
	local value = if type(radius) == "number" then radius else (Theme.RADIUS[radius] or Theme.RADIUS.control)
	return Theme.new("UICorner", { CornerRadius = UDim.new(0, value) }, parent)
end

function Theme.spacer(parent, height)
	return Theme.new("Frame", { Name = "Spacer", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, height or Theme.SPACE.sm) }, parent)
end

function Theme.label(parent, text, options)
	local config = options or {}
	local size = config.size or "md"
	local textSize = if type(size) == "number" then size else (Theme.TEXT[size] or Theme.TEXT.md)
	local height = config.height
	local truncate = config.truncate == true
	local label = Theme.new("TextLabel", { Name = config.name or "Label", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, height or 0), AutomaticSize = if height then Enum.AutomaticSize.None else Enum.AutomaticSize.Y,
		Text = if type(text) == "string" then text else "", TextSize = textSize, RichText = false,
		TextWrapped = not truncate and config.wrap ~= false,
		TextTruncate = if truncate then Enum.TextTruncate.AtEnd else Enum.TextTruncate.None,
		TextXAlignment = alignment(config.align),
		TextYAlignment = if height then Enum.TextYAlignment.Center else Enum.TextYAlignment.Top }, parent)
	label.Font = Theme.font(config.font)
	Theme.paint(label, { TextColor3 = config.token or "text" })
	return label
end

-- Câmpul text (o linie sau mai multe): fundal `bg` în dark și `surface` în light, contur cu inel de focus.
local function textBox(parent, options, multiline)
	local config = options or {}
	local height = config.height or (if multiline then 72 else 32)
	local box = Theme.new("TextBox", { Name = config.name or (if multiline then "Textarea" else "Input"),
		Size = UDim2.new(1, 0, 0, height), BorderSizePixel = 0, Text = "", PlaceholderText = config.placeholder or "",
		TextSize = Theme.TEXT.md, RichText = false, ClearTextOnFocus = false, MultiLine = multiline == true,
		TextWrapped = multiline == true, TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = if multiline then Enum.TextYAlignment.Top else Enum.TextYAlignment.Center }, parent)
	box.Font = if config.mono then Theme.font("mono") else Theme.font("body")
	Theme.paint(box, { BackgroundColor3 = { dark = "bg", light = "surface" }, TextColor3 = "text", PlaceholderColor3 = "text2" })
	Theme.corner(box, "control")
	Theme.padding(box, 10, if multiline then 8 else 0)
	Theme.focusRing(box, Theme.stroke(box, "borderInput"))
	return box
end

function Theme.input(parent, options)
	return textBox(parent, options, false)
end

function Theme.textarea(parent, options)
	return textBox(parent, options, true)
end

function Theme.scroll(parent, name)
	local scroll = Theme.new("ScrollingFrame", { Name = name or "Scroll", Size = UDim2.fromScale(1, 1),
		BackgroundTransparency = 1, BorderSizePixel = 0, CanvasSize = UDim2.fromOffset(0, 0),
		AutomaticCanvasSize = Enum.AutomaticSize.Y, ScrollingDirection = Enum.ScrollingDirection.Y,
		ScrollBarThickness = 4 }, parent)
	Theme.paint(scroll, { ScrollBarImageColor3 = "text3" })
	return scroll
end

-- 6. Runtime: componente --------------------------------------------------------------------------------------------

-- Card: container acționabil cu șină de stare pe stânga (§7.1).
function Theme.card(parent, options)
	local config = options or {}
	local role = config.role or "neutral"
	local padding = config.padding or Theme.SPACE.md
	local clickable = config.clickable == true
	local root
	if clickable then
		root = Theme.new("TextButton", { Name = config.name or "Card", Text = "", RichText = false, AutoButtonColor = false, Selectable = true,
			Size = UDim2.new(1, 0, 0, 0), AutomaticSize = Enum.AutomaticSize.Y, BorderSizePixel = 0, ClipsDescendants = true }, parent)
		root.TextSize = Theme.TEXT.md
		root.Font = Theme.font("body")
	else
		root = Theme.new("Frame", { Name = config.name or "Card", Size = UDim2.new(1, 0, 0, 0),
			AutomaticSize = Enum.AutomaticSize.Y, BorderSizePixel = 0, ClipsDescendants = true }, parent)
	end
	root:SetAttribute("Component", "card")
	Theme.paint(root, { BackgroundColor3 = "surface" })
	Theme.corner(root, "card")
	local stroke = Theme.stroke(root, "border")
	local rail = Theme.new("Frame", { Name = "Rail", Size = UDim2.new(0, 3, 1, 0), BorderSizePixel = 0, ZIndex = 2 }, root)
	rail:SetAttribute("Component", "rail")
	local body = Theme.new("Frame", { Name = "Body", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y }, root)
	Theme.new("UIPadding", { PaddingLeft = UDim.new(0, padding + 3), PaddingRight = UDim.new(0, padding),
		PaddingTop = UDim.new(0, padding), PaddingBottom = UDim.new(0, padding) }, body)
	Theme.list(body, Theme.SPACE.xs + 2)
	local card = { root = root, rail = rail, body = body }
	function card.setRole(newRole)
		Theme.recolor(rail, newRole or "neutral")
		root:SetAttribute("Role", newRole or "neutral")
	end
	card.setRole(role)
	if clickable then
		Theme.hover(root, { normal = "surface", stroke = stroke, strokeNormal = "border", strokeHover = "borderStrong" })
	end
	return card
end

-- Chip: etichetă scurtă, statică (provider, tip, stare, contor) — nu este buton și nu are hover (§7.2).
function Theme.chip(parent, text, role)
	local chip = Theme.new("TextLabel", { Name = "Chip", Text = if type(text) == "string" then text else "",
		Size = UDim2.new(0, 0, 0, 20), AutomaticSize = Enum.AutomaticSize.X, BorderSizePixel = 0,
		TextSize = Theme.TEXT.xs, RichText = false, TextWrapped = false,
		TextXAlignment = Enum.TextXAlignment.Center, TextYAlignment = Enum.TextYAlignment.Center }, parent)
	chip.Font = Theme.font("ui")
	chip:SetAttribute("Component", "chip")
	Theme.corner(chip, "control")
	Theme.new("UIPadding", { PaddingLeft = UDim.new(0, Theme.SPACE.sm), PaddingRight = UDim.new(0, Theme.SPACE.sm) }, chip)
	Theme.recolor(chip, role or "neutral")
	return chip
end

-- Punct de stare (§7.3): stă mereu lângă o etichetă text, niciodată singur.
function Theme.dot(parent, role, size)
	local pixels = size or 8
	local dot = Theme.new("Frame", { Name = "Dot", Size = UDim2.fromOffset(pixels, pixels), BorderSizePixel = 0 }, parent)
	dot:SetAttribute("Component", "dot")
	Theme.corner(dot, "pill")
	Theme.recolor(dot, role or "neutral")
	return dot
end

-- Avatar rotund cu inițialele dedesubt: dacă imaginea nu se încarcă, inițialele rămân vizibile (§7.4).
function Theme.avatar(parent, identity, options)
	local config = options or {}
	local size = config.size or 24
	local name = if type(identity) == "table" and type(identity.name) == "string" then identity.name else ""
	local root = Theme.new("Frame", { Name = "Avatar_" .. (if name ~= "" then name else "necunoscut"),
		Size = UDim2.fromOffset(size, size), BorderSizePixel = 0 }, parent)
	Theme.paint(root, { BackgroundColor3 = "raised" })
	Theme.corner(root, "pill")
	local initials = Theme.new("TextLabel", { Name = "Initials", BackgroundTransparency = 1, Size = UDim2.fromScale(1, 1),
		Text = Theme.initials(name), TextSize = math.max(9, math.floor(size * 0.42)), RichText = false,
		TextXAlignment = Enum.TextXAlignment.Center, TextYAlignment = Enum.TextYAlignment.Center }, root)
	initials.Font = Theme.font("ui")
	Theme.paint(initials, { TextColor3 = "text2" })
	local url = if type(identity) == "table" and type(identity.avatar) == "string" and identity.avatar ~= "" then identity.avatar
		else Theme.avatarUrl(if type(identity) == "table" then tonumber(identity.userId) else nil)
	if url then
		local image = Theme.new("ImageLabel", { Name = "Image", BackgroundTransparency = 1, Size = UDim2.fromScale(1, 1),
			Image = url, ScaleType = Enum.ScaleType.Crop, ZIndex = 2 }, root)
		Theme.corner(image, "pill")
	end
	if config.ring then
		local ring = Theme.stroke(root, "surface", 2)
		ring.ZIndex = 3
	end
	if config.offline then
		root.BackgroundTransparency = 0.45
		initials.TextTransparency = 0.45
		local image = root:FindFirstChild("Image")
		if image then image.ImageTransparency = 0.45 end
	end
	return root
end

-- Rândul de prezență: avatare suprapuse cu 6 px („tu” primul) și numele de lângă (§7.4).
function Theme.avatarRow(parent, members, options)
	local config = options or {}
	local size = config.size or 24
	local max = config.max or 6
	local root = Theme.new("Frame", { Name = config.name or "AvatarRow", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, size) }, parent)
	local stack = Theme.new("Frame", { Name = "Stack", BackgroundTransparency = 1, Size = UDim2.new(0, 0, 1, 0),
		AutomaticSize = Enum.AutomaticSize.X }, root)
	local layout = Theme.list(stack, 0, true)
	layout.Padding = UDim.new(0, -6)
	local names = Theme.label(root, "", { size = "sm", token = "text2", height = size, truncate = true, name = "Names" })
	local avatars = {}
	local row = { root = root, stack = stack, names = names }
	local function order(list)
		local sorted = {}
		for _, member in ipairs(list) do
			if type(member) == "table" then table.insert(sorted, member) end
		end
		table.sort(sorted, function(a, b)
			if (a.me == true) ~= (b.me == true) then return a.me == true end
			local onlineA, onlineB = a.online ~= false, b.online ~= false
			if onlineA ~= onlineB then return onlineA end
			-- `lastSeen` este momentul absolut al ultimei prezențe (epoch, ca în `Store.memberFrom`): cei mai recenți primii.
			local seenA = tonumber(a.lastSeen) or -math.huge
			local seenB = tonumber(b.lastSeen) or -math.huge
			if seenA ~= seenB then return seenA > seenB end
			return tostring(a.name or "") < tostring(b.name or "")
		end)
		return sorted
	end
	function row.set(list)
		local sorted = order(if type(list) == "table" then list else {})
		local used = {}
		local shown = 0
		for index, member in ipairs(sorted) do
			if index > max then break end
			local key = if type(member.deviceId) == "string" then member.deviceId else "user:" .. tostring(member.userId)
			local avatar = avatars[key]
			if not avatar then
				avatar = Theme.avatar(stack, member, { size = size, ring = true, offline = member.online == false })
				avatars[key] = avatar
			end
			avatar.LayoutOrder = index
			used[key] = true
			shown = index
		end
		for key, avatar in pairs(avatars) do
			if not used[key] then
				avatar:Destroy()
				avatars[key] = nil
			end
		end
		-- Lățimea avatarelor suprapuse: primul întreg, restul câte `size - 6` (§1.2).
		local width = if shown > 0 then size + (shown - 1) * (size - 6) else 0
		local extra = math.max(0, #sorted - shown)
		local more = root:FindFirstChild("More")
		if extra > 0 then
			if not more then
				more = Theme.chip(root, "", "neutral")
				more.Name = "More"
			end
			more.Text = "+" .. extra
			more.Position = UDim2.new(0, width + Theme.SPACE.xs, 0, math.floor((size - 20) / 2))
			width += MORE_WIDTH
		elseif more then
			more:Destroy()
		end
		local labels = {}
		for index, member in ipairs(sorted) do
			if index > 3 then break end
			table.insert(labels, if member.me == true then "tu" else Theme.fit(tostring(member.name or "?"), 16))
		end
		local text = table.concat(labels, ", ")
		-- Contorul apare o singură dată pe fiecare ecran (§7.4): în plugin secțiunea Workspace nu are chip de contor, deci
		-- numărul stă aici; în panou îl poartă chip-ul secțiunii „Prezenți”, iar rândul de nume rămâne doar cu numele.
		if #sorted > 0 then
			local counted = Theme.countLabel(#sorted, "prezent", "prezenți")
			text = if text ~= "" then text .. " · " .. counted else counted
		end
		names.Text = text
		local offset = width + (if width > 0 then Theme.SPACE.sm else 0)
		names.Position = UDim2.new(0, offset, 0, 0)
		names.Size = UDim2.new(1, -offset, 0, size)
	end
	row.set(members)
	return row
end

-- Buton: patru feluri, o singură formă (§7.5). Activarea se leagă din view cu `Theme.click`.
function Theme.button(parent, text, options)
	local config = options or {}
	local kind = config.kind or "secondary"
	local size = config.size or "md"
	local height = BUTTON_HEIGHT[size] or BUTTON_HEIGHT.md
	local width = config.width
	-- Trunchierea și padding-ul lateral vin din regula pură de mai sus, ca să fie verificabile fără Studio.
	local fixed = if typeof(width) == "UDim" and width.Scale == 0 then width.Offset else nil
	local truncate, pad = Theme.buttonText(width == "auto", fixed)
	local button = Theme.new("TextButton", { Name = config.name or "Button",
		Size = if typeof(width) == "UDim" then UDim2.new(width.Scale, width.Offset, 0, height) else UDim2.new(1, 0, 0, height),
		AutomaticSize = if width == "auto" then Enum.AutomaticSize.X else Enum.AutomaticSize.None,
		BorderSizePixel = 0, Text = if type(text) == "string" then text else "",
		TextSize = Theme.TEXT[BUTTON_TEXT[size] or "md"], RichText = false, TextWrapped = false,
		TextTruncate = if truncate then Enum.TextTruncate.AtEnd else Enum.TextTruncate.None,
		AutoButtonColor = false, Selectable = true, TextXAlignment = Enum.TextXAlignment.Center }, parent)
	if width == "auto" then button.Size = UDim2.new(0, 0, 0, height) end
	button.Font = Theme.font("ui")
	button:SetAttribute("Component", "button")
	button:SetAttribute("Kind", kind)
	Theme.corner(button, "control")
	Theme.new("UIPadding", { PaddingLeft = UDim.new(0, pad), PaddingRight = UDim.new(0, pad) }, button)
	if kind == "primary" then
		Theme.paint(button, { BackgroundColor3 = "primary", TextColor3 = "onPrimary" })
		local stroke = Theme.stroke(button, "primaryMark")
		Theme.hover(button, { normal = "primary", hover = "primaryHover", pressed = "primaryActive", stroke = stroke, strokeNormal = "primaryMark" })
	elseif kind == "ghost" then
		button.BackgroundTransparency = 1
		Theme.paint(button, { BackgroundColor3 = "raised", TextColor3 = "text2" })
		Theme.hover(button, { normal = "raised", fadeNormal = 1, fadeHover = 0, textNormal = "text2", textHover = "text" })
	elseif kind == "danger" then
		Theme.paint(button, { BackgroundColor3 = "dangerSoft", TextColor3 = "dangerText" })
		local stroke = Theme.stroke(button, "dangerMark")
		stroke.Transparency = 0.6
		strokeBase[stroke] = 0.6
		Theme.hover(button, { normal = "dangerSoft", hover = "dangerMark", fadeNormal = 0, fadeHover = 0.8, stroke = stroke, strokeNormal = "dangerMark" })
	else
		Theme.paint(button, { BackgroundColor3 = "raised", TextColor3 = "text" })
		local stroke = Theme.stroke(button, "border")
		-- Hover-ul secundar folosește `bg` în dark (mai închis) și `border` în light (mai închis): schimbarea se vede în ambele teme.
		Theme.hover(button, { normal = "raised", hover = { dark = "bg", light = "border" }, stroke = stroke,
			strokeNormal = "border", strokeHover = "borderStrong" })
	end
	Theme.enabled(button, true)
	return button
end

-- Control cu două-trei valori (segmented) sau file de ecran (tab): aceeași formă, altă decorație.
local function choiceGroup(parent, items, value, onChange, mode)
	local count = math.max(1, #items)
	local height = if mode == "tab" then 34 else 32
	local root = Theme.new("Frame", { Name = if mode == "tab" then "Tabs" else "Segmented",
		Size = UDim2.new(1, 0, 0, height), BorderSizePixel = 0 }, parent)
	if mode == "tab" then
		root.BackgroundTransparency = 1
	else
		Theme.paint(root, { BackgroundColor3 = "raised" })
		Theme.corner(root, "control")
		Theme.stroke(root, "border")
		Theme.padding(root, 2, 2)
	end
	Theme.list(root, 0, true)
	local group = { root = root, buttons = {}, value = value }
	local underlines = {}
	local function repaintItems()
		for key, button in pairs(group.buttons) do
			local selected = key == group.value
			if mode == "tab" then
				Theme.paint(button, { TextColor3 = if selected then "text" else "text2" })
				local underline = underlines[key]
				if underline then underline.Visible = selected end
			else
				button.BackgroundTransparency = if selected then 0 else 1
				Theme.paint(button, { BackgroundColor3 = "surface", TextColor3 = if selected then "text" else "text2" })
				local stroke = button:FindFirstChildWhichIsA("UIStroke")
				if stroke then stroke.Transparency = if selected then 0 else 1 end
			end
		end
	end
	for index, item in ipairs(items) do
		local button = Theme.new("TextButton", { Name = "Item_" .. tostring(item.key), LayoutOrder = index,
			Size = UDim2.new(1 / count, 0, 1, 0), BorderSizePixel = 0, BackgroundTransparency = 1,
			Text = if type(item.text) == "string" then item.text else tostring(item.key),
			TextSize = Theme.TEXT.md, RichText = false, TextWrapped = false, AutoButtonColor = false, Selectable = true }, root)
		button.Font = Theme.font("ui")
		if mode == "tab" then
			local underline = Theme.new("Frame", { Name = "Underline", BorderSizePixel = 0, Visible = false,
				AnchorPoint = Vector2.new(0, 1), Position = UDim2.new(0, 0, 1, 0), Size = UDim2.new(1, 0, 0, 2) }, button)
			underline:SetAttribute("Component", "underline")
			Theme.recolor(underline, "primary")
			underlines[item.key] = underline
		else
			Theme.corner(button, "control")
			local stroke = Theme.stroke(button, "borderStrong")
			stroke.Transparency = 1
			strokeBase[stroke] = 0
		end
		button.Activated:Connect(function()
			if button:GetAttribute("EnabledAction") == false then return end
			if group.value == item.key then return end
			group.value = item.key
			repaintItems()
			if onChange then onChange(item.key) end
		end)
		group.buttons[item.key] = button
	end
	function group.set(newValue)
		group.value = newValue
		repaintItems()
	end
	repaintItems()
	return group
end

function Theme.segmented(parent, items, value, onChange)
	return choiceGroup(parent, items, value, onChange, "segmented")
end

function Theme.tab(parent, items, value, onChange)
	return choiceGroup(parent, items, value, onChange, "tab")
end

-- Rând de listă fără chenar: text principal stânga, meta dreapta, acțiune opțională la capăt (§7.7).
function Theme.row(parent, options)
	local config = options or {}
	local lines = config.lines or 1
	local leadKind = config.lead
	local leadWidth = if leadKind == "avatar" then 24 + Theme.SPACE.sm elseif leadKind == "dot" then 8 + Theme.SPACE.xs else 0
	local actionWidth = if config.action then 96 else 0
	local metaWidth = if lines >= 2 then 0 else 96
	local height = if lines >= 2 then 44 else 32
	local root = Theme.new("Frame", { Name = config.name or "Row", BorderSizePixel = 0,
		Size = UDim2.new(1, 0, 0, height) }, parent)
	Theme.paint(root, { BackgroundColor3 = "raised" })
	root.BackgroundTransparency = 1
	Theme.corner(root, "control")
	local content = Theme.new("Frame", { Name = "Content", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 1, 0) }, root)
	Theme.new("UIPadding", { PaddingLeft = UDim.new(0, Theme.SPACE.md), PaddingRight = UDim.new(0, Theme.SPACE.md),
		PaddingTop = UDim.new(0, Theme.SPACE.xs + 2), PaddingBottom = UDim.new(0, Theme.SPACE.xs + 2) }, content)
	local row = { root = root }
	if leadKind == "avatar" then
		row.lead = Theme.avatar(content, config.identity, { size = 24 })
		row.lead.Position = UDim2.new(0, 0, 0.5, -12)
	elseif leadKind == "dot" then
		row.lead = Theme.dot(content, config.role or "neutral", 8)
		row.lead.Position = UDim2.new(0, 0, 0.5, -4)
	end
	local primaryWidth = -(leadWidth + metaWidth + actionWidth)
	row.primary = Theme.label(content, config.primary or "", { size = "sm", token = "text", truncate = true,
		font = if config.mono then "mono" else "body", height = if lines >= 2 then 18 else nil, name = "Primary" })
	-- Rândul are dimensiuni date (nu AutomaticSize): textul care nu încape se trunchiază la capăt.
	row.primary.AutomaticSize = Enum.AutomaticSize.None
	row.primary.Position = UDim2.new(0, leadWidth, 0, 0)
	row.primary.Size = UDim2.new(1, primaryWidth, if lines >= 2 then 0 else 1, if lines >= 2 then 18 else 0)
	row.primary.TextYAlignment = Enum.TextYAlignment.Center
	row.secondary = Theme.label(content, config.secondary or "", { size = "xs", token = "text2", truncate = true,
		align = if lines >= 2 then "left" else "right", name = "Secondary" })
	row.secondary.AutomaticSize = Enum.AutomaticSize.None
	if lines >= 2 then
		row.secondary.Position = UDim2.new(0, leadWidth, 0, 20)
		row.secondary.Size = UDim2.new(1, primaryWidth, 0, 16)
	else
		row.secondary.Position = UDim2.new(1, -(metaWidth + actionWidth), 0, 0)
		row.secondary.Size = UDim2.new(0, metaWidth, 1, 0)
	end
	row.secondary.TextYAlignment = Enum.TextYAlignment.Center
	if config.action then
		-- Butonul primește padding mic (vezi `Theme.button`: sub 64 px lățime), ca eticheta să încapă la 320 px lățime de dock.
		row.action = Theme.button(content, config.action.text or "", { kind = config.action.kind or "ghost", size = "sm",
			name = "Action", width = UDim.new(0, actionWidth - Theme.SPACE.sm) })
		row.action.AnchorPoint = Vector2.new(1, 0.5)
		row.action.Position = UDim2.new(1, 0, 0.5, 0)
	end
	-- Rândul acționabil primește un buton transparent peste tot rândul; hover-ul aprinde fundalul `raised`.
	if config.clickable then
		local button = Theme.new("TextButton", { Name = "Hit", BackgroundTransparency = 1, Text = "", RichText = false, Size = UDim2.new(1, 0, 1, 0),
			AutoButtonColor = false, Selectable = true, ZIndex = 0 }, root)
		row.button = button
		button.MouseEnter:Connect(function()
			if button:GetAttribute("EnabledAction") ~= false then Theme.tween(root, { BackgroundTransparency = 0 }) end
		end)
		button.MouseLeave:Connect(function() Theme.tween(root, { BackgroundTransparency = 1 }) end)
	end
	function row.set(primary, secondary)
		if type(primary) == "string" and row.primary.Text ~= primary then row.primary.Text = primary end
		if type(secondary) == "string" and row.secondary.Text ~= secondary then row.secondary.Text = secondary end
	end
	return row
end

-- Starea goală a unei secțiuni: ce va apărea aici și ce poate face utilizatorul acum (§7.8, texte în §12).
function Theme.empty(parent, text, options)
	local config = options or {}
	local root = Theme.new("Frame", { Name = config.name or "Empty", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0), AutomaticSize = Enum.AutomaticSize.Y }, parent)
	Theme.padding(root, 0, Theme.SPACE.xs)
	Theme.list(root, Theme.SPACE.xs)
	local empty = { root = root }
	empty.label = Theme.label(root, text or "", { size = "sm", token = "text2", name = "Text" })
	empty.label.LayoutOrder = 1
	if config.hint then
		-- `text2`, nu `text3`: tokenul al treilea rămâne decorativ (sub 4,5:1 pe toate suprafețele, §2.5).
		empty.hint = Theme.label(root, config.hint, { size = "xs", token = "text2", name = "Hint" })
		empty.hint.LayoutOrder = 2
	end
	if config.action then
		empty.action = Theme.button(root, config.action.text or "", { kind = "ghost", size = "sm", width = "auto", name = "Action" })
		empty.action.LayoutOrder = 3
	end
	return empty
end

-- Banner de ecran cu șină de rol (§7.9); `show(role, text, seconds)` repictează și, opțional, se ascunde singur.
function Theme.banner(parent, options)
	local config = options or {}
	local root = Theme.new("Frame", { Name = config.name or "Banner", Size = UDim2.new(1, 0, 0, 0), BorderSizePixel = 0,
		AutomaticSize = Enum.AutomaticSize.Y, ClipsDescendants = true, Visible = false }, parent)
	root:SetAttribute("Component", "banner")
	Theme.corner(root, "card")
	local rail = Theme.new("Frame", { Name = "Rail", Size = UDim2.new(0, 3, 1, 0), BorderSizePixel = 0, ZIndex = 2 }, root)
	rail:SetAttribute("Component", "rail")
	local body = Theme.new("Frame", { Name = "Body", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y }, root)
	-- Cu buton de închidere, corpul se retrage din dreapta cu lățimea lui plus spațiile: altfel prima linie trece pe sub „×”.
	local rightPad = Theme.bannerPad(config.close)
	Theme.new("UIPadding", { PaddingLeft = UDim.new(0, Theme.SPACE.md + 3), PaddingRight = UDim.new(0, rightPad),
		PaddingTop = UDim.new(0, Theme.SPACE.md), PaddingBottom = UDim.new(0, Theme.SPACE.md) }, body)
	Theme.list(body, Theme.SPACE.sm)
	local label = Theme.label(body, "", { size = "sm", name = "Text" })
	label:SetAttribute("Component", "roleText")
	local banner = { root = root, rail = rail, label = label, visible = false }
	if config.action then
		banner.action = Theme.button(body, config.action.text or "", { kind = "ghost", size = "sm", width = "auto", name = "Action" })
		banner.action.LayoutOrder = 2
	end
	if config.close then
		banner.close = Theme.button(root, "×", { kind = "ghost", size = "sm", name = "Close", width = UDim.new(0, CLOSE_WIDTH) })
		banner.close.AnchorPoint = Vector2.new(1, 0)
		banner.close.Position = UDim2.new(1, -Theme.SPACE.xs, 0, Theme.SPACE.xs)
		banner.close.ZIndex = 3
	end
	local generation = 0
	local role = config.role or "neutral"
	local function setRole(newRole)
		role = newRole or "neutral"
		Theme.recolor(root, role)
		Theme.recolor(rail, role)
		Theme.recolor(label, role)
	end
	setRole(role)
	function banner.show(newRole, text, seconds)
		generation += 1
		local mine = generation
		setRole(newRole)
		label.Text = if type(text) == "string" then text else ""
		root.Visible = true
		banner.visible = true
		if type(seconds) == "number" and seconds > 0 then
			task.delay(seconds, function()
				if generation == mine then banner.hide() end
			end)
		end
	end
	function banner.hide()
		generation += 1
		root.Visible = false
		banner.visible = false
	end
	return banner
end

-- Secțiune: titlu 16/600, contor opțional, subtitlu opțional, corp cu pas 8; varianta pliabilă are „▾”/„▸” (§7.10).
function Theme.section(parent, title, options)
	local config = options or {}
	local collapsible = config.collapsible == true
	local root = Theme.new("Frame", { Name = config.name or "Section", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 0), AutomaticSize = Enum.AutomaticSize.Y }, parent)
	Theme.list(root, Theme.SPACE.sm)
	local header
	if collapsible then
		header = Theme.new("TextButton", { Name = "Header", BackgroundTransparency = 1, Text = "", RichText = false, Size = UDim2.new(1, 0, 0, 24),
			AutoButtonColor = false, Selectable = true, LayoutOrder = 1 }, root)
	else
		header = Theme.new("Frame", { Name = "Header", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 24), LayoutOrder = 1 }, root)
	end
	local titleLabel = Theme.label(header, title or "", { size = "lg", font = "ui", height = 24, truncate = true, name = "Title" })
	titleLabel.Size = UDim2.new(1, -40, 1, 0)
	local count = Theme.chip(header, "", "neutral")
	count.Name = "Count"
	count.AnchorPoint = Vector2.new(1, 0.5)
	count.Position = UDim2.new(1, 0, 0.5, 0)
	count.Visible = false
	local body = Theme.new("Frame", { Name = "Body", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y, LayoutOrder = 3 }, root)
	Theme.list(body, Theme.SPACE.sm)
	if collapsible then
		Theme.paint(body, { BackgroundColor3 = "surface" })
		body.BackgroundTransparency = 0
		Theme.corner(body, "card")
		Theme.stroke(body, "borderStrong")
		Theme.padding(body, Theme.SPACE.md)
	end
	local section = { root = root, header = header, title = titleLabel, count = count, body = body, open = config.open == true }
	local subtitleLabel = nil
	function section.setCount(n)
		if type(n) == "number" then
			count.Text = tostring(math.floor(n))
			count.Visible = true
		else
			count.Visible = false
		end
	end
	function section.subtitle(text)
		if type(text) == "string" and text ~= "" then
			if not subtitleLabel then
				subtitleLabel = Theme.label(root, text, { size = "xs", token = "text2", height = 16, name = "Subtitle" })
				subtitleLabel.LayoutOrder = 2
				section.subtitleLabel = subtitleLabel
			end
			subtitleLabel.Text = text
			subtitleLabel.Visible = true
		elseif subtitleLabel then
			subtitleLabel.Visible = false
		end
	end
	function section.setOpen(open)
		section.open = open == true
		body.Visible = section.open
		if collapsible then titleLabel.Text = (if section.open then "▾ " else "▸ ") .. (title or "") end
	end
	section.setOpen(if collapsible then config.open == true else true)
	-- Antetul pliabil se comportă ca un buton ghost: titlul se aprinde la hover, fără fundal.
	if collapsible then
		Theme.paint(titleLabel, { TextColor3 = "text2" })
		Theme.enabled(header, true)
		header.MouseEnter:Connect(function() Theme.paint(titleLabel, { TextColor3 = "text" }) end)
		header.MouseLeave:Connect(function() Theme.paint(titleLabel, { TextColor3 = "text2" }) end)
	end
	return section
end

-- Bulă din fluxul unei sesiuni (§7.13); `set` schimbă textul fără să recreeze instanța (scroll-ul nu sare).
function Theme.bubble(parent, message, provider)
	local data = if type(message) == "table" then message else {}
	local kind = if type(data.kind) == "string" and data.kind ~= "" then data.kind else "text"
	local role = Theme.messageRole(kind, provider)
	local user = kind == "text"
	local root = Theme.new("Frame", { Name = "Bubble", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y }, parent)
	local box = Theme.new("Frame", { Name = "Box", BackgroundTransparency = 1, BorderSizePixel = 0,
		Size = if user then UDim2.new(0.85, 0, 0, 0) else UDim2.new(1, 0, 0, 0),
		AnchorPoint = if user then Vector2.new(1, 0) else Vector2.new(0, 0),
		Position = if user then UDim2.fromScale(1, 0) else UDim2.fromScale(0, 0),
		AutomaticSize = Enum.AutomaticSize.Y }, root)
	local rail = nil
	local leftPadding = 0
	if user then
		Theme.paint(box, { BackgroundColor3 = "raised" })
		box.BackgroundTransparency = 0
		Theme.corner(box, "card")
		Theme.padding(box, Theme.SPACE.sm, Theme.SPACE.sm)
	elseif kind ~= "assistant" then
		rail = Theme.new("Frame", { Name = "Rail", Size = UDim2.new(0, 3, 1, 0), BorderSizePixel = 0, ZIndex = 2 }, box)
		rail:SetAttribute("Component", "rail")
		leftPadding = Theme.SPACE.sm + 3
		Theme.new("UIPadding", { PaddingLeft = UDim.new(0, leftPadding) }, box)
	end
	local content = Theme.new("Frame", { Name = "Content", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y }, box)
	Theme.list(content, 2)
	local author = Theme.label(content, "", { size = "xs", font = "ui", name = "Author" })
	author:SetAttribute("Component", "roleText")
	author.LayoutOrder = 1
	local bodySize = if kind == "text" or kind == "assistant" then "md" else "sm"
	local bodyToken = if kind == "error" then "dangerText" elseif kind == "text" or kind == "assistant" then "text" else "text2"
	local body = Theme.label(content, "", { size = bodySize, token = bodyToken, name = "Body" })
	body.LayoutOrder = 2
	local bubble = { root = root, box = box, rail = rail, author = author, body = body }
	function bubble.set(newMessage)
		local fields = if type(newMessage) == "table" then newMessage else {}
		local newKind = if type(fields.kind) == "string" and fields.kind ~= "" then fields.kind else kind
		local newRole = Theme.messageRole(newKind, provider)
		if newRole ~= role then
			role = newRole
			if rail then Theme.recolor(rail, role) end
			Theme.recolor(author, role)
		end
		local authorText = if type(fields.author) == "string" then fields.author else ""
		if author.Text ~= authorText then author.Text = authorText end
		author.Visible = authorText ~= ""
		local text = if type(fields.text) == "string" then fields.text else ""
		if body.Text ~= text then body.Text = text end
	end
	if rail then Theme.recolor(rail, role) end
	Theme.recolor(author, role)
	bubble.set(data)
	return bubble
end

return Theme
