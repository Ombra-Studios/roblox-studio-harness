-- Legătura pluginului cu daemon-ul local (singurul modul, împreună cu loader-ul, care folosește HttpService). 1.0: conectare automată cu codul livrat
-- de loader (reconectare cu backoff 1 → 15 s), identitatea Roblox (StudioService/Players) trimisă prin POST /v1/identity, „Panou” prin POST /v1/panel/open,
-- vizualizarea altor workspace-uri prin GET /v1/hub/workspace(s), polling (joburi 1 s, tablă 2 s, status 10 s, identitate 30 s) și inventarul proiectului.
-- Maparea răspunsurilor în store (applyStatus, syncBoard, applyWorkspaces, applyWorkspaceView) este a SessionStore-ului; aici stau doar cadența și erorile.
local HttpService = game:GetService("HttpService")
local Selection = game:GetService("Selection")
local Players = game:GetService("Players")
local Store = require(script.Parent.SessionStore)
local Scanner = require(script.Parent.ProjectScanner)
local Controller = {}
Controller.__index = Controller
local BASE = "http://127.0.0.1:34871"
local HOST = "127.0.0.1:34871"
local TOKEN_SETTING = "StudioHarnessBridgeToken"
local TOKEN_PLACEHOLDER = "STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER"
local AVATAR = "rbxthumb://type=AvatarHeadShot&id=%d&w=48&h=48"
local UNKNOWN_NAME = "Studio"
local OLD_DAEMON = "Daemon vechi sau lipsă: pornește daemon-ul 1.0 sau mai nou din terminalul cu pluginul Claude Code sau din Start-Daemon.cmd. Trimiterea este blocată."
local PROJECT_UNSUPPORTED = "daemon-ul nu primește inventarul (necesită 0.7)"
local SCAN_INTERVAL, SCAN_DEBOUNCE = 30, 5
local POLL_INTERVAL, BOARD_INTERVAL, STATUS_INTERVAL, IDENTITY_INTERVAL, VIEW_INTERVAL = 1, 2, 10, 30, 5
local RETRY_MIN, RETRY_MAX = 1, 15
local IDENTITY_PROPERTIES = { "PlaceId", "GameId", "Name" }

local function validToken(token)
	return type(token) == "string" and #token >= 8 and #token <= 512 and string.find(token, "[%c%s\\]") == nil and token ~= TOKEN_PLACEHOLDER
end

local function context()
	local selected = {}
	for _, object in ipairs(Selection:Get()) do
		if #selected >= 50 then break end
		local ok, path = pcall(function() return object:GetFullName() end)
		if ok then table.insert(selected, path) end
	end
	-- Forma documentată în STUDIO_BRIDGE_CONTRACT (fără creator): doar citirile locului trec prin helperul pur și protejat.
	local place = Scanner.place()
	return { place_id = place.place_id, game_id = place.game_id, place_name = place.place_name, selection = selected }
end

-- Id-ul contului din Studio (StudioService); indisponibil sau 0 → identitate necunoscută.
local function studioUserId()
	local ok, id = pcall(function() return game:GetService("StudioService"):GetUserId() end)
	return if ok and type(id) == "number" and id == id and id > 0 then math.floor(id) else 0
end

-- Corpul POST /v1/identity (contract §4.4): contul și jocul deschis; avatarul se derivă în daemon din user_id.
-- Locul și limitele câmpurilor vin din `Scanner` (helperi puri, testați în ProjectScanner.spec), ca antetul chunk-urilor și
-- identitatea să raporteze exact aceleași valori.
local function identityPayload(identity)
	return Scanner.identityPayload(Scanner.place(), identity.userId, identity.name)
end

function Controller.new(pluginObject, store, snapshot)
	local self = setmetatable({ plugin = pluginObject, store = store, alive = true, generation = 0,
		token = "", tokenOrigin = nil, tokenRejected = false, manualStop = false, retryDelay = RETRY_MIN, reconnect = nil,
		worker = nil, cancelWorker = nil, accountWorker = nil, queue = {}, urgent = {},
		nextPoll = 0, nextBoard = 0, nextStatus = 0, nextIdentity = 0, nextView = 0, failures = 0, remoteBusy = false, identity = nil, resolving = false, lastHub = nil, viewFailure = nil,
		project = nil, lastDigest = nil, nextScan = math.huge, pendingScan = nil, projectDisabled = false, connections = {} }, Controller)
	-- Orice modificare din scenă cere o rescanare la 5 s după ultimul eveniment; digestul neschimbat nu retrimite nimic.
	local function changed()
		if self.alive then self.pendingScan = os.clock() + SCAN_DEBOUNCE end
	end
	for _, signal in ipairs({ game.DescendantAdded, game.DescendantRemoving }) do
		local ok, connection = pcall(function() return signal:Connect(changed) end)
		if ok then table.insert(self.connections, connection) end
	end
	-- Schimbarea locului (PlaceId/GameId/Name) retrimite identitatea la următoarea iterație; daemon-ul recalculează workspace-ul.
	for _, property in ipairs(IDENTITY_PROPERTIES) do
		local ok, connection = pcall(function()
			return game:GetPropertyChangedSignal(property):Connect(function() if self.alive then self.nextIdentity = 0 end end)
		end)
		if ok then table.insert(self.connections, connection) end
	end
	-- Codul local: din snapshotul aplicației anterioare (metamorfoză) sau din handoff-ul loader-ului, altfel din setări; conectare fără intervenția utilizatorului.
	local token, source = "", nil
	if type(snapshot) == "table" then
		if validToken(snapshot.token) then token, source = snapshot.token, if type(snapshot.source) == "string" then snapshot.source else "settings" end
		self.manualStop = snapshot.manualStop == true
		local identity = snapshot.identity
		if type(identity) == "table" and type(identity.userId) == "number" and type(identity.name) == "string" then
			self.identity = { userId = identity.userId, name = identity.name, avatar = if type(identity.avatar) == "string" then identity.avatar else nil, resolved = identity.resolved == true }
		end
	end
	if token == "" then token = self:savedToken(); source = if token ~= "" then "settings" else nil end
	self.tokenOrigin = source
	self.accountWorker = task.spawn(function()
		self:account()
		if self.alive then self:publishIdentity() end
	end)
	if token ~= "" and not self.manualStop then self:connect(token, source) end
	return self
end

-- Starea legăturii pentru handoff: codul local (niciodată în store sau în serialize()), sursa lui, oprirea manuală și contul deja rezolvat.
function Controller:snapshot()
	local identity = self.identity
	return { token = if self.tokenRejected then "" else self.token, source = self.tokenOrigin, manualStop = self.manualStop,
		identity = if identity then { userId = identity.userId, name = identity.name, avatar = identity.avatar, resolved = identity.resolved } else nil }
end

function Controller:savedToken()
	local ok, token = pcall(function() return self.plugin:GetSetting(TOKEN_SETTING) end)
	return if ok and validToken(token) then token else ""
end

-- Adevărat doar după `stop()` (oprire manuală): atunci reconectarea automată este suspendată, deci UI-ul oferă „Conectează daemon-ul”.
function Controller:manualStopped()
	return self.manualStop == true
end

-- Sursa codului folosit: "loader" (livrat la instalare), "settings" (salvat anterior), "manual" (lipit în Avansat) sau nil.
function Controller:tokenSource()
	return self.tokenOrigin
end

-- Câmpul „Cod de asociere” din Avansat apare doar când nu există un cod valid sau daemon-ul l-a refuzat (401).
function Controller:needsToken()
	return self.tokenRejected or (self.token == "" and self:savedToken() == "")
end

function Controller:valid(generation)
	return self.alive and self.store.connected and self.generation == generation
end

function Controller:request(method, path, data, token)
	local headers = { ["X-Studio-Harness-Token"] = token }
	local options = { Url = BASE .. path, Method = method, Headers = headers }
	if method ~= "GET" then
		headers["Content-Type"] = "application/json"
		local ok, encoded = pcall(function() return if data == nil or next(data) == nil then "{}" else HttpService:JSONEncode(data) end)
		if not ok then return nil, "Cererea nu poate fi codificată JSON.", 400 end
		options.Body = encoded
	end
	local ok, response = pcall(function() return HttpService:RequestAsync(options) end)
	if not ok then return nil, "Daemon-ul local nu a răspuns. Verifică pornirea lui și permisiunea HTTP pentru 127.0.0.1.", nil end
	local decoded, body = pcall(function() return HttpService:JSONDecode(response.Body) end)
	if not decoded or type(body) ~= "table" then return nil, "Răspuns JSON invalid de la daemon.", response.StatusCode end
	if response.Success ~= true or body.ok ~= true then return nil, tostring(body.error or "Cererea a fost respinsă."), response.StatusCode end
	return body, nil, response.StatusCode
end

function Controller:status(text, isError)
	self.store.status, self.store.statusError = text, isError == true
	self.store:emit(nil)
end

-- Starea hub-ului raportată de daemon (connecting | pending | approved | offline | revoked | disabled) sau nil fără daemon.
function Controller:hubStatus()
	local hub = self.store.hub
	return if type(hub) == "table" and type(hub.status) == "string" then hub.status else nil
end

-- Contul Roblox din Studio, memorat per id; numele (Players, poate cere rețeaua) se reîncearcă la următorul apel dacă nu a putut fi citit.
function Controller:account()
	local userId = studioUserId()
	if userId == 0 then
		self.identity = { userId = 0, name = UNKNOWN_NAME, avatar = nil, resolved = true }
		return self.identity
	end
	local current = self.identity
	if current and current.userId == userId and current.resolved then return current end
	-- O singură interogare Players la un moment dat: al doilea apelant așteaptă rezultatul primului (cel mult 10 s).
	if self.resolving then
		local deadline = os.clock() + 10
		while self.resolving and os.clock() < deadline do task.wait(0.1) end
		current = self.identity
		if current and current.userId == userId and current.resolved then return current end
	end
	self.resolving = true
	local ok, name = pcall(function() return Players:GetNameFromUserIdAsync(userId) end)
	self.resolving = false
	local resolved = ok and type(name) == "string" and string.find(name, "%S") ~= nil
	self.identity = { userId = userId, name = if resolved then Store.prefix(name, 64) else "user_" .. userId, avatar = string.format(AVATAR, userId), resolved = resolved }
	return self.identity
end

-- Antetul arată contul și fără daemon: store.identity se completează local până când daemon-ul o confirmă prin status sau tablă.
function Controller:publishIdentity()
	local identity = self.identity
	if not identity or self.store.identity ~= nil then return end
	self.store.identity = { userId = identity.userId, name = identity.name, avatar = identity.avatar }
	self.store:emit(nil)
end

function Controller:applyStatus(data)
	local compatible = Store.isCompatible(data)
	self.store.compatible = compatible
	if compatible and self.store:remoteChanged(data.bridge_id) then
		self.queue, self.urgent = {}, {}
		self.nextBoard, self.nextIdentity, self.nextView = 0, 0, 0
		self:dropProject(true)
	end
	self.store:applyStatus(data)
	self:publishIdentity()
	-- O stare nouă a hub-ului (ex. pending → approved) aduce imediat tabla și vizualizarea altui workspace.
	local hub = self:hubStatus()
	if hub ~= self.lastHub then self.lastHub, self.nextBoard, self.nextView = hub, 0, 0 end
	self.remoteBusy = type(data.active_job_id) == "string" or (tonumber(data.queued_count) or 0) > 0
	self:status(if compatible then "Daemon · " .. HOST else OLD_DAEMON, not compatible)
end

-- Reconectarea automată după un eșec: 1, 2, 4, 8, apoi 15 s; se resetează la prima stare citită cu succes.
function Controller:scheduleReconnect()
	local delay = self.retryDelay
	self.retryDelay = math.min(delay * 2, RETRY_MAX)
	if self.reconnect then pcall(task.cancel, self.reconnect) end
	self.reconnect = task.delay(delay, function()
		self.reconnect = nil
		if self.alive and not self.store.connected and not self.manualStop then self:connect("") end
	end)
	return delay
end

-- „Deconectează daemon-ul” din Avansat: oprire cerută de utilizator, fără reconectare automată (revine doar prin `connect`).
function Controller:stop()
	self:disconnect(nil)
end

-- Fără mesaj = oprire cerută de utilizator (fără reconectare); cu mesaj = eșec, urmat de reconectare automată cu backoff.
function Controller:disconnect(message, code)
	self.generation += 1
	self.store.connected, self.store.connecting = false, false
	self.store:resetUpdate()
	self.store:resetProject()
	self.store:resetHub()
	self.viewFailure = nil
	self:dropProject(false)
	self.queue, self.urgent = {}, {}
	local worker, urgent = self.worker, self.cancelWorker
	self.worker, self.cancelWorker = nil, nil
	if worker and worker ~= coroutine.running() then pcall(task.cancel, worker) end
	if urgent and urgent ~= coroutine.running() then pcall(task.cancel, urgent) end
	for _, window in pairs(self.store.windows) do
		window.cancelling, window.decisionId, window.closeAfterCancel, window.releasing = false, nil, false, false
		if window.request then
			window.request.state, window.state = "uncertain", "uncertain"
			if window.request.message then window.request.message.author = "Tu · neconfirmat" end
		end
	end
	self.manualStop = message == nil
	if self.manualStop then
		if self.reconnect then pcall(task.cancel, self.reconnect); self.reconnect = nil end
		self:status("Daemon deconectat la cerere. Joburile deja trimise pot continua; nu sunt retrimise automat.")
		return
	end
	if code == 401 then self.tokenRejected, self.retryDelay = true, RETRY_MAX end
	if not self.alive then return end
	local delay = self:scheduleReconnect()
	self:status("Daemon · nu răspunde (reîncerc în " .. delay .. " s): " .. tostring(message), true)
end

-- `token` gol → codul curent sau cel din setări; un cod lipit manual devine sursa "manual". Conectarea pornește firul de polling.
function Controller:connect(token, source)
	if not self.alive or self.store.connected then return end
	token = string.match(tostring(token or ""), "^%s*(.-)%s*$") or ""
	local typed = token ~= ""
	if not typed then token = if self.token ~= "" then self.token else self:savedToken() end
	if not validToken(token) then
		self:status(if typed then "Introdu codul local al daemon-ului, fără spații. Nu parola contului."
			else "Lipsește codul local: rulează Install-Studio-Plugin.cmd sau lipește codul din daemon.log în Avansat.", true)
		return
	end
	if self.reconnect then pcall(task.cancel, self.reconnect); self.reconnect = nil end
	self.generation += 1
	self.token = token
	self.tokenOrigin = if typed then "manual" else (source or self.tokenOrigin or "settings")
	self.tokenRejected, self.manualStop = false, false
	self.store.connected, self.store.connecting = true, true
	-- Ultima tablă rămâne valabilă până sosește una nouă.
	self.failures, self.nextPoll, self.nextBoard, self.nextIdentity, self.nextView = 0, 0, 0, 0, 0
	self:status("Daemon · se conectează…")
	local generation = self.generation
	self.worker = task.defer(function()
		local success, message = pcall(function() self:loop(generation) end)
		if not success and self:valid(generation) then self:disconnect("Controllerul s-a oprit: " .. tostring(message)) end
	end)
end

function Controller:pollFailure(message, code)
	self.failures += 1
	if code == 401 or code == 403 or self.failures >= 3 then self:disconnect(message, code)
	else self:status("Legătura locală este întreruptă temporar; verific din nou…", true) end
end

function Controller:loop(generation)
	if not self:valid(generation) then return end
	local data, message, code = self:request("GET", "/v1/status", nil, self.token)
	if not self:valid(generation) then return end
	if not data then self:disconnect(message, code); return end
	self.store.connecting = false
	self.retryDelay = RETRY_MIN
	self.nextStatus = os.clock() + STATUS_INTERVAL
	pcall(function() self.plugin:SetSetting(TOKEN_SETTING, self.token) end)
	self:applyStatus(data)
	self:dropProject(true)
	while self:valid(generation) do
		local action = table.remove(self.queue, 1)
		local jobs = self.store:jobs()
		local now = os.clock()
		if action then
			if not action.bridgeId or action.bridgeId == self.store.bridgeId then
				action.run(generation)
			end
		elseif #jobs > 0 and self.store.compatible and now >= self.nextPoll then
			for offset = 1, #jobs, 16 do
				local batch = {}
				for index = offset, math.min(offset + 15, #jobs) do table.insert(batch, jobs[index]) end
				local result, errorMessage, resultCode = self:request("POST", "/v1/jobs/poll", { jobs = batch }, self.token)
				if not self:valid(generation) then return end
				if result then
					if type(result.bridge_id) ~= "string" or result.bridge_id == "" then
						self:pollFailure("Daemon-ul nu a identificat originea evenimentelor.", nil)
						break
					end
					self.failures = 0
					if self.store:remoteChanged(result.bridge_id) then
						self.queue, self.urgent = {}, {}
						self.nextStatus, self.nextBoard, self.nextIdentity = 0, 0, 0
						self:dropProject(true)
						break
					end
					for _, snapshot in ipairs(if type(result.jobs) == "table" then result.jobs else {}) do
						if type(snapshot) == "table" then self.store:applyEvents(snapshot) end
					end
				elseif resultCode == 404 then
					self.store.compatible = false
					self:status(OLD_DAEMON, true)
					break
				else self:pollFailure(errorMessage, resultCode); break end
			end
			self.nextPoll = os.clock() + POLL_INTERVAL
		elseif self.store.compatible and now >= self.nextBoard then
			local result, errorMessage, resultCode = self:request("GET", "/v1/board", nil, self.token)
			if not self:valid(generation) then return end
			self.nextBoard = os.clock() + BOARD_INTERVAL
			if result then
				self.failures = 0
				if type(result.bridge_id) == "string" and self.store:remoteChanged(result.bridge_id) then
					self.queue, self.urgent = {}, {}
					self.nextStatus, self.nextBoard, self.nextIdentity = 0, 0, 0
					self:dropProject(true)
				else
					self.store:syncBoard(result)
					self:publishIdentity()
				end
			elseif resultCode == 404 then
				self.store.compatible = false
				self:status(OLD_DAEMON, true)
			else self:pollFailure(errorMessage, resultCode) end
		elseif now >= self.nextStatus then
			local result, errorMessage, resultCode = self:request("GET", "/v1/status", nil, self.token)
			if not self:valid(generation) then return end
			self.nextStatus = os.clock() + STATUS_INTERVAL
			if result then self.failures = 0; self:applyStatus(result)
			else self:pollFailure(errorMessage, resultCode) end
		elseif self.store.compatible and now >= self.nextIdentity then self:sendIdentity(generation)
		elseif self.store.compatible and self.store.viewWorkspace ~= nil and now >= self.nextView then self:fetchView(generation)
		-- Inventarul: cel mult un chunk pe iterație, după poll/board/status, ca evenimentele să nu aștepte.
		elseif self.store.compatible and self.project then self:sendProjectChunk(generation)
		elseif self.store.compatible and self:scanDue() then self:scanProject()
		else task.wait(0.15) end
	end
end

-- POST /v1/identity la conectare, la 30 s și la schimbarea locului; tabla următoare aduce workspace-ul și identitatea confirmate de daemon.
function Controller:sendIdentity(generation)
	self.nextIdentity = os.clock() + IDENTITY_INTERVAL
	local identity = self:account()
	if not self:valid(generation) then return end
	self:publishIdentity()
	local response, message, code = self:request("POST", "/v1/identity", identityPayload(identity), self.token)
	if not self:valid(generation) then return end
	if response then
		self.failures = 0
		self.nextBoard = 0
	elseif code == 401 or code == 403 then self:disconnect(message, code)
	elseif code ~= nil and code >= 400 and code < 500 then self:status("Identitatea nu a fost acceptată de daemon: " .. tostring(message), true)
	else self:pollFailure(message, code) end
end

-- Modul de aprobare a operațiilor cerute de agent. Daemon-ul îl păstrează în `config.json`, deci ține și după repornire.
function Controller:setAutoApprove(mode)
	if mode ~= "ask" and mode ~= "edits" and mode ~= "all" then return false end
	if not self.store.connected or self.store.connecting or not self.store.compatible then
		self:status("Aprobarea se schimbă prin daemon: pornește-l și așteaptă conectarea.", true)
		return false
	end
	local previous = self.store.autoApprove
	-- Arătăm imediat noua stare; dacă daemon-ul refuză, revenim la cea veche și spunem de ce.
	self.store.autoApprove = mode
	self.store:emit(nil)
	table.insert(self.queue, 1, { bridgeId = self.store.bridgeId, run = function(generation)
		local response, message, code = self:request("POST", "/v1/settings", { auto_approve = mode }, self.token)
		if not self:valid(generation) then return end
		if response then return end
		self.store.autoApprove = previous
		self.store:emit(nil)
		if code == 401 or code == 403 then self:disconnect(message, code) else self:status("Aprobarea nu s-a schimbat: " .. tostring(message), true) end
	end })
	return true
end

-- Butonul „Panou”: daemon-ul deschide browserul la panoul hub-ului (adresa cu tokenul dispozitivului nu trece prin plugin).
function Controller:openPanel()
	if not self.store.connected or self.store.connecting or not self.store.compatible then
		self:status("Panoul se deschide prin daemon: pornește-l și așteaptă conectarea.", true)
		return false
	end
	if self:hubStatus() == "disabled" then
		self:status("Hub dezactivat (daemon pornit cu --no-hub): panoul nu este disponibil.", true)
		return false
	end
	table.insert(self.queue, 1, { bridgeId = self.store.bridgeId, run = function(generation)
		local response, message, code = self:request("POST", "/v1/panel/open", {}, self.token)
		if not self:valid(generation) then return end
		if response then return end
		if code == 401 or code == 403 then self:disconnect(message, code) else self:status("Panoul nu s-a deschis: " .. tostring(message), true) end
	end })
	return true
end

-- „Alte workspace-uri”: privește un alt workspace (read-only, `store.viewWorkspace`); nil sau cheia jocului deschis revin la workspace-ul curent.
function Controller:viewWorkspace(key)
	if not self.store:setViewWorkspace(key) then return false end
	self.nextView = 0
	self:setViewFailure(nil)
	return true
end

-- Motivul pentru care vizualizarea workspace-ului privit nu a putut fi citită (text în română) sau nil; HubView îl arată în locul listei.
function Controller:viewError()
	return self.viewFailure
end

function Controller:setViewFailure(message)
	if self.viewFailure == message then return end
	self.viewFailure = message
	self.store:emit(nil)
end

-- GET /v1/hub/workspace?key= la 5 s cât timp vizualizarea e deschisă; fără hub aprobat nu se face nicio cerere, doar se explică starea.
function Controller:fetchView(generation)
	local key = self.store.viewWorkspace
	self.nextView = os.clock() + VIEW_INTERVAL
	local hub = self:hubStatus()
	if hub ~= "approved" then
		self:setViewFailure("Hub-ul nu este disponibil (stare: " .. tostring(hub or "necunoscută") .. ").")
		return
	end
	local response, message, code = self:request("GET", "/v1/hub/workspace?key=" .. HttpService:UrlEncode(key), nil, self.token)
	if not self:valid(generation) or self.store.viewWorkspace ~= key then return end
	if response then
		self.failures = 0
		self.viewFailure = nil
		self.store:applyWorkspaceView(key, response)
	elseif code == 401 or code == 403 then self:disconnect(message, code)
	else self:setViewFailure(tostring(message)) end
end

-- Lista „Alte workspace-uri” la cerere (GET /v1/hub/workspaces); statusul și tabla o împrospătează oricum la 10 s / 2 s.
function Controller:refreshWorkspaces()
	if not self.store.connected or self.store.connecting or not self.store.compatible or self:hubStatus() ~= "approved" then return false end
	table.insert(self.queue, { bridgeId = self.store.bridgeId, run = function(generation)
		local response, message, code = self:request("GET", "/v1/hub/workspaces", nil, self.token)
		if not self:valid(generation) then return end
		if response then
			self.store:applyWorkspaces(response.workspaces)
			self.store:emit(nil)
		elseif code == 401 or code == 403 then self:disconnect(message, code)
		else self:status("Lista workspace-urilor nu a putut fi citită: " .. tostring(message), true) end
	end })
	return true
end

function Controller:dropProject(rescan)
	self.project, self.lastDigest, self.pendingScan, self.projectDisabled = nil, nil, nil, false
	self.nextScan = if rescan then 0 else math.huge
end

function Controller:scanDue()
	if self.projectDisabled then return false end
	local now = os.clock()
	return now >= self.nextScan or (self.pendingScan ~= nil and now >= self.pendingScan)
end

function Controller:scanProject()
	self.nextScan, self.pendingScan = os.clock() + SCAN_INTERVAL, nil
	local ok, result = pcall(Scanner.scan)
	if not ok then self.store:setProject("error", { message = "scanarea a eșuat: " .. tostring(result) }); return end
	if result.digest == self.lastDigest then return end
	self.lastDigest = result.digest
	local chunks = Scanner.chunks(result.nodes, Scanner.CHUNK_BYTES, function(value) return HttpService:JSONEncode(value) end)
	self.project = { id = HttpService:GenerateGUID(false), chunks = chunks, index = 1, truncated = result.truncated }
	self.store:setProject("sending", { snapshotId = self.project.id, count = result.count, truncated = result.truncated, message = "" })
end

-- Un chunk al inventarului (contract §8.5): antetul poartă și jocul/creatorul, ca daemon-ul să lege proiectul de workspace.
function Controller:sendProjectChunk(generation)
	local project = self.project
	local payload = Scanner.chunkPayload(project, Scanner.place(), self.store.defaultStudioId)
	local response, message, code = self:request("POST", "/v1/project/chunks", payload, self.token)
	if not self:valid(generation) or self.project ~= project then return end
	if response then
		self.failures = 0
		project.index += 1
		if project.index > #project.chunks then
			self.project = nil
			self.store:setProject("sent", { sentAt = os.time() })
		end
		return
	end
	-- Snapshotul este abandonat; fără digest, următorul ciclu de 30 s îl retrimite (404: daemon fără inventar, nu mai încercăm).
	self.project, self.lastDigest = nil, nil
	if code == 404 then self.projectDisabled, message = true, PROJECT_UNSUPPORTED end
	self.store:setProject("error", { message = tostring(message) })
	if code == 401 or code == 403 then self:disconnect(message, code) end
end

function Controller:createWindow(provider, studio, name)
	local id = HttpService:GenerateGUID(false)
	local window = self.store:create(id, provider or self.store.defaultProvider, studio or self.store:studio(self.store.defaultStudioId), name)
	if not window then self:status("Limita este de 32 de ferestre. Închide o sesiune inactivă înainte de a crea alta.", true) end
	return window
end

function Controller:setDefaultStudio(studioId)
	if not self.store:setDefaultStudio(studioId) then return end
	if not self.store.connected or self.store.connecting or not studioId then return end
	table.insert(self.queue, { bridgeId = self.store.bridgeId, run = function(generation)
		local response, message, code = self:request("POST", "/v1/default-studio", { studio_id = studioId }, self.token)
		if not self:valid(generation) then return end
		if not response then
			if code == 401 or code == 403 then self:disconnect(message, code) else self:status("Studio-ul țintă nu a fost confirmat de daemon: " .. tostring(message), true) end
		end
	end })
end

function Controller:sendRequest(window, request)
	local windowId, requestId = window.id, request.id
	table.insert(self.queue, { bridgeId = request.bridgeId, run = function(generation)
		local current = self.store.windows[windowId]
		if not current or current.request ~= request then return end
		local response, message, code = self:request("POST", "/v1/chat", request.payload, self.token)
		if not self:valid(generation) then return end
		if response and self.store:acceptRequest(windowId, requestId, response) then
			self.nextPoll = 0
			local live = self.store.windows[windowId]
			if live and live.closeAfterCancel and not self:cancel(windowId, true) then live.closeAfterCancel = false end
		else
			local rejected = code == 400 or code == 401 or code == 403 or code == 404 or code == 409 or code == 410 or code == 413 or code == 429
			self.store:requestFailed(windowId, requestId, tostring(message or "Răspunsul nu identifică un job valid."), not rejected or response ~= nil)
			if code == 401 or code == 403 then self:disconnect(message, code) end
		end
	end })
end

function Controller:send(id)
	local request, message = self.store:beginRequest(id, HttpService:GenerateGUID(false), context())
	if not request then
		local window = self.store.windows[id]
		if window then self.store:message(window, "Nu am trimis", message, "error"); self.store:emit(id) end
		return
	end
	self:sendRequest(self.store.windows[id], request)
end

function Controller:canRetry(window)
	local request = window and window.request
	return request ~= nil and request.state == "uncertain" and self.store.connected and not self.store.connecting
		and self.store.compatible and request.bridgeId == self.store.bridgeId and self.store:providerAvailable(window.provider)
end

function Controller:retry(id)
	local window = self.store.windows[id]
	local request = window and window.request
	if not self:canRetry(window) then return end
	request.state, window.state = "sending", "sending"
	self.store:emit(id)
	self:sendRequest(window, request)
end

function Controller:decide(id, approvalId, allow)
	local window = self.store.windows[id]
	local approval = window and window.approvals[1]
	if not approval or approval.id ~= approvalId then return end
	if approval.inactive then
		if not allow then self.store:removeApproval(id, approval.jobId, approval.id); self.store:emit(id) end
		return
	end
	if not self.store.connected or window.remote or window.cancelling or window.decisionId or (allow and approval.blocked) then return end
	window.decisionId = approvalId
	self.store:emit(id)
	local jobId, bridgeId = approval.jobId, self.store.bridgeId
	table.insert(self.queue, 1, { bridgeId = bridgeId, run = function(generation)
		local current = self.store.windows[id]
		if not current or not current.job or current.job.id ~= jobId then return end
		local response, message, code = self:request("POST", "/v1/jobs/" .. HttpService:UrlEncode(jobId) .. "/approve", { approval_id = approvalId, allow = allow }, self.token)
		if not self:valid(generation) then return end
		current = self.store.windows[id]
		if not current then return end
		if current.decisionId == approvalId then current.decisionId = nil end
		if response or code == 409 then
			self.store:removeApproval(id, jobId, approvalId)
			self.store:message(current, if code == 409 then "Aprobare expirată" else "Decizia ta",
				if code == 409 then tostring(message or "Apelul nu mai este activ.") else (if allow then "Aprobat: " else "Refuzat: ") .. tostring(approval.tool), "notice")
		else self.store:message(current, "Decizie neconfirmată", tostring(message) .. "\nNu este retrimisă automat.", "error") end
		self.nextPoll = 0
		self.store:emit(id)
		if code == 401 or code == 403 then self:disconnect(message, code) end
	end })
end

function Controller:releaseClaims(jobId, paths)
	if not self.store.connected or self.store.connecting or type(jobId) ~= "string" or jobId == "" then return false end
	local window = self.store:findJob(jobId)
	if window then
		-- Daemon-ul răspunde 403 pentru joburile altui dispozitiv; nu trimitem deloc.
		if window.remote then return false end
		if window.releasing then return true end
		window.releasing = true
		self.store:emit(window.id)
	end
	local body = if type(paths) == "table" and #paths > 0 then { paths = paths } else {}
	table.insert(self.queue, { bridgeId = self.store.bridgeId, run = function(generation)
		local response, message, code = self:request("POST", "/v1/jobs/" .. HttpService:UrlEncode(jobId) .. "/release", body, self.token)
		if not self:valid(generation) then return end
		local current = self.store:findJob(jobId)
		if current then
			current.releasing = false
			self.store:message(current, if response then "Claims" else "Eliberare neconfirmată",
				if response then "Eliberat: " .. (if body.paths then table.concat(body.paths, ", ") else "toate claims-urile acestei sesiuni") else tostring(message), if response then "claim" else "error")
			self.store:emit(current.id)
		elseif not response then self:status("Eliberarea nu a fost confirmată: " .. tostring(message), true) end
		self.nextBoard = 0
		if code == 401 or code == 403 then self:disconnect(message, code) end
	end })
	return true
end

function Controller:cancel(id, closeAfter)
	local window = self.store.windows[id]
	if not window or not window.job or window.remote or not self.store.connected then return false end
	if closeAfter then window.closeAfterCancel = true end
	if window.cancelling then return true end
	window.cancelling = true
	self.store:emit(id)
	table.insert(self.urgent, { windowId = id, jobId = window.job.id, bridgeId = self.store.bridgeId })
	if self.cancelWorker then return true end
	local generation = self.generation
	-- O singură cale urgentă comună, independentă de polling și lockurile MCP.
	local function drain()
		while self:valid(generation) and #self.urgent > 0 do
			local item = table.remove(self.urgent, 1)
			if item.bridgeId == self.store.bridgeId then
				local response, message, code = self:request("POST", "/v1/jobs/" .. HttpService:UrlEncode(item.jobId) .. "/cancel", {}, self.token)
				if not self:valid(generation) then return end
				local current = self.store.windows[item.windowId]
				if current and current.job and current.job.id == item.jobId then
					current.cancelling = false
					if response then
						self.store:finish(current, "cancelled")
						if current.closeAfterCancel then self.store:remove(current.id) else self.store:emit(current.id) end
					else
						current.closeAfterCancel = false
						self.store:message(current, "Anulare neconfirmată", tostring(message), "error"); self.store:emit(current.id)
					end
				elseif current and response and not current.job and current.closeAfterCancel then
					-- Jobul s-a încheiat prin poll înainte de răspunsul anulării trimise efectiv.
					self.store:remove(current.id)
				elseif current then
					current.closeAfterCancel = false
					self.store:emit(current.id)
				end
				if code == 401 or code == 403 then self:disconnect(message, code); return end
			end
		end
	end
	self.cancelWorker = task.defer(function()
		local success, failure = pcall(drain)
		if self.generation == generation then
			self.cancelWorker = nil
			if not success then
				self.urgent = {}
				for _, current in pairs(self.store.windows) do current.cancelling = false end
				self:status("Calea de anulare s-a oprit: " .. tostring(failure) .. ". Nu retrimit automat.", true)
			end
		end
	end)
	return true
end

function Controller:close(id, confirmed)
	local window = self.store.windows[id]
	if not window then return end
	if window.kind == "terminal" then self.store:dismissTerminal(id); return end
	if window.job or window.request then
		if not confirmed then window.closeConfirm = true; window.visible = true; self.store:emit(id); return end
		if window.job then
			if not self:cancel(id, true) then
				window.closeAfterCancel = false
				self.store:message(window, "Închidere anulată", "Așteaptă reconectarea daemon-ului, apoi apasă din nou × pentru a anula jobul înainte de închidere.", "error"); self.store:emit(id)
			end
		else
			window.closeAfterCancel = true
			self.store:message(window, "Închidere în așteptare", "Aștept identificarea cererii trimise înainte de anulare. Pentru o trimitere neconfirmată, reluarea se face numai cu confirmarea ta explicită.", "notice")
			self.store:emit(id)
		end
	else self.store:remove(id) end
end

function Controller:destroy()
	self.alive = false
	self.generation += 1
	for _, key in ipairs({ "worker", "cancelWorker", "reconnect", "accountWorker" }) do
		if self[key] then pcall(task.cancel, self[key]); self[key] = nil end
	end
	self.queue, self.urgent = {}, {}
	self.project = nil
	for _, connection in ipairs(self.connections) do pcall(function() connection:Disconnect() end) end
	table.clear(self.connections)
end

return Controller
