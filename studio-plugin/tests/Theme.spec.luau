-- Teste pure pentru sistemul de design (design/DESIGN.md §8.10). Runnerul injectează modulul Theme;
-- nu se atinge nicio globală Roblox, deci spec-ul rulează în luau.exe (fără DataModel, fără Instance.new).
return function(Theme)
	local passed = 0
	local function test(name, run)
		local ok, message = pcall(run)
		assert(ok, name .. ": " .. tostring(message))
		passed += 1
	end
	local function round(value)
		return math.floor(value * 100 + 0.5) / 100
	end
	-- Cheile normative ale paletei (§2.5), în ordinea din document.
	local KEYS = {
		"bg", "surface", "raised", "border", "borderStrong", "borderInput", "text", "text2", "text3", "focus",
		"primary", "primaryText", "primaryMark", "primarySoft", "primaryHover", "primaryActive", "onPrimary", "completedMark",
		"info", "infoText", "infoMark", "infoSoft",
		"warning", "warningText", "warningMark", "warningSoft",
		"danger", "dangerText", "dangerMark", "dangerSoft",
		"claude", "claudeText", "claudeMark", "claudeSoft",
		"codex", "codexText", "codexMark", "codexSoft",
		"neutral", "neutralText", "neutralMark", "neutralSoft",
	}
	local MODES = { "dark", "light" }
	-- Contrastul unui token față de altul, în modul dat.
	local function ratio(mode, a, b)
		return Theme.contrastRatio(Theme.token(a, mode), Theme.token(b, mode))
	end
	local function atLeast(mode, a, b, minimum)
		local value = ratio(mode, a, b)
		assert(value >= minimum, mode .. ": " .. a .. " pe " .. b .. " = " .. round(value) .. " < " .. minimum)
	end

	test("Versiunea modulului este 1.0.0", function()
		assert(Theme.VERSION == "1.0.0")
	end)

	test("Paletele au exact cheile din §2.5, ca hex de 7 caractere", function()
		for _, mode in ipairs(MODES) do
			local palette = Theme.PALETTES[mode]
			assert(type(palette) == "table", "lipsește modul " .. mode)
			local expected = {}
			for _, key in ipairs(KEYS) do
				expected[key] = true
				local value = palette[key]
				assert(type(value) == "string" and string.match(value, "^#%x%x%x%x%x%x$"), mode .. "." .. key .. " nu este hex: " .. tostring(value))
			end
			for key in pairs(palette) do assert(expected[key], mode .. " are cheia în plus " .. key) end
			local count = 0
			for _ in pairs(palette) do count += 1 end
			assert(count == #KEYS, mode .. " are " .. count .. " chei, nu " .. #KEYS)
		end
	end)

	test("Contrast: textul principal și cel secundar trec 4,5:1 pe toate cele trei niveluri", function()
		for _, mode in ipairs(MODES) do
			for _, surface in ipairs({ "bg", "surface", "raised" }) do
				atLeast(mode, "text", surface, 4.5)
				atLeast(mode, "text2", surface, 4.5)
			end
		end
	end)

	test("Contrast: textul fiecărui rol trece 4,5:1 pe suprafață, pe ridicat și pe fundalul propriu", function()
		for _, mode in ipairs(MODES) do
			for _, role in ipairs(Theme.ROLES) do
				local tokens = Theme.roleTokens(role)
				atLeast(mode, tokens.text, "surface", 4.5)
				atLeast(mode, tokens.text, "raised", 4.5)
				atLeast(mode, tokens.text, tokens.soft, 4.5)
			end
		end
	end)

	test("Contrast: punctele, șinele, conturul câmpurilor și inelul de focus trec 3:1 pe suprafață", function()
		for _, mode in ipairs(MODES) do
			for _, role in ipairs(Theme.ROLES) do
				atLeast(mode, Theme.roleTokens(role).mark, "surface", 3)
			end
			atLeast(mode, "completedMark", "surface", 3)
			atLeast(mode, "neutralMark", "surface", 3)
			atLeast(mode, "borderInput", "surface", 3)
			atLeast(mode, "borderInput", "bg", 3)
			atLeast(mode, "focus", "surface", 3)
		end
	end)

	test("Contrast: textul butonului primar trece 4,5:1 pe toate cele trei stări", function()
		for _, mode in ipairs(MODES) do
			for _, state in ipairs({ "primary", "primaryHover", "primaryActive" }) do
				atLeast(mode, "onPrimary", state, 4.5)
			end
		end
	end)

	test("contrastRatio: alb pe negru este 21, o culoare pe ea însăși este 1", function()
		assert(round(Theme.contrastRatio("#FFFFFF", "#000000")) == 21)
		assert(round(Theme.contrastRatio("#4ADE80", "#4ADE80")) == 1)
		assert(round(Theme.contrastRatio("4ADE80", "#4ADE80")) == 1)
	end)

	test("token: valorile vin din modul cerut, iar un nume greșit este eroare", function()
		assert(Theme.token("bg", "dark") == "#0B0F14" and Theme.token("bg", "light") == "#F6F8FA")
		assert(Theme.token("onPrimary", "light") == "#06210F")
		assert(not pcall(Theme.token, "fundal", "dark"))
	end)

	test("resolveMode: doar „Dark” este întuneric", function()
		assert(Theme.resolveMode("Dark") == "dark")
		assert(Theme.resolveMode("dark") == "dark")
		assert(Theme.resolveMode("DARK") == "dark")
		assert(Theme.resolveMode("Light") == "light")
		assert(Theme.resolveMode(nil) == "light")
		assert(Theme.resolveMode("Altceva") == "light")
	end)

	test("roleTokens: fiecare rol întoarce patru token-uri existente în ambele palete", function()
		for _, role in ipairs(Theme.ROLES) do
			local tokens = Theme.roleTokens(role)
			for _, field in ipairs({ "fill", "text", "mark", "soft" }) do
				assert(type(tokens[field]) == "string", role .. " nu are " .. field)
				for _, mode in ipairs(MODES) do Theme.token(tokens[field], mode) end
			end
		end
		local completed = Theme.roleTokens("completed")
		assert(completed.fill == "primary" and completed.text == "text2" and completed.mark == "completedMark" and completed.soft == "neutralSoft")
		local unknown = Theme.roleTokens("inexistent")
		assert(unknown.mark == "neutralMark" and unknown.soft == "neutralSoft")
		assert(Theme.roleTokens(nil).text == "neutralText")
	end)

	test("elapsed: scara de timp cu un „acum” fix", function()
		local now = 1000000
		assert(Theme.elapsed(now, now) == "de 0 s")
		assert(Theme.elapsed(now - 59, now) == "de 59 s")
		assert(Theme.elapsed(now - 60, now) == "de 1 min")
		assert(Theme.elapsed(now - 3599, now) == "de 59 min")
		assert(Theme.elapsed(now - 3600, now) == "de 1 h")
		assert(Theme.elapsed(now - 86399, now) == "de 23 h")
		assert(Theme.elapsed(now - 86400, now) == "de 1 zi")
		assert(Theme.elapsed(now - 2 * 86400, now) == "de 2 zile")
		assert(Theme.elapsed(nil, now) == "")
		assert(Theme.elapsed(now + 30, now) == "de 0 s")
	end)

	test("clock: ora sau cratimele când nu există moment", function()
		assert(Theme.clock(nil) == "--:--")
		assert(Theme.clock("acum") == "--:--")
		assert(string.match(Theme.clock(0), "^%d%d:%d%d$") ~= nil)
	end)

	test("initials: una sau două majuscule, sigure pe UTF-8", function()
		assert(Theme.initials("ellob") == "EL")
		assert(Theme.initials("Ana Maria") == "AM")
		assert(Theme.initials("ana maria ionescu") == "AM")
		assert(Theme.initials("ș") == "Ș")
		assert(Theme.initials("ștefan popescu") == "ȘP")
		assert(Theme.initials("  ") == "?")
		assert(Theme.initials("") == "?")
		assert(Theme.initials(nil) == "?")
		assert(Theme.initials(12345) == "?")
	end)

	test("shortId: prefixul identificatorului, „—” când lipsește", function()
		assert(Theme.shortId("ab12cd34ef56") == "ab12cd34")
		assert(Theme.shortId("ab12cd34ef56", 12) == "ab12cd34ef56")
		assert(Theme.shortId("abc", 12) == "abc")
		assert(Theme.shortId("") == "—")
		assert(Theme.shortId(nil) == "—")
	end)

	test("avatarUrl: doar conturile cunoscute au imagine", function()
		assert(Theme.avatarUrl(12345) == "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48")
		assert(Theme.avatarUrl(12345, 100) == "rbxthumb://type=AvatarHeadShot&id=12345&w=100&h=100")
		assert(Theme.avatarUrl(12345, 37) == "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48")
		assert(Theme.avatarUrl(0) == nil)
		assert(Theme.avatarUrl(-5) == nil)
		assert(Theme.avatarUrl(nil) == nil)
	end)

	test("fit: scurtare cu „…”, fără să rupă un caracter multi-octet", function()
		assert(Theme.fit("abc", 3) == "abc")
		assert(Theme.fit("abcdef", 3) == "abc…")
		assert(Theme.fit("ăă", 1) == "…")
		assert(Theme.fit("ăă", 2) == "ă…")
		assert(Theme.fit("ăă", 4) == "ăă")
		assert(Theme.fit(nil, 5) == "")
		assert(Theme.fit("abc", 0) == "…")
	end)

	test("countLabel: pluralul românesc cu „de” de la 20", function()
		assert(Theme.countLabel(1, "sesiune", "sesiuni") == "1 sesiune")
		assert(Theme.countLabel(0, "sesiune", "sesiuni") == "0 sesiuni")
		assert(Theme.countLabel(3, "sesiune", "sesiuni") == "3 sesiuni")
		assert(Theme.countLabel(19, "sesiune", "sesiuni") == "19 sesiuni")
		assert(Theme.countLabel(20, "sesiune", "sesiuni") == "20 de sesiuni")
		assert(Theme.countLabel(21, "sesiune", "sesiuni") == "21 de sesiuni")
		assert(Theme.countLabel(101, "prezent", "prezenți") == "101 prezenți")
		assert(Theme.countLabel(100, "prezent", "prezenți") == "100 de prezenți")
	end)

	test("sessionState: fiecare stare din §6.1 are etichetă și rol", function()
		for key, label in pairs(Theme.states) do
			local stateKey, stateLabel, role = Theme.sessionState({ state = key, approvals = {} })
			assert(stateKey == key and stateLabel == label, key)
			assert(role == Theme.SESSION_ROLE[key], key .. " are rolul " .. tostring(role))
		end
		local key, label, role = Theme.sessionState({})
		assert(key == "draft" and label == "Draft" and role == "neutral")
	end)

	test("sessionState: aprobările, oprirea și eliberarea se văd peste starea curentă", function()
		local key, label, role = Theme.sessionState({ state = "running", approvals = { { id = "a1" } } })
		assert(key == "waiting_approval" and label == "Cere aprobare" and role == "warning")
		key, label = Theme.sessionState({ state = "queued", approvals = {}, pendingApproval = true })
		assert(key == "waiting_approval" and label == "Cere aprobare")
		key, label, role = Theme.sessionState({ state = "running", approvals = {}, cancelling = true })
		assert(key == "running" and label == "Se oprește" and role == "primary")
		key, label = Theme.sessionState({ state = "running", approvals = {}, releasing = true })
		assert(key == "running" and label == "Eliberez claims")
		key, label, role = Theme.sessionState({ state = "inventat", approvals = {} })
		assert(key == "inventat" and label == "inventat" and role == "neutral")
	end)

	test("messageRole: răspunsul agentului poartă culoarea providerului", function()
		assert(Theme.messageRole("assistant", "claude") == "claude")
		assert(Theme.messageRole("assistant", "codex") == "codex")
		assert(Theme.messageRole("assistant", nil) == "neutral")
		assert(Theme.messageRole("assistant", "altul") == "neutral")
		assert(Theme.messageRole("text") == "neutral")
		assert(Theme.messageRole("tool") == "info")
		assert(Theme.messageRole("claim") == "warning")
		assert(Theme.messageRole("approval") == "warning")
		assert(Theme.messageRole("error") == "danger")
		assert(Theme.messageRole("notice") == "neutral")
		assert(Theme.messageRole(nil) == "neutral")
		assert(Theme.messageRole("inventat") == "neutral")
	end)

	test("placeLine: locul și creatorul lui", function()
		local workspace = { key = "game:987654", name = "Ball", placeId = 1291603, gameId = 987654, creatorId = 555, creatorType = "User" }
		assert(Theme.placeLine(workspace, { userId = 555 }) == "place 1291603 · al tău")
		assert(Theme.placeLine(workspace, { userId = 1 }) == "place 1291603 · utilizator 555")
		assert(Theme.placeLine(workspace, nil) == "place 1291603 · utilizator 555")
		local group = { key = "game:88", placeId = 88120044, creatorId = 555, creatorType = "Group" }
		assert(Theme.placeLine(group, { userId = 1 }) == "place 88120044 · grup 555")
		assert(Theme.placeLine({ key = "game:1", placeId = 7, creatorId = 0 }, { userId = 1 }) == "place 7")
		assert(Theme.placeLine({ key = "local", name = "joc.rbxl" }, nil) == "Fișier nepublicat")
		assert(Theme.placeLine(nil, nil) == "Fișier nepublicat")
		assert(Theme.placeLine({ name = "fără cheie" }, nil) == "Fișier nepublicat")
	end)

	test("hubLine: textele complete din §6.2 și rolurile lor", function()
		local text, role = Theme.hubLine({ status = "approved" })
		assert(text == "Hub conectat" and role == "primary")
		text, role = Theme.hubLine({ status = "pending", deviceId = "ab12cd34ef56" })
		assert(text == "Așteaptă aprobarea adminului · dispozitiv ab12cd34" and role == "warning")
		text, role = Theme.hubLine({ status = "pending" })
		assert(text == "Așteaptă aprobarea adminului · dispozitiv —" and role == "warning")
		text, role = Theme.hubLine({ status = "offline", error = "timeout după 5 s." })
		assert(text == "Hub offline: timeout după 5 s. Sesiunile tale merg local; claims-urile sunt doar locale până revine." and role == "danger")
		text = Theme.hubLine({ status = "offline" })
		assert(string.find(text, "Hub offline: hub-ul nu răspunde. ", 1, true) == 1)
		text, role = Theme.hubLine({ status = "offline", error = Theme.OLD_HUB_ERROR })
		assert(text == "Hub-ul rulează o versiune mai veche. Actualizează hub-ul ca să apari în workspace." and role == "danger")
		text, role = Theme.hubLine({ status = "revoked" })
		assert(text == "Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou." and role == "danger")
		text, role = Theme.hubLine({ status = "disabled" })
		assert(text == "Hub dezactivat (daemon pornit cu --no-hub)." and role == "neutral")
		text, role = Theme.hubLine({ status = "connecting" })
		assert(text == "Se conectează la hub…" and role == "neutral")
		text, role = Theme.hubLine(nil)
		assert(text == "Se conectează la hub…" and role == "neutral")
	end)

	test("hubLine: motivul lung al stării offline se scurtează la 80 de octeți", function()
		local reason = string.rep("a", 200)
		local text = Theme.hubLine({ status = "offline", error = reason })
		local cut = string.match(text, "^Hub offline: (a+)%.")
		assert(cut ~= nil and #cut == 80, "motivul nu a fost scurtat la 80: " .. tostring(cut and #cut))
	end)

	test("Etichetele de stare și rolurile acoperă exact aceleași chei", function()
		for key in pairs(Theme.states) do assert(Theme.SESSION_ROLE[key], "lipsește rolul pentru " .. key) end
		for key in pairs(Theme.SESSION_ROLE) do assert(Theme.states[key], "lipsește eticheta pentru " .. key) end
		for key in pairs(Theme.hubStates) do assert(Theme.HUB_ROLE[key], "lipsește rolul hub pentru " .. key) end
		for key in pairs(Theme.HUB_ROLE) do assert(Theme.hubStates[key], "lipsește eticheta hub pentru " .. key) end
		for _, key in ipairs({ "text", "assistant", "tool", "claim", "approval", "error", "notice" }) do
			assert(Theme.MESSAGE_ROLE[key], "lipsește rolul mesajului " .. key)
		end
		-- Eticheta scurtă a hub-ului din antet și textul complet trebuie să fie consecvente ca rol.
		for key in pairs(Theme.hubStates) do
			local _, role = Theme.hubLine({ status = key, deviceId = "ab12cd34", error = "timeout" })
			assert(role == Theme.HUB_ROLE[key], key .. ": rolul " .. role .. " ≠ " .. Theme.HUB_ROLE[key])
		end
	end)

	test("buttonText: lățimea fixă trunchiază la capăt, „auto” urmează textul", function()
		-- „auto” își ia lățimea din text (AutomaticSize.X), deci nu are ce trunchia.
		local truncate, pad = Theme.buttonText(true, nil)
		assert(truncate == false and pad == Theme.SPACE.md)
		-- Lățimea implicită este toată lățimea containerului: „Studio țintă: <nume>” se taie la capăt cu „…”, nu la ambele capete.
		truncate, pad = Theme.buttonText(false, nil)
		assert(truncate == true and pad == Theme.SPACE.md)
		-- Acțiunea unui rând (96 − 8) și „×”-ul bannerului (28) sunt prea înguste pentru padding 12.
		truncate, pad = Theme.buttonText(false, 88)
		assert(truncate == true and pad == Theme.SPACE.xs)
		truncate, pad = Theme.buttonText(false, 28)
		assert(truncate == true and pad == Theme.SPACE.xs)
		truncate, pad = Theme.buttonText(false, 96)
		assert(truncate == true and pad == Theme.SPACE.md)
		truncate, pad = Theme.buttonText(false, 0)
		assert(truncate == true and pad == Theme.SPACE.md)
		truncate, pad = Theme.buttonText(false, "88")
		assert(truncate == true and pad == Theme.SPACE.md)
	end)

	test("bannerPad: „×” retrage corpul din dreapta cu lățimea lui plus spațiile", function()
		assert(Theme.bannerPad(false) == Theme.SPACE.md)
		assert(Theme.bannerPad(nil) == Theme.SPACE.md)
		-- 12 + 28 + 4: prima linie de text se oprește înainte de buton, nu trece pe sub el (§7.9).
		assert(Theme.bannerPad(true) == 44)
		assert(Theme.bannerPad(true) - Theme.bannerPad(false) >= 28)
	end)

	test("Scara de spațiere, razele și treptele de text sunt cele din §4 și §3.2", function()
		assert(Theme.SPACE.xs == 4 and Theme.SPACE.sm == 8 and Theme.SPACE.md == 12 and Theme.SPACE.lg == 16 and Theme.SPACE.xl == 24)
		assert(Theme.RADIUS.control == 6 and Theme.RADIUS.card == 10 and Theme.RADIUS.pill == 999)
		assert(Theme.TEXT.xs == 12 and Theme.TEXT.sm == 13 and Theme.TEXT.md == 14 and Theme.TEXT.lg == 16 and Theme.TEXT.xl == 20)
		assert(Theme.FONT_NAMES.ui == "GothamMedium" and Theme.FONT_NAMES.body == "Gotham" and Theme.FONT_NAMES.mono == "Code")
		assert(Theme.names.claude == "Claude Code" and Theme.names.codex == "Codex")
		assert(Theme.kinds.studio == "Studio" and Theme.kinds.terminal == "Terminal")
	end)

	test("Modulul nu a atins nicio globală Roblox la încărcare", function()
		assert(Theme.mode == "dark")
		assert(next(Theme.colors) == nil and next(Theme.fonts) == nil)
		assert(rawget(_G, "game") == nil)
	end)

	return passed
end
