-- Studio Harness Loader 1.1.0. Pornește aplicația din `Modules` și o înlocuiește fără repornirea Studio-ului când daemon-ul livrează un bundle nou (faza 5).
-- 1.0: codul local vine din StringValue-ul `LocalToken` (copil al acestui script, completat la instalare), este salvat în setări și ajunge la aplicație
-- prin handoff-ul de pornire; fără el rămâne câmpul manual din „Avansat”. Logica de decizie este pură și testată în tests/Loader.spec.luau;
-- în afara Studio scriptul întoarce doar tabelul Loader.
local Loader = { VERSION = "1.1.0", BASE = "http://127.0.0.1:34871", TOKEN_SETTING = "StudioHarnessBridgeToken", CHECK_INTERVAL = 5,
	MAX_MODULES = 64, MAX_SOURCE = 2 * 1024 * 1024, TOKEN_VALUE = "LocalToken", TOKEN_PLACEHOLDER = "STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER" }

local function validRevision(revision)
	return type(revision) == "string" and revision ~= "" and #revision <= 64 and string.find(revision, "^[%w%.%-_]+$") ~= nil
end

-- Un cod local acceptabil: 8–512 caractere, fără spații, caractere de control sau `\`, diferit de placeholder-ul din pachet.
function Loader.validToken(token)
	return type(token) == "string" and #token >= 8 and #token <= 512 and string.find(token, "[%c%s\\]") == nil and token ~= Loader.TOKEN_PLACEHOLDER
end

-- Codul livrat la instalare: StringValue-ul `LocalToken` de sub scriptul loader; lipsă, altă clasă, placeholder sau valoare invalidă → nil.
function Loader.localToken(container)
	local ok, value = pcall(function()
		local child = container:FindFirstChild(Loader.TOKEN_VALUE)
		if child == nil or child.ClassName ~= "StringValue" then return nil end
		return child.Value
	end)
	return if ok and Loader.validToken(value) then value else nil
end

-- Codul cu care pornește aplicația și sursa lui: cel livrat de instalare are prioritate față de setarea salvată anterior (fallback 1.0.0/manual).
function Loader.resolveToken(delivered, saved)
	if Loader.validToken(delivered) then return delivered, "loader" end
	if Loader.validToken(saved) then return saved, "settings" end
	return "", nil
end

-- Decide dacă `/v1/status.plugin_bundle.revision` cere o metamorfoză; `skipRevision` este ultimul bundle care nu a pornit.
function Loader.shouldSwap(currentRevision, status, skipRevision)
	local bundle = if type(status) == "table" then status.plugin_bundle else nil
	local revision = if type(bundle) == "table" then bundle.revision else nil
	if not validRevision(revision) or revision == currentRevision or revision == skipRevision then return false, nil end
	return true, revision
end

-- Validează răspunsul `/v1/plugin/bundle`: revizie, module cu nume sigure și surse text, intrarea prezentă. Întoarce ok și numele intrării sau motivul.
function Loader.validateBundle(bundle)
	if type(bundle) ~= "table" then return false, "răspuns lipsă" end
	if not validRevision(bundle.revision) then return false, "revizie invalidă" end
	if type(bundle.modules) ~= "table" then return false, "modulele lipsesc" end
	local entry = if bundle.entry == nil then "Main" else bundle.entry
	if type(entry) ~= "string" or not string.find(entry, "^[%a_][%w_]*$") then return false, "intrare invalidă" end
	local count = 0
	for name, source in pairs(bundle.modules) do
		count += 1
		if count > Loader.MAX_MODULES then return false, "prea multe module" end
		if type(name) ~= "string" or #name > 64 or not string.find(name, "^[%a_][%w_]*$") then return false, "nume de modul invalid" end
		if type(source) ~= "string" or #source > Loader.MAX_SOURCE or not string.find(source, "%S") then return false, "sursă invalidă pentru " .. name end
	end
	if type(bundle.modules[entry]) ~= "string" then return false, "intrarea " .. entry .. " lipsește" end
	return true, entry
end

function Loader.normalize(source)
	return (string.gsub(tostring(source), "\r\n?", "\n"))
end

-- Bundle-ul încorporat nu are revizie; dacă sursele coincid cu cele servite, revizia se adoptă fără metamorfoză.
function Loader.sameSources(bundle, folder)
	local ok, same = pcall(function()
		local count = 0
		for _, child in ipairs(folder:GetChildren()) do
			if child.ClassName == "ModuleScript" then
				count += 1
				local source = bundle.modules[child.Name]
				if type(source) ~= "string" or Loader.normalize(child.Source) ~= Loader.normalize(source) then return false end
			end
		end
		local expected = 0
		for _ in pairs(bundle.modules) do expected += 1 end
		return count == expected
	end)
	return ok and same == true
end

-- Folderul „App-<revizie>” cu câte un ModuleScript per modul (Source = sursă); `create` înlocuiește Instance.new în teste.
function Loader.buildFolder(bundle, parent, create)
	create = create or function(className) return Instance.new(className) end
	local folder = create("Folder")
	folder.Name = "App-" .. tostring(bundle.revision)
	local names = {}
	for name in pairs(bundle.modules) do table.insert(names, name) end
	table.sort(names)
	for _, name in ipairs(names) do
		local module = create("ModuleScript")
		module.Name = name
		module.Source = bundle.modules[name]
		module.Parent = folder
	end
	folder.Parent = parent
	return folder
end

-- Handoff-ul cu care repornește bundle-ul anterior după o metamorfoză eșuată: istoricul aplicației (când snapshotul a reușit),
-- plus motivul eșecului, ca `Main` să îl mute în store, iar dock-ul să arate bannerul din DESIGN.md §12.2 — nu doar Output-ul.
function Loader.retryHandoff(handoff, revision, failure)
	local retry = if type(handoff) == "table" then table.clone(handoff) else {}
	retry.bundleError = tostring(revision) .. ": " .. tostring(failure)
	return retry
end

if not plugin then return Loader end

local HttpService = game:GetService("HttpService")
local root = script.Parent
local embedded = root:FindFirstChild("Modules")
local toolbar = plugin:CreateToolbar("Studio Harness")
local button = toolbar:CreateButton("StudioHarnessMatrixToggle", "Deschide hubul sesiunilor", "", "Hub")
button.ClickableWhenViewportHidden = true
local apps = Instance.new("Folder")
apps.Name, apps.Parent = "Apps", root
local current = { revision = nil, folder = embedded, app = nil }
local alive, failedRevision = true, nil

local function startApp(folder, handoff)
	local main = if folder then folder:FindFirstChild("Main") else nil
	if not main then error("modulul Main lipsește din " .. (if folder then folder.Name else "bundle")) end
	return require(main).start(plugin, handoff, toolbar, button)
end

-- Întoarce (a pornit?, motivul eșecului): motivul merge în handoff-ul repornirii, ca utilizatorul să îl vadă în bannerul din dock,
-- nu doar în fereastra Output a Studio-ului (DESIGN.md §12.2).
local function boot(folder, handoff, label)
	local ok, app = pcall(startApp, folder, handoff)
	current.app = if ok then app else nil
	if not ok then warn("Studio Harness: " .. label .. " nu a pornit: " .. tostring(app)) end
	return ok, if ok then nil else tostring(app)
end

local function savedToken()
	local ok, token = pcall(function() return plugin:GetSetting(Loader.TOKEN_SETTING) end)
	return if ok and Loader.validToken(token) then token else ""
end

-- Codul livrat la instalare are prioritate și intră în setări (aplicația și verificarea bundle-ului îl citesc de acolo).
local delivered = Loader.localToken(script)
if delivered and delivered ~= savedToken() then
	local saved = pcall(function() plugin:SetSetting(Loader.TOKEN_SETTING, delivered) end)
	if not saved then warn("Studio Harness: codul local livrat nu a putut fi salvat în setări; se folosește doar în această sesiune.") end
end
local startToken, startSource = Loader.resolveToken(delivered, savedToken())

-- Setarea urmează ultimul cod care a mers (aplicația o rescrie după un cod lipit manual); codul livrat rămâne rezerva dacă setarea nu s-a putut scrie.
local function currentToken()
	local saved = savedToken()
	return if saved ~= "" then saved else startToken
end

-- Singurul loc din loader cu HttpService: GET cu codul local, în pcall; orice eșec întoarce nil.
local function get(path, token)
	local ok, response = pcall(function()
		return HttpService:RequestAsync({ Url = Loader.BASE .. path, Method = "GET", Headers = { ["X-Studio-Harness-Token"] = token } })
	end)
	if not ok or type(response) ~= "table" or response.Success ~= true then return nil end
	local decoded, body = pcall(function() return HttpService:JSONDecode(response.Body) end)
	return if decoded and type(body) == "table" and body.ok == true then body else nil
end

-- Metamorfoza: folderul nou se creează înainte de oprirea aplicației; la eșec revine bundle-ul anterior cu același handoff.
local function swap(bundle, revision)
	local built, folder = pcall(Loader.buildFolder, bundle, apps)
	if not built then warn("Studio Harness: bundle-ul " .. revision .. " nu a putut fi creat: " .. tostring(folder)); return false end
	local handoff = nil
	if current.app then
		local ok, snapshot = pcall(function() return current.app:snapshot() end)
		if ok then handoff = snapshot else warn("Studio Harness: snapshotul aplicației a eșuat, pornesc fără istoric: " .. tostring(snapshot)) end
		pcall(function() current.app:destroy() end)
		current.app = nil
	end
	local previous = current.folder
	local started, failure = boot(folder, handoff, "bundle-ul " .. revision)
	if started then
		current.revision, current.folder = revision, folder
		if previous and previous ~= embedded then previous:Destroy() end
		return true
	end
	folder:Destroy()
	boot(previous, Loader.retryHandoff(handoff, revision, failure), "bundle-ul anterior")
	return false
end

local function check()
	local token = currentToken()
	if token == "" then return end
	local swapNeeded, revision = Loader.shouldSwap(current.revision, get("/v1/status", token), failedRevision)
	if not swapNeeded then return end
	local bundle = get("/v1/plugin/bundle", token)
	local valid, message = Loader.validateBundle(bundle)
	if not valid then failedRevision = revision; warn("Studio Harness: bundle-ul " .. revision .. " este invalid: " .. message); return end
	if bundle.revision ~= revision or not alive then return end
	if current.revision == nil and current.app and Loader.sameSources(bundle, current.folder) then current.revision = revision; return end
	if not swap(bundle, revision) then failedRevision = revision end
end

plugin.Unloading:Connect(function()
	alive = false
	if current.app then pcall(function() current.app:destroy() end) end
	current.app = nil
end)
-- Pornirea încorporată primește un handoff fără store: doar codul local și sursa lui, ca aplicația să se conecteze fără intervenția utilizatorului.
boot(embedded, if startToken ~= "" then { controller = { token = startToken, source = startSource } } else nil, "aplicația încorporată")
task.spawn(function()
	while alive do
		task.wait(Loader.CHECK_INTERVAL)
		if not alive then break end
		local ok, failure = pcall(check)
		if not ok then warn("Studio Harness: verificarea bundle-ului a eșuat: " .. tostring(failure)) end
	end
end)
