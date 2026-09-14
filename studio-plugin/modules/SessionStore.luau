-- Model pur: fără servicii Roblox, HTTP sau modificări ale scenei. Oglindește `/v1/status` și `/v1/board` ale daemon-ului 1.0:
-- identitatea Roblox, workspace-ul (jocul deschis), starea hub-ului, prezenții, sesiunile proprii și ale colegilor, claims-urile și jurnalul.
local Store = {}
Store.__index = Store
Store.MAX_WINDOWS = 32
Store.MAX_MESSAGES = 120
Store.MAX_TEXT = 120000
Store.MAX_JOURNAL = 10
Store.MAX_CLAIMS = 128
Store.MAX_MEMBERS = 64
Store.MAX_WORKSPACES = 64
Store.MAX_VIEW_SESSIONS = 64
Store.PROVIDERS = { claude = "Claude Code", codex = "Codex" }
Store.TERMINAL = { completed = true, failed = true, cancelled = true, lost = true }
Store.REQUIRED_FEATURES = { "queued_sessions", "batch_events", "terminal_sessions", "claims", "board", "identity", "workspaces", "panel" }
Store.CLAIM_ACTIONS = { claimed = "Claim", released = "Eliberat", denied = "Claim refuzat", expired = "Claim expirat" }
Store.UPDATE_STATES = { idle = true, available = true, downloading = true, installed = true, error = true }
Store.PROJECT_STATES = { idle = true, sending = true, sent = true, error = true }
Store.STATES = { draft = true, sending = true, uncertain = true, queued = true, running = true, waiting_approval = true, completed = true, failed = true, cancelled = true, lost = true }
-- Stările hub-ului raportate de daemon (contract §4.1) și rolul de culoare al fiecăreia (DESIGN.md §6.2).
Store.HUB_STATES = { connecting = true, pending = true, approved = true, offline = true, revoked = true, disabled = true }
Store.HUB_ROLES = { connecting = "neutral", pending = "warning", approved = "primary", offline = "danger", revoked = "danger", disabled = "neutral" }
Store.CREATOR_TYPES = { User = true, Group = true }
Store.UNKNOWN_NAME = "Studio"
Store.UNKNOWN_WORKSPACE = "Workspace necunoscut"
Store.LOCAL_WORKSPACE = "local"
Store.OLD_HUB_ERROR = "Hub-ul rulează o versiune mai veche."
Store.HANDOFF_VERSION = 1

function Store.prefix(text, limit)
	text = tostring(text or "")
	if #text <= limit then return text end
	local stop = limit
	while stop > 0 do
		local byte = string.byte(text, stop + 1)
		if not byte or byte < 128 or byte >= 192 then break end
		stop -= 1
	end
	return string.sub(text, 1, stop)
end

local function copy(value)
	if type(value) ~= "table" then return value end
	local result = {}
	for key, child in pairs(value) do result[key] = copy(child) end
	return result
end

local function list(value)
	return if type(value) == "table" then value else {}
end

local function integer(value, minimum)
	local number = tonumber(value)
	if number == nil or number ~= number or math.abs(number) == math.huge then return nil end
	number = math.floor(number)
	return if number >= (minimum or 0) then number else nil
end

local function stringList(value, limit)
	local result = {}
	for _, item in ipairs(list(value)) do
		if type(item) == "string" and item ~= "" and #result < (limit or 64) then table.insert(result, Store.prefix(item, 200)) end
	end
	return result
end

local function optionalText(value, limit)
	return if type(value) == "string" and value ~= "" then Store.prefix(value, limit) else nil
end

-- Text pe o singură linie (caracterele de control devin spații), pentru mesajele afișate în bannere și rânduri.
local function cleanText(value, limit)
	if type(value) ~= "string" then return nil end
	local cleaned = string.match((string.gsub(value, "%c", " ")), "^%s*(.-)%s*$") or ""
	return optionalText(cleaned, limit)
end

function Store.isCompatible(data)
	local features = if type(data) == "table" and type(data.features) == "table" then data.features else {}
	for _, name in ipairs(Store.REQUIRED_FEATURES) do
		if features[name] ~= true then return false end
	end
	return type(data.bridge_id) == "string" and data.bridge_id ~= ""
end

function Store.terminalWindowId(jobId)
	return "terminal:" .. tostring(jobId)
end

-- Avatarul se derivă din contul Roblox (contract §1.5); fără cont cunoscut nu există avatar.
function Store.avatarUrl(userId)
	local id = integer(userId, 1)
	if not id then return nil end
	return "rbxthumb://type=AvatarHeadShot&id=" .. id .. "&w=48&h=48"
end

-- Starea hub-ului până la primul `/v1/status` al daemon-ului: se conectează, fără url, fără dispozitiv.
function Store.defaultHub()
	return { status = "connecting", url = nil, hubId = nil, deviceId = nil, error = nil, panelUrl = nil, enrollment = nil, lastSync = nil }
end

-- Starea actualizării automate raportată de daemon (0.6+); fără daemon sau cu unul vechi rămâne implicită.
function Store.defaultUpdate()
	return { current = "", available = nil, state = "idle", message = "", restartRequired = false }
end

-- Inventarul proiectului trimis daemon-ului (0.7+): starea locală a ultimului snapshot.
function Store.defaultProject()
	return { snapshotId = nil, count = 0, truncated = false, sentAt = nil, state = "idle", message = "" }
end

-- `identity` din `/v1/status` (contract §4.3): `user_id` 0 = cont necunoscut (numele „Studio”, fără avatar); avatarul lipsă se derivă din id.
function Store.identityFrom(data)
	if type(data) ~= "table" then return nil end
	local userId = integer(data.user_id) or 0
	if userId == 0 then return { userId = 0, name = Store.UNKNOWN_NAME, avatar = nil } end
	return { userId = userId, name = cleanText(data.name, 64) or ("user_" .. userId), avatar = optionalText(data.avatar, 300) or Store.avatarUrl(userId) }
end

-- Meta unui workspace (contract §2.2): `{key, game_id, place_id, name, creator_id, creator_type}` → câmpuri camelCase; numele lipsă devine cheia.
function Store.workspaceFrom(meta)
	if type(meta) ~= "table" then return nil end
	local key = optionalText(meta.key, 64)
	if not key then return nil end
	return { key = key, name = cleanText(meta.name, 200) or key, placeId = integer(meta.place_id) or 0, gameId = integer(meta.game_id) or 0,
		creatorId = integer(meta.creator_id) or 0, creatorType = if Store.CREATOR_TYPES[meta.creator_type] then meta.creator_type else nil }
end

-- `hub` din `/v1/status`/`/v1/board` (contract §4.3); `panel_url` vine doar din status, altfel se derivă din url (nil când hub-ul e dezactivat).
function Store.hubFrom(data, panelUrl)
	local hub = Store.defaultHub()
	if type(data) ~= "table" then return hub end
	hub.status = if Store.HUB_STATES[data.status] then data.status else "connecting"
	hub.url = optionalText(data.url, 300)
	hub.hubId = optionalText(data.hub_id, 64)
	hub.deviceId = optionalText(data.device_id, 64)
	hub.error = cleanText(data.error, 200)
	hub.enrollment = optionalText(data.enrollment, 16)
	hub.lastSync = tonumber(data.last_sync)
	hub.panelUrl = optionalText(panelUrl, 300) or (if hub.url and hub.status ~= "disabled" then hub.url .. "/panel" else nil)
	return hub
end

-- Un rând de prezență (`members` din status/board sau `/hub/workspace`): fără cont Roblox, numele afișat este mașina (contract §1.5).
function Store.memberFrom(row, myDeviceId)
	if type(row) ~= "table" then return nil end
	local deviceId = optionalText(row.device_id, 64) or optionalText(row.member_id, 64)
	if not deviceId then return nil end
	local userId = integer(row.roblox_user_id) or 0
	local machine = cleanText(row.machine, 64)
	local name = cleanText(row.roblox_name, 64) or cleanText(row.developer, 64) or machine or Store.UNKNOWN_NAME
	return { deviceId = deviceId, userId = userId, name = name, machine = machine or "?", lastSeen = tonumber(row.last_seen),
		me = row.me == true or deviceId == myDeviceId, online = row.online ~= false }
end

-- Un rând din sumarul workspace-urilor: `members_online` este număr (daemon) sau listă (hub); câmpurile de meta sunt opționale.
function Store.workspaceRowFrom(row)
	if type(row) ~= "table" then return nil end
	local key = optionalText(row.key, 64)
	if not key then return nil end
	local online = row.members_online
	return { key = key, name = cleanText(row.name, 200) or key,
		membersOnline = if type(online) == "table" then #online else integer(online) or 0,
		sessionsActive = integer(row.sessions_active) or 0, claims = integer(row.claims) or 0, mine = row.mine == true,
		placeId = integer(row.place_id), gameId = integer(row.game_id), creatorId = integer(row.creator_id),
		creatorType = if Store.CREATOR_TYPES[row.creator_type] then row.creator_type else nil, lastSeen = tonumber(row.last_seen) }
end

-- Proprietarul unei sesiuni din tablă (`developer`, `device_id`, `roblox_user_id`, `machine`); `member_id` este forma 0.8 a dispozitivului.
function Store.ownerFrom(session)
	if type(session) ~= "table" then return nil end
	local name = cleanText(session.developer, 60)
	local deviceId = optionalText(session.device_id, 64) or optionalText(session.member_id, 64)
	local machine = cleanText(session.machine, 64)
	local userId = integer(session.roblox_user_id) or 0
	if not name and not deviceId and not machine and userId == 0 then return nil end
	return { userId = userId, name = name or machine or Store.UNKNOWN_NAME, deviceId = deviceId, machine = machine }
end

local function sessionName(session)
	local name = if type(session.name) == "string" and string.find(session.name, "%S") then session.name else "Terminal"
	return Store.prefix((string.gsub(name, "%c", " ")), 80)
end

-- O sesiune a altui workspace (vizualizare read-only din `/v1/hub/workspace?key=`): doar meta, fără fereastră și fără evenimente.
function Store.sessionRowFrom(row, myDeviceId)
	if type(row) ~= "table" or type(row.job_id) ~= "string" or row.job_id == "" then return nil end
	local owner = Store.ownerFrom(row)
	return { jobId = row.job_id, kind = if row.kind == "studio" then "studio" else "terminal",
		provider = if Store.PROVIDERS[row.provider] then row.provider else "claude", name = sessionName(row),
		state = if type(row.state) == "string" and Store.STATES[row.state] then row.state else "running",
		owner = owner, workspace = optionalText(row.workspace, 64), started = tonumber(row.started), lastActivity = tonumber(row.last_activity),
		claims = stringList(row.claims), pendingApproval = row.pending_approval == true,
		mine = row.mine == true or (owner ~= nil and owner.deviceId ~= nil and owner.deviceId == myDeviceId) }
end

-- O intrare de jurnal (hub sau locală): `seq` și `workspace` lipsesc la intrările locale ale unui daemon fără hub.
function Store.journalFrom(entry)
	return { seq = integer(entry.seq), time = tonumber(entry.time), jobId = optionalText(entry.job_id, 128), tool = Store.prefix(entry.tool, 60),
		developer = if type(entry.developer) == "string" then Store.prefix(entry.developer, 60) else "?",
		userId = integer(entry.roblox_user_id) or 0, provider = if Store.PROVIDERS[entry.provider] then entry.provider else nil,
		paths = stringList(entry.paths, 8), summary = if type(entry.summary) == "string" then Store.prefix(entry.summary, 160) else "",
		workspace = optionalText(entry.workspace, 64) }
end

-- Sumarul inventarului reținut de daemon (`project` din `/v1/status`), diferit de starea locală a scanării (`store.project`).
function Store.daemonProjectFrom(project)
	if type(project) ~= "table" then return nil end
	return { snapshotId = optionalText(project.snapshot_id, 128), count = integer(project.count) or 0, taken = tonumber(project.taken),
		placeName = cleanText(project.place_name, 200) }
end

-- Numele afișat al unui workspace: numele jocului; fișierul nepublicat fără nume devine „Fișier nepublicat”; fără workspace, „Workspace necunoscut”.
function Store.workspaceName(workspace)
	if type(workspace) ~= "table" then return Store.UNKNOWN_WORKSPACE end
	local key = optionalText(workspace.key, 64)
	local name = cleanText(workspace.name, 200)
	if key == Store.LOCAL_WORKSPACE and (name == nil or name == key) then return "Fișier nepublicat" end
	return name or key or Store.UNKNOWN_WORKSPACE
end

-- Textul complet al unei stări hub (DESIGN.md §6.2 și §12.2) și rolul de culoare.
function Store.hubText(hub)
	hub = if type(hub) == "table" then hub else Store.defaultHub()
	local status = hub.status
	if status == "approved" then return "Hub conectat", "primary"
	elseif status == "pending" then
		return "Așteaptă aprobarea adminului · dispozitiv " .. (if hub.deviceId then string.sub(hub.deviceId, 1, 8) else "—"), "warning"
	elseif status == "offline" then
		local reason = hub.error or "hub-ul nu răspunde"
		if string.sub(reason, 1, #Store.OLD_HUB_ERROR) == Store.OLD_HUB_ERROR then
			return "Hub-ul rulează o versiune mai veche. Actualizează hub-ul ca să apari în workspace.", "danger"
		end
		reason = string.gsub(Store.prefix(reason, 80), "[%.%s]+$", "")
		return "Hub offline: " .. reason .. ". Sesiunile tale merg local; claims-urile sunt doar locale până revine.", "danger"
	elseif status == "revoked" then return "Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou.", "danger"
	elseif status == "disabled" then return "Hub dezactivat (daemon pornit cu --no-hub).", "neutral"
	end
	return "Se conectează la hub…", "neutral"
end

function Store.new()
	return setmetatable({
		windows = {}, order = {}, listeners = {},
		connected = false, connecting = false, compatible = false, bridgeId = nil,
		providers = {}, studios = {}, defaultStudioId = nil, defaultProvider = "claude",
		developer = nil, machine = nil, identity = nil, workspace = nil, hub = Store.defaultHub(),
		members = {}, workspaces = {}, viewWorkspace = nil, view = nil, pluginConnected = false, daemonProject = nil,
		-- „ask” (implicit) | „edits” (orice modificare, fără generare) | „all” (inclusiv generarea, care consumă credite).
		autoApprove = "ask",
		claims = {}, journal = {}, dismissedTerminal = {},
		update = Store.defaultUpdate(), project = Store.defaultProject(), bundleError = nil,
		status = "Daemon deconectat. Poți deschide ferestre draft.", statusError = false,
	}, Store)
end

function Store:subscribe(callback)
	table.insert(self.listeners, callback)
	return function()
		local index = table.find(self.listeners, callback)
		if index then table.remove(self.listeners, index) end
	end
end

function Store:emit(id)
	for _, listener in ipairs(self.listeners) do listener(id) end
end

function Store:message(window, author, text, kind)
	local clipped = #tostring(text or "") > Store.MAX_TEXT
	local message = {
		author = author, text = Store.prefix(text, Store.MAX_TEXT), kind = kind or "text",
		clipped = clipped,
	}
	if clipped then message.text ..= "\n[Afișare locală limitată.]" end
	table.insert(window.messages, message)
	while #window.messages > Store.MAX_MESSAGES do table.remove(window.messages, 1) end
	return message
end

local function newWindow(id, kind, provider, name)
	return {
		id = id, kind = kind, name = name, provider = provider, developer = nil,
		remote = false, mine = true, machine = nil, owner = nil, workspace = nil, sessionKind = kind, started = nil,
		studioId = nil, studioName = nil, cwd = nil, cursor = 0,
		sessionId = nil, job = nil, request = nil, locked = false, terminalJobId = nil,
		draft = "", draftRevision = 0, messages = {}, approvals = {}, visible = true,
		state = "draft", cancelling = false, decisionId = nil, releasing = false,
		closeConfirm = false, closeAfterCancel = false, restoreVersion = 0, claims = {}, pendingApproval = false,
	}
end

-- Cheia workspace-ului curent (jocul deschis), cunoscută după primul `/v1/identity` acceptat de daemon.
function Store:workspaceKey()
	return if self.workspace then self.workspace.key else nil
end

-- Proprietarul ferestrelor proprii: contul Roblox raportat (sau mașina) și dispozitivul acestui PC; nil până se știe ceva.
function Store:ownIdentity()
	local identity = self.identity
	if not identity and not self.hub.deviceId and not self.machine then return nil end
	return { userId = if identity then identity.userId else 0, name = if identity then identity.name else (self.machine or Store.UNKNOWN_NAME),
		deviceId = self.hub.deviceId, machine = self.machine }
end

function Store:create(id, provider, studio, name)
	if self.windows[id] or #self.order >= Store.MAX_WINDOWS or not Store.PROVIDERS[provider] then return nil end
	local window = newWindow(id, "studio", provider, name or ("Sesiune " .. (#self.order + 1)))
	window.studioId, window.studioName = studio and studio.id or nil, studio and studio.name or nil
	window.owner, window.workspace = self:ownIdentity(), self:workspaceKey()
	-- Momentul real al deschiderii ferestrei: supraviețuiește hot swap-ului prin handoff, deci „de X min” nu se resetează la metamorfoză.
	window.started = os.time()
	self.windows[id] = window
	table.insert(self.order, id)
	self:message(window, "Sesiune nouă", "Context separat pentru această fereastră. Scrie un mesaj; nimic nu se trimite înainte de Trimite.")
	self:emit(id)
	return window
end

function Store:setDraft(id, text)
	local window = self.windows[id]
	if window and window.kind == "studio" and window.draft ~= text then
		window.draft = text
		window.draftRevision += 1
	end
end

function Store:rename(id, name)
	local window = self.windows[id]
	if not window or window.kind ~= "studio" then return end
	name = Store.prefix((string.gsub(name, "%c", " ")), 80)
	if string.find(name, "%S") then window.name = name end
	self:emit(id)
end

function Store:setVisible(id, visible)
	local window = self.windows[id]
	if window and window.visible ~= visible then window.visible = visible; self:emit(id) end
end

function Store:restore(id)
	local window = self.windows[id]
	if window then
		window.visible = true
		window.restoreVersion += 1
		self:emit(id)
	end
end

function Store:bindStudio(id, studio)
	local window = self.windows[id]
	if not window or window.kind ~= "studio" or window.locked or window.job or window.request or not studio then return false end
	window.studioId, window.studioName = studio.id, studio.name
	self:emit(id)
	return true
end

function Store:studio(id)
	for _, studio in ipairs(self.studios) do if studio.id == id then return studio end end
	return nil
end

function Store:applyStudios(studios)
	self.studios = {}
	local seen = {}
	for _, studio in ipairs(list(studios)) do
		if type(studio) == "table" and type(studio.id) == "string" and studio.id ~= "" and not seen[studio.id] then
			seen[studio.id] = true
			table.insert(self.studios, { id = studio.id, name = tostring(studio.name or "Studio") })
		end
	end
	if not self:studio(self.defaultStudioId) then self.defaultStudioId = nil end
	if #self.studios == 1 then self.defaultStudioId = self.studios[1].id end
end

function Store:setStudios(studios)
	self:applyStudios(studios)
	self:emit(nil)
end

function Store:setDefaultStudio(studioId)
	if studioId ~= nil and not self:studio(studioId) then return false end
	self.defaultStudioId = studioId
	self:emit(nil)
	return true
end

function Store:providerAvailable(provider)
	local entry = self.providers[provider]
	return type(entry) == "table" and entry.available == true
end

-- La deconectarea daemon-ului: hub-ul, prezenții și sumarul workspace-urilor nu mai sunt cunoscute; identitatea și jocul deschis rămân.
function Store:resetHub()
	self.hub, self.members, self.workspaces, self.viewWorkspace, self.view = Store.defaultHub(), {}, {}, nil, nil
	self.daemonProject, self.pluginConnected = nil, false
end

function Store:remoteChanged(bridgeId)
	if type(bridgeId) ~= "string" or bridgeId == "" or self.bridgeId == bridgeId then return false end
	local previous = self.bridgeId
	self.bridgeId = bridgeId
	if previous then
		for index = #self.order, 1, -1 do
			local window = self.windows[self.order[index]]
			if window.kind == "terminal" then
				self.windows[window.id] = nil
				table.remove(self.order, index)
			else
				if window.request and window.request.message then window.request.message.author = "Tu · neconfirmat înainte de restart" end
				window.sessionId, window.job, window.request = nil, nil, nil
				window.approvals, window.claims = {}, {}
				window.cancelling, window.decisionId, window.closeAfterCancel = false, nil, false
				window.state = "draft"
				self:message(window, "Daemon repornit", "Referințele remote vechi au fost invalidate. Istoricul și draftul local sunt păstrate. Nimic nu este retrimis automat; următoarea trimitere începe o conversație remote nouă.", "notice")
			end
		end
		self.studios, self.defaultStudioId = {}, nil
		self.claims, self.journal, self.dismissedTerminal = {}, {}, {}
		self:resetHub()
		self.update, self.project = Store.defaultUpdate(), Store.defaultProject()
	end
	self:emit(nil)
	return previous ~= nil
end

function Store:canSend(window)
	if not window then return false, "Sesiunea nu există." end
	if window.kind ~= "studio" then return false, "Sesiune din terminal: doar de citit." end
	if not self.connected or self.connecting then return false, "Daemon-ul nu răspunde. Mesajul rămâne ca draft." end
	if not self.compatible then return false, "Pornește daemon-ul 1.0 sau mai nou (terminal cu pluginul Claude Code sau Start-Daemon.cmd)." end
	if window.job or window.request or window.cancelling then return false, "Cererea acestei ferestre nu s-a încheiat." end
	if #window.approvals > 0 then return false, "Rezolvă aprobarea acestei ferestre." end
	if not self:providerAvailable(window.provider) then
		return false, "CLI-ul oficial " .. (Store.PROVIDERS[window.provider] or window.provider) .. " nu a fost găsit de daemon."
	end
	if window.studioId == nil then return false, "Alege Studio-ul țintă în Avansat sau deschide locul din identitate." end
	if not self:studio(window.studioId) then return false, "Studio-ul țintă nu mai este conectat; verifică MCP în Studio sau alege altul în Avansat." end
	return true
end

function Store:beginRequest(id, requestId, context)
	local window = self.windows[id]
	local allowed, message = self:canSend(window)
	if not allowed then return nil, message end
	local prompt = string.match(window.draft, "^%s*(.-)%s*$") or ""
	if prompt == "" or #prompt > 24000 then return nil, "Mesajul trebuie să conțină text și să fie de cel mult 24 KB." end
	local payload = {
		provider = window.provider, prompt = prompt, studio_id = window.studioId,
		client_request_id = requestId, context = copy(context),
	}
	if window.sessionId then payload.session_id = window.sessionId end
	window.request = { id = requestId, payload = payload, bridgeId = self.bridgeId,
		draftRevision = window.draftRevision, state = "sending",
		message = self:message(window, "Tu · trimitere", prompt, "user") }
	window.locked = true
	window.state = "sending"
	self:emit(id)
	return window.request
end

function Store:requestFailed(id, requestId, message, uncertain)
	local window = self.windows[id]
	if not window or not window.request or window.request.id ~= requestId then return end
	if window.request.message then window.request.message.author = if uncertain then "Tu · neconfirmat" else "Tu · respins" end
	if uncertain then window.request.state = "uncertain"; window.state = "uncertain"
	else
		-- Respingere definitivă: nu există job, deci ținta se poate schimba dacă nu există conversație.
		window.request, window.state, window.closeAfterCancel = nil, "failed", false
		if not window.sessionId then window.locked = false end
	end
	self:message(window, "Trimitere neconfirmată", message .. (if uncertain then "\nNu retrimit automat. Poți confirma explicit reluarea aceleiași cereri, cu același ID." else "\nNimic nu este retrimis automat."), "error")
	self:emit(id)
end

function Store:acceptRequest(id, requestId, data)
	local window = self.windows[id]
	if not window or not window.request or window.request.id ~= requestId then return false end
	if type(data.job_id) ~= "string" or data.job_id == "" or type(data.session_id) ~= "string" then return false end
	local owner = self:findJob(data.job_id)
	if owner and owner ~= window then return false end
	local request = window.request
	window.sessionId = data.session_id
	window.job = { id = data.job_id, after = 0, state = data.state or "queued", message = nil }
	window.state = window.job.state
	-- Daemon-ul 1.0 întoarce workspace-ul jobului (contract §4.2); lipsa lui păstrează cheia de la creare.
	window.workspace = optionalText(data.workspace, 64) or window.workspace
	if request.message and table.find(window.messages, request.message) then request.message.author = "Tu"
	else self:message(window, "Tu", request.payload.prompt, "user") end
	if window.draftRevision == request.draftRevision then window.draft = ""; window.draftRevision += 1 end
	window.request = nil
	self:emit(id)
	return true
end

function Store:findJob(jobId)
	for _, window in pairs(self.windows) do
		if window.job and window.job.id == jobId then return window end
	end
	return nil
end

function Store:jobs()
	local jobs = {}
	for _, id in ipairs(self.order) do
		local window = self.windows[id]
		if window.job then table.insert(jobs, { job_id = window.job.id, after = window.job.after }) end
	end
	return jobs
end

function Store:jobIdOf(window)
	if not window then return nil end
	return if window.job then window.job.id else window.terminalJobId
end

function Store:removeApproval(id, jobId, approvalId)
	local window = self.windows[id]
	if not window then return end
	for index = #window.approvals, 1, -1 do
		local approval = window.approvals[index]
		if approval.jobId == jobId and approval.id == approvalId then table.remove(window.approvals, index) end
	end
	if window.decisionId == approvalId and window.job and window.job.id == jobId then window.decisionId = nil end
end

-- ApprovalView confirmă că argumentele au putut fi afișate integral; HubView activează „Permite” doar atunci (regula 0.8: nu aprobi ce nu ai citit).
function Store:setApprovalInspectable(id, approvalId, ok)
	local window = self.windows[id]
	if not window then return false end
	for _, approval in ipairs(window.approvals) do
		if approval.id == approvalId then
			local value = ok == true
			if approval.inspectable ~= value then approval.inspectable = value; self:emit(id) end
			return true
		end
	end
	return false
end

-- `mine` vine de la daemon (claims-urile joburilor proprii); fără el, după fereastra jobului sau după developer (tablă 0.8).
function Store:ownsClaim(claim)
	if type(claim.mine) == "boolean" then return claim.mine end
	local owner = self:findJob(claim.jobId)
	if owner then return not owner.remote end
	return self.developer ~= nil and claim.developer == self.developer
end

function Store:finish(window, terminalState)
	if not window.job then return end
	window.cursor = window.job.after
	window.job = nil
	window.state = terminalState
	window.cancelling, window.decisionId, window.releasing = false, nil, false
	window.claims = {}
	for _, approval in ipairs(window.approvals) do approval.inactive = true end
	if terminalState == "cancelled" then window.approvals = {} end
	self:message(window, "Status", if terminalState == "completed" then (if window.kind == "terminal" then "Sesiunea din terminal s-a încheiat." else "Cererea s-a încheiat.")
		elseif terminalState == "cancelled" then (if window.kind == "terminal" then "Sesiunea a fost oprită din Studio." else "Cererea a fost anulată.")
		elseif terminalState == "lost" then (if window.remote then "Developerul nu mai răspunde hub-ului; sesiunea revine dacă se reconectează." else "Jobul nu mai există în daemon; nu este reluat automat.")
		else "Cererea s-a încheiat cu o eroare.", if terminalState == "failed" or terminalState == "lost" then "error" else "notice")
end

function Store:claimText(event)
	local action = Store.CLAIM_ACTIONS[event.action] or "Claim"
	local paths = stringList(event.paths)
	local text = action .. ": " .. (if #paths > 0 then table.concat(paths, ", ") else "(fără căi)")
	if event.action == "denied" and type(event.holder) == "string" and event.holder ~= "" then text ..= " · deținut de " .. Store.prefix(event.holder, 60) end
	return text
end

function Store:applyEvents(data)
	local window = self:findJob(data.job_id)
	if not window then return nil end
	if data.ok == false then
		if data.status == 404 then self:finish(window, "lost"); self:emit(window.id) end
		return window
	end
	local job = window.job
	local changed = false
	local events = {}
	for _, event in ipairs(list(data.events)) do
		if type(event) == "table" and type(event.seq) == "number" and event.seq % 1 == 0 and event.seq >= 0 then table.insert(events, event) end
	end
	table.sort(events, function(a, b) return a.seq < b.seq end)
	for _, event in ipairs(events) do
		if event.seq > job.after then
			changed = true
			if event.type == "text" and type(event.text) == "string" then
				if not job.message or not table.find(window.messages, job.message) then
					job.message = self:message(window, Store.PROVIDERS[window.provider], "", "assistant")
				end
				if not job.message.clipped then
					local text = job.message.text .. event.text
					job.message.clipped = #text > Store.MAX_TEXT
					job.message.text = Store.prefix(text, Store.MAX_TEXT) .. (if job.message.clipped then "\n[Afișare locală limitată.]" else "")
				end
			elseif event.type == "prompt" and type(event.text) == "string" then
				job.message = nil
				self:message(window, tostring(window.developer or self.developer or "Developer") .. " · terminal", event.text, "user")
			elseif event.type == "claim" then
				self:message(window, "Coordonare", self:claimText(event), "claim")
			elseif event.type == "approval" and window.remote then
				-- Aprobarea aparține developerului sesiunii; aici este doar o notificare.
				self:message(window, "Propunere", tostring(event.tool or "Instrument necunoscut") .. " așteaptă aprobarea lui " .. tostring(window.developer or "developer") .. ".", "approval")
			elseif event.type == "approval" and type(event.approval_id) == "string" and event.approval_id ~= "" then
				local exists = false
				for _, approval in ipairs(window.approvals) do if approval.id == event.approval_id then exists = true end end
				if not exists then
					if #window.approvals >= 32 then
						window.approvals[1].blocked = true
						self:message(window, "Prea multe aprobări", "Oprește această cerere și cere pași mai mici.", "error")
					else
						table.insert(window.approvals, { id = event.approval_id, jobId = job.id,
							tool = event.tool, arguments = event.arguments, inspectable = false,
							blocked = type(event.tool) ~= "string" or event.tool == "" or type(event.arguments) ~= "table", inactive = false })
						self:message(window, "Propunere", tostring(event.tool or "Instrument necunoscut") .. " cere aprobarea ta.", "approval")
					end
				end
			elseif event.type == "status" then
				if event.approval_active == false and type(event.approval_id) == "string" then self:removeApproval(window.id, job.id, event.approval_id) end
				if type(event.text) == "string" then self:message(window, "Status", event.text, "notice") end
			elseif event.type == "error" then
				self:message(window, "Eroare", tostring(event.text or "Daemon-ul a raportat o eroare."), "error")
			elseif event.type == "tool" then
				job.message = nil
				self:message(window, "Instrument", tostring(event.tool or "MCP") .. (if type(event.text) == "string" then "\n" .. event.text else ""), "tool")
			end
			job.after = event.seq
		end
	end
	if type(data.state) == "string" and job.state ~= data.state then
		job.state, window.state, changed = data.state, data.state, true
	end
	if Store.TERMINAL[job.state] then self:finish(window, job.state); changed = true end
	if changed then self:emit(window.id) end
	return window
end

-- Sesiune a altui dispozitiv (oglindită prin hub): `mine` vine de la daemon-ul 1.0; `remote`/developer diferit sunt formele vechi ale tablei.
function Store:isRemote(session)
	if session.mine == true then return false end
	if session.mine == false or session.remote == true then return true end
	return type(session.developer) == "string" and self.developer ~= nil and session.developer ~= self.developer
end

function Store:syncTerminalSession(session, remote)
	local jobId = session.job_id
	local id = Store.terminalWindowId(jobId)
	if self.dismissedTerminal[jobId] then return nil end
	local window = self.windows[id]
	local state = if type(session.state) == "string" then session.state else "running"
	if not window then
		if #self.order >= Store.MAX_WINDOWS then return nil end
		window = newWindow(id, "terminal", if Store.PROVIDERS[session.provider] then session.provider else "claude", sessionName(session))
		window.terminalJobId, window.remote, window.mine = jobId, remote == true, remote ~= true
		window.sessionKind = if session.kind == "studio" then "studio" else "terminal"
		window.locked, window.state = true, state
		window.started = tonumber(session.started) or os.time()
		if not Store.TERMINAL[state] then window.job = { id = jobId, after = 0, state = state, message = nil } end
		self.windows[id] = window
		table.insert(self.order, id)
		self:message(window, if remote then "Sesiune a altui developer" else "Sesiune din terminal",
			if remote then "Fereastră doar de citit, oglindită prin hub. Oprirea, claims-urile și aprobările aparțin developerului ei."
			else "Fereastră doar de citit. Comenzile se dau în terminalul developerului; aici vezi promptul, instrumentele și claims-urile.", "notice")
	end
	window.name = sessionName(session)
	window.started = tonumber(session.started) or window.started
	window.developer = if type(session.developer) == "string" then Store.prefix(session.developer, 60) else window.developer
	window.machine = if type(session.machine) == "string" then Store.prefix(session.machine, 64) else window.machine
	window.owner = Store.ownerFrom(session) or window.owner
	window.workspace = optionalText(session.workspace, 64) or window.workspace
	window.cwd = if type(session.cwd) == "string" then Store.prefix(session.cwd, 200) else nil
	window.claims = stringList(session.claims)
	window.pendingApproval = session.pending_approval == true
	if type(session.studio_id) == "string" and session.studio_id ~= "" then
		window.studioId = session.studio_id
		local studio = self:studio(session.studio_id)
		window.studioName = if studio then studio.name else window.studioName
	end
	if window.job then
		if Store.TERMINAL[state] then self:finish(window, state)
		elseif window.job.state ~= state then window.job.state, window.state = state, state end
	elseif not Store.TERMINAL[state] and Store.TERMINAL[window.state] then
		-- Jobul a revenit în board ca activ (dispozitiv remote reconectat); îl urmărim de la ultimul cursor.
		window.job = { id = jobId, after = window.cursor, state = state, message = nil }
		window.state = state
	elseif Store.TERMINAL[state] and window.state ~= state then
		window.state = state
	end
	return window
end

local function validClaim(claim)
	return type(claim) == "table" and type(claim.path) == "string" and claim.path ~= "" and type(claim.job_id) == "string"
end

function Store:resetUpdate()
	self.update = Store.defaultUpdate()
end

function Store:applyUpdate(update)
	if type(update) ~= "table" then self:resetUpdate(); return end
	local message = if type(update.message) == "string" then (string.gsub(update.message, "%c", " ")) else ""
	self.update = {
		current = if type(update.current) == "string" then Store.prefix(update.current, 32) else "",
		available = optionalText(update.available, 32),
		state = if type(update.state) == "string" and Store.UPDATE_STATES[update.state] then update.state else "idle",
		message = Store.prefix(message, 200),
		restartRequired = update.restart_required == true,
	}
end

function Store:resetProject()
	self.project = Store.defaultProject()
end

-- Câmpurile lipsă rămân din snapshotul anterior (sending → sent păstrează numărul); valorile invalide revin la implicit.
function Store:setProject(state, fields)
	local previous, project = self.project, Store.defaultProject()
	fields = if type(fields) == "table" then fields else {}
	local function pick(key) return if fields[key] ~= nil then fields[key] else previous[key] end
	project.state = if Store.PROJECT_STATES[state] then state else "idle"
	project.snapshotId = optionalText(pick("snapshotId"), 64)
	project.count = math.max(0, math.floor(tonumber(pick("count")) or 0))
	project.truncated = pick("truncated") == true
	project.sentAt = tonumber(pick("sentAt"))
	local message = pick("message")
	project.message = if type(message) == "string" then Store.prefix((string.gsub(message, "%c", " ")), 200) else ""
	self.project = project
	self:emit(nil)
	return project
end

function Store:applyMembers(members)
	self.members = {}
	for _, row in ipairs(list(members)) do
		local member = Store.memberFrom(row, self.hub.deviceId)
		if member and #self.members < Store.MAX_MEMBERS then table.insert(self.members, member) end
	end
end

function Store:applyWorkspaces(rows)
	self.workspaces = {}
	local current = self:workspaceKey()
	for _, row in ipairs(list(rows)) do
		local workspace = Store.workspaceRowFrom(row)
		if workspace and #self.workspaces < Store.MAX_WORKSPACES then
			workspace.mine = workspace.mine or workspace.key == current
			table.insert(self.workspaces, workspace)
		end
	end
end

function Store:workspaceRow(key)
	for _, workspace in ipairs(self.workspaces) do if workspace.key == key then return workspace end end
	return nil
end

-- Câmpurile comune din `/v1/status` și `/v1/board` (contract §4.3, §4.6): identitate, workspace, hub, prezenți, sumarul workspace-urilor.
function Store:applyPresence(data)
	-- `identity` este `null` până la primul `/v1/identity` acceptat (contract §4.3); identitatea citită local din Studio
	-- și publicată de controller rămâne până când daemon-ul o confirmă, altfel antetul își pierde numele la fiecare status.
	self.identity = Store.identityFrom(data.identity) or self.identity
	self.workspace = Store.workspaceFrom(data.workspace)
	self.hub = Store.hubFrom(data.hub, data.panel_url)
	self:applyMembers(data.members)
	self:applyWorkspaces(data.workspaces)
	local owner = self:ownIdentity()
	if owner then
		for _, window in pairs(self.windows) do
			if not window.remote then window.owner = copy(owner) end
		end
	end
end

-- `GET /v1/status` → store (contract §4.3): providerii, numele afișat, identitatea, workspace-ul, hub-ul, prezenții, actualizarea, inventarul daemon-ului.
function Store:applyStatus(data)
	if type(data) ~= "table" then return false end
	self.providers = if type(data.providers) == "table" then data.providers else {}
	if type(data.developer) == "string" and data.developer ~= "" then self.developer = Store.prefix(data.developer, 60) end
	self.machine = cleanText(data.machine, 64) or self.machine
	self.pluginConnected = data.plugin_connected == true
	-- Modul de aprobare ține de daemon (îl păstrează în config.json); pluginul doar îl arată și îl schimbă.
	local settings = if type(data.settings) == "table" then data.settings else nil
	local mode = if settings then settings.auto_approve else nil
	if mode == "ask" or mode == "edits" or mode == "all" then self.autoApprove = mode end
	self:applyPresence(data)
	self:applyUpdate(data.update)
	self.daemonProject = Store.daemonProjectFrom(data.project)
	self:emit(nil)
	return true
end

-- `GET /v1/board` → store (contract §4.6): sesiunile proprii și ale colegilor din workspace, claims-urile cu `mine`, jurnalul workspace-ului.
function Store:syncBoard(board)
	if type(board) ~= "table" then return false end
	self:applyStudios(board.studios)
	if type(board.developer) == "string" and board.developer ~= "" then self.developer = Store.prefix(board.developer, 60) end
	if type(board.default_studio_id) == "string" and self:studio(board.default_studio_id) then self.defaultStudioId = board.default_studio_id end
	self:applyPresence(board)
	local present = {}
	for _, session in ipairs(list(board.sessions)) do
		if type(session) == "table" and type(session.job_id) == "string" and session.job_id ~= "" then
			local remote = self:isRemote(session)
			if remote or session.kind == "terminal" then
				present[session.job_id] = true
				self:syncTerminalSession(session, remote)
			else
				local window = self:findJob(session.job_id)
				if window then
					window.claims = stringList(session.claims)
					window.workspace = optionalText(session.workspace, 64) or window.workspace
					window.owner = Store.ownerFrom(session) or window.owner
				end
			end
		end
	end
	for index = #self.order, 1, -1 do
		local window = self.windows[self.order[index]]
		if window.kind == "terminal" and not present[window.terminalJobId] then
			if window.job then self:finish(window, "lost") end
			self.windows[window.id] = nil
			table.remove(self.order, index)
			self.dismissedTerminal[window.terminalJobId] = nil
		end
	end
	for jobId in pairs(self.dismissedTerminal) do
		if not present[jobId] then self.dismissedTerminal[jobId] = nil end
	end
	self.claims = {}
	for _, claim in ipairs(list(board.claims)) do
		if validClaim(claim) and #self.claims < Store.MAX_CLAIMS then
			table.insert(self.claims, { path = Store.prefix(claim.path, 200), jobId = claim.job_id,
				developer = if type(claim.developer) == "string" then Store.prefix(claim.developer, 60) else "?",
				since = tonumber(claim.since), expires = tonumber(claim.expires), reason = optionalText(claim.reason, 120),
				workspace = optionalText(claim.workspace, 64), mine = if type(claim.mine) == "boolean" then claim.mine else nil })
		end
	end
	-- Daemon-ul trimite jurnalul cronologic; aici rămân ultimele MAX_JOURNAL intrări, cea mai nouă prima (DESIGN.md §10.1).
	self.journal = {}
	local entries = list(board.journal)
	for index = #entries, 1, -1 do
		local entry = entries[index]
		if type(entry) == "table" and type(entry.tool) == "string" and #self.journal < Store.MAX_JOURNAL then
			table.insert(self.journal, Store.journalFrom(entry))
		end
	end
	self:emit(nil)
	return true
end

-- Alege workspace-ul privit în secțiunea Workspace (read-only); nil sau cheia jocului deschis revin la cel curent. Datele vin apoi prin applyWorkspaceView.
function Store:setViewWorkspace(key)
	if key ~= nil and (type(key) ~= "string" or key == "" or #key > 64) then return false end
	if key == self:workspaceKey() then key = nil end
	if key ~= self.viewWorkspace then
		self.viewWorkspace, self.view = key, nil
		self:emit(nil)
	end
	return true
end

-- Răspunsul `GET /v1/hub/workspace?key=` pentru workspace-ul privit: meta, prezenți, sesiuni (doar meta) și numărul de claims; alt key este ignorat.
function Store:applyWorkspaceView(key, data)
	if type(key) ~= "string" or key == "" or key ~= self.viewWorkspace or type(data) ~= "table" then return false end
	local members, sessions = {}, {}
	for _, row in ipairs(list(data.members)) do
		local member = Store.memberFrom(row, self.hub.deviceId)
		if member and #members < Store.MAX_MEMBERS then table.insert(members, member) end
	end
	for _, row in ipairs(list(data.sessions)) do
		local session = Store.sessionRowFrom(row, self.hub.deviceId)
		if session and #sessions < Store.MAX_VIEW_SESSIONS then table.insert(sessions, session) end
	end
	table.sort(sessions, function(a, b)
		local activeA, activeB = not Store.TERMINAL[a.state], not Store.TERMINAL[b.state]
		if activeA ~= activeB then return activeA end
		local startedA, startedB = a.started or 0, b.started or 0
		if startedA ~= startedB then return startedA > startedB end
		return a.jobId < b.jobId
	end)
	local workspace = Store.workspaceFrom(data.workspace) or self:workspaceRow(key) or { key = key, name = key }
	self.view = { key = key, workspace = copy(workspace), members = members, sessions = sessions, claims = #list(data.claims) }
	self:emit(nil)
	return true
end

-- Workspace-ul afișat în secțiunea Workspace: cel privit (meta din vizualizare sau rândul din sumar), altfel jocul deschis.
function Store:viewedWorkspace()
	local key = self.viewWorkspace
	if not key then return self.workspace end
	if self.view and self.view.key == key then return self.view.workspace end
	return self:workspaceRow(key) or { key = key, name = key }
end

function Store:workspaceLabel()
	return Store.workspaceName(self:viewedWorkspace())
end

local function memberBefore(a, b)
	if a.me ~= b.me then return a.me end
	local seenA, seenB = a.lastSeen or 0, b.lastSeen or 0
	if seenA ~= seenB then return seenA > seenB end
	if a.name ~= b.name then return a.name < b.name end
	return a.deviceId < b.deviceId
end

-- Prezenții din workspace-ul afișat („tu” primul, apoi după ultima activitate); în vizualizarea altui workspace, doar după ce datele au sosit.
function Store:membersInView()
	local source = self.members
	if self.viewWorkspace then source = if self.view and self.view.key == self.viewWorkspace then self.view.members else {} end
	local result = table.move(source, 1, #source, 1, {})
	table.sort(result, memberBefore)
	return result
end

-- Sesiunile (meta) ale workspace-ului privit; pentru workspace-ul curent sesiunile sunt ferestre (sessionsMine/sessionsOthers).
function Store:sessionsInView()
	if not self.viewWorkspace or not self.view or self.view.key ~= self.viewWorkspace then return {} end
	return table.move(self.view.sessions, 1, #self.view.sessions, 1, {})
end

-- Ferestrele proprii (Mod S și sesiunile din terminal ale acestui dispozitiv), în ordinea creării.
function Store:sessionsMine()
	local result = {}
	for _, id in ipairs(self.order) do
		local window = self.windows[id]
		if not window.remote then table.insert(result, window) end
	end
	return result
end

-- Sesiunile colegilor din workspace-ul curent (oglindite prin hub, doar de citit).
function Store:sessionsOthers()
	local result = {}
	for _, id in ipairs(self.order) do
		local window = self.windows[id]
		if window.remote then table.insert(result, window) end
	end
	return result
end

-- Textul stării hub-ului (DESIGN.md §6.2) și rolul de culoare; fără daemon, starea hub-ului nu este cunoscută.
function Store:hubStatusText()
	if not self.connected then return "Daemon-ul nu răspunde. Pornește CLI-ul cu pluginul Studio Harness sau Start-Daemon.cmd.", "neutral" end
	return Store.hubText(self.hub)
end

function Store:dismissTerminal(id)
	local window = self.windows[id]
	if not window or window.kind ~= "terminal" then return false end
	if window.job then self:setVisible(id, false); return false end
	self.dismissedTerminal[window.terminalJobId] = true
	self.windows[id] = nil
	local index = table.find(self.order, id)
	if index then table.remove(self.order, index) end
	self:emit(id)
	return true
end

function Store:remove(id)
	local window = self.windows[id]
	if not window or window.job or window.request or window.cancelling then return false end
	if window.kind == "terminal" then return self:dismissTerminal(id) end
	self.windows[id] = nil
	local index = table.find(self.order, id)
	if index then table.remove(self.order, index) end
	self:emit(id)
	return true
end

-- Handoff-ul metamorfozei (faza 5): starea locală ca tabel simplu, copiat, fără token și fără referințe la obiectele store-ului.
function Store:serialize()
	local windows = {}
	for _, id in ipairs(self.order) do
		local window = self.windows[id]
		local messages, approvals = {}, {}
		for _, message in ipairs(window.messages) do
			table.insert(messages, { author = message.author, text = message.text, kind = message.kind, clipped = message.clipped == true })
		end
		for _, approval in ipairs(window.approvals) do
			table.insert(approvals, { id = approval.id, jobId = approval.jobId, tool = approval.tool, arguments = copy(approval.arguments),
				blocked = approval.blocked == true, inactive = approval.inactive == true })
		end
		local request = window.request
		table.insert(windows, {
			id = window.id, kind = window.kind, sessionKind = window.sessionKind, name = window.name, provider = window.provider,
			developer = window.developer, remote = window.remote, mine = window.mine, machine = window.machine,
			owner = copy(window.owner), workspace = window.workspace, started = window.started,
			studioId = window.studioId, studioName = window.studioName, cwd = window.cwd, cursor = window.cursor,
			sessionId = window.sessionId, terminalJobId = window.terminalJobId, locked = window.locked, state = window.state,
			job = if window.job then { id = window.job.id, after = window.job.after, state = window.job.state } else nil,
			request = if request then { id = request.id, payload = copy(request.payload), bridgeId = request.bridgeId,
				draftRevision = request.draftRevision, messageIndex = table.find(window.messages, request.message) } else nil,
			draft = window.draft, draftRevision = window.draftRevision, visible = window.visible,
			messages = messages, approvals = approvals, claims = copy(window.claims),
		})
	end
	local studios, dismissed = {}, {}
	for _, studio in ipairs(self.studios) do table.insert(studios, { id = studio.id, name = studio.name }) end
	for jobId in pairs(self.dismissedTerminal) do table.insert(dismissed, jobId) end
	return { version = Store.HANDOFF_VERSION, windows = windows, studios = studios, dismissedTerminal = dismissed,
		defaultStudioId = self.defaultStudioId, defaultProvider = self.defaultProvider, developer = self.developer, bridgeId = self.bridgeId,
		machine = self.machine, identity = copy(self.identity), workspace = copy(self.workspace), hub = copy(self.hub), viewWorkspace = self.viewWorkspace }
end

local function restoreMessages(list)
	local messages = {}
	for _, entry in ipairs(if type(list) == "table" then list else {}) do
		if type(entry) == "table" and type(entry.text) == "string" then
			table.insert(messages, { author = if type(entry.author) == "string" then Store.prefix(entry.author, 120) else "Status",
				text = Store.prefix(entry.text, Store.MAX_TEXT), kind = if type(entry.kind) == "string" then entry.kind else "text", clipped = entry.clipped == true })
		end
	end
	while #messages > Store.MAX_MESSAGES do table.remove(messages, 1) end
	return messages
end

-- Proprietarul dintr-un handoff: forma 1.0 (`owner`) sau câmpurile 0.8 ale ferestrei (`developer`, `memberId`, `machine`).
local function restoreOwner(entry)
	local owner = entry.owner
	if type(owner) == "table" then
		return Store.ownerFrom({ developer = owner.name, device_id = owner.deviceId, machine = owner.machine, roblox_user_id = owner.userId })
	end
	return Store.ownerFrom({ developer = entry.developer, member_id = entry.memberId, machine = entry.machine })
end

-- O fereastră din handoff; câmpurile lipsă sau invalide revin la implicit, o intrare imposibilă întoarce nil.
local function restoreWindow(entry, index)
	if type(entry) ~= "table" or type(entry.id) ~= "string" or entry.id == "" or #entry.id > 200 then return nil end
	local kind = if entry.kind == "terminal" then "terminal" else "studio"
	local terminalJobId = optionalText(entry.terminalJobId, 200) or string.match(entry.id, "^terminal:(.+)$")
	if kind == "terminal" and not terminalJobId then return nil end
	local name = if type(entry.name) == "string" and string.find(entry.name, "%S") then Store.prefix((string.gsub(entry.name, "%c", " ")), 80)
		elseif kind == "terminal" then "Terminal" else "Sesiune " .. index
	local window = newWindow(entry.id, kind, if Store.PROVIDERS[entry.provider] then entry.provider else "claude", name)
	window.sessionKind = if entry.sessionKind == "studio" or entry.sessionKind == "terminal" then entry.sessionKind else kind
	window.developer, window.machine = optionalText(entry.developer, 60), optionalText(entry.machine, 64)
	window.remote = entry.remote == true
	window.mine = not window.remote
	window.owner, window.workspace = restoreOwner(entry), optionalText(entry.workspace, 64)
	window.started = integer(entry.started)
	window.studioId, window.studioName, window.cwd = optionalText(entry.studioId, 128), optionalText(entry.studioName, 200), optionalText(entry.cwd, 200)
	window.cursor = integer(entry.cursor) or 0
	window.messages, window.claims = restoreMessages(entry.messages), stringList(entry.claims)
	window.visible = entry.visible ~= false
	window.draft = if kind == "studio" and type(entry.draft) == "string" then Store.prefix(entry.draft, Store.MAX_TEXT) else ""
	window.draftRevision = integer(entry.draftRevision) or 0
	if kind == "terminal" then window.terminalJobId, window.locked = terminalJobId, true
	else
		window.sessionId = optionalText(entry.sessionId, 200)
		window.locked = entry.locked == true or window.sessionId ~= nil
	end
	local job, state = entry.job, if type(entry.state) == "string" and Store.STATES[entry.state] then entry.state else nil
	if type(job) == "table" and type(job.id) == "string" and job.id ~= "" and #job.id <= 200 and (kind == "studio" or job.id == terminalJobId)
		and not (type(job.state) == "string" and Store.TERMINAL[job.state]) then
		local jobState = if type(job.state) == "string" and Store.STATES[job.state] and job.state ~= "draft" then job.state else "running"
		window.job = { id = job.id, after = integer(job.after) or 0, state = jobState, message = nil }
		window.state, window.locked = jobState, true
		-- Ultimul mesaj al asistentului rămâne ținta evenimentelor `text` următoare, ca răspunsul să nu se rupă în două.
		local last = window.messages[#window.messages]
		if last and last.kind == "assistant" then window.job.message = last end
		for _, approval in ipairs(if type(entry.approvals) == "table" then entry.approvals else {}) do
			if type(approval) == "table" and type(approval.id) == "string" and approval.id ~= "" and approval.jobId == job.id and #window.approvals < 32 then
				-- `inspectable` nu se moștenește: ApprovalView-ul nou măsoară din nou argumentele înainte de „Permite”.
				table.insert(window.approvals, { id = approval.id, jobId = job.id, tool = approval.tool, arguments = copy(approval.arguments), inactive = approval.inactive == true,
					inspectable = false, blocked = approval.blocked == true or type(approval.tool) ~= "string" or approval.tool == "" or type(approval.arguments) ~= "table" })
			end
		end
	else
		local ended = if type(job) == "table" and type(job.state) == "string" and Store.TERMINAL[job.state] then job.state else nil
		if kind == "terminal" then window.state = if state and Store.TERMINAL[state] then state else ended or "completed"
		else window.state = if state and (state == "draft" or Store.TERMINAL[state]) then state else ended or "draft" end
	end
	local request = entry.request
	if kind == "studio" and not window.job and type(request) == "table" and type(request.id) == "string" and request.id ~= "" and #request.id <= 200
		and type(request.payload) == "table" and type(request.payload.prompt) == "string" then
		-- Cererea era în zbor în aplicația veche: devine neconfirmată, reluabilă doar cu confirmare explicită (același ID).
		local payload = copy(request.payload)
		payload.client_request_id = request.id
		local message = window.messages[integer(request.messageIndex, 1) or 0]
		if message then message.author = "Tu · neconfirmat" end
		window.request = { id = request.id, payload = payload, bridgeId = optionalText(request.bridgeId, 128),
			draftRevision = integer(request.draftRevision) or -1, state = "uncertain", message = message }
		window.state, window.locked = "uncertain", true
	end
	return window
end

-- Tolerant: orice date (chiar nil sau tabel corupt) dau un store valid; ferestrele imposibile sunt sărite, joburile duplicate păstrate o singură dată.
-- Snapshot-urile 0.8 nu au identitate, workspace sau hub: câmpurile rămân nil/implicite până la primul `/v1/status`.
function Store.deserialize(data)
	local store = Store.new()
	if type(data) ~= "table" then return store end
	store.defaultProvider = if Store.PROVIDERS[data.defaultProvider] then data.defaultProvider else "claude"
	store.developer, store.bridgeId = optionalText(data.developer, 60), optionalText(data.bridgeId, 128)
	store.machine = cleanText(data.machine, 64)
	local identity, workspace, hub = data.identity, data.workspace, data.hub
	if type(identity) == "table" then store.identity = Store.identityFrom({ user_id = identity.userId, name = identity.name, avatar = identity.avatar }) end
	if type(workspace) == "table" then
		store.workspace = Store.workspaceFrom({ key = workspace.key, name = workspace.name, place_id = workspace.placeId, game_id = workspace.gameId,
			creator_id = workspace.creatorId, creator_type = workspace.creatorType })
	end
	if type(hub) == "table" then
		store.hub = Store.hubFrom({ status = hub.status, url = hub.url, hub_id = hub.hubId, device_id = hub.deviceId, error = hub.error,
			enrollment = hub.enrollment, last_sync = hub.lastSync }, hub.panelUrl)
	end
	local view = data.viewWorkspace
	if type(view) == "string" and view ~= "" and #view <= 64 and view ~= store:workspaceKey() then store.viewWorkspace = view end
	pcall(store.applyStudios, store, data.studios)
	if store:studio(data.defaultStudioId) then store.defaultStudioId = data.defaultStudioId end
	for _, jobId in ipairs(if type(data.dismissedTerminal) == "table" then data.dismissedTerminal else {}) do
		if type(jobId) == "string" and jobId ~= "" then store.dismissedTerminal[jobId] = true end
	end
	local seenJobs = {}
	for _, entry in ipairs(if type(data.windows) == "table" then data.windows else {}) do
		if #store.order >= Store.MAX_WINDOWS then break end
		local ok, window = pcall(restoreWindow, entry, #store.order + 1)
		if ok and window and not store.windows[window.id] and not (window.job and seenJobs[window.job.id]) then
			if window.job then seenJobs[window.job.id] = true end
			store.windows[window.id] = window
			table.insert(store.order, window.id)
		end
	end
	return store
end

return Store
