-- Studio Harness HubView 1.0.0 — dock-ul pluginului (design/DESIGN.md §10.1–§10.5): antet cu identitatea Roblox și starea hub-ului,
-- banner condiționat, workspace cu prezenți și „Alte workspace-uri”, acțiunea principală, sesiunile grupate „Ale tale” / „În workspace”,
-- aprobarea inline, claims-urile, jurnalul și subsolul cu secțiunea pliabilă „Avansat”.
-- Toate piesele vizuale vin din Theme.luau (nicio culoare literală aici), datele din SessionStore, acțiunile din BridgeController.
-- Re-randarea reciclează instanțele după cheie (id de fereastră, job, cale, dispozitiv): la fiecare `store:emit` se schimbă doar textele,
-- iar conexiunile intrărilor reciclate sunt ținute de intrare (nu de view), ca lista să nu adune conexiuni moarte.
local Theme = require(script.Parent.Theme)
local Hub = {}
Hub.__index = Hub

local VISIBILITY_SETTING = "StudioHarnessMatrixHubVisible"
local WIDGET_ID = "StudioHarnessMatrixHub040"
-- Versiunea afișată în antet; se bumpează odată cu antetul `App` din Main.luau (faza 9).
local APP_VERSION = "1.0.0"
local MAX_WINDOWS = 32
local HEADER_ACTIONS = 112 -- lățimea rezervată în antet pentru „v1.0.0” și butonul „Panou”
local HEADER_INDENT = 36 -- avatarul (28) plus spațiul de după el: linia stării hub-ului se aliniază sub nume

-- Texte normative (DESIGN.md §12.1, §12.2).
local DAEMON_DOWN = "Daemon-ul nu răspunde. Pornește CLI-ul cu pluginul Studio Harness sau Start-Daemon.cmd."
local OLD_DAEMON = "Pornește daemon-ul 1.0 sau mai nou (terminal cu pluginul Claude Code sau Start-Daemon.cmd)."
local EMPTY_MINE = "Nicio sesiune încă. Alege providerul și apasă „Sesiune nouă”, sau pornește CLI-ul din terminal cu pluginul activ."
local EMPTY_CLAIMS = "Nicio zonă revendicată. Agenții cer hub_claim înainte de a modifica scena."
local EMPTY_JOURNAL = "Nicio modificare înregistrată încă în acest workspace."
local EMPTY_WORKSPACES = "Doar acest workspace este folosit acum."
local EMPTY_MEMBERS = "Nimeni prezent acum."
local UNPUBLISHED = "Fișier nepublicat. Publică locul ca să apară în hub pentru ceilalți."
local DISCONNECT_CONFIRM = "Deconectezi daemon-ul? Sesiunile deschise rămân, dar nu mai primesc mesaje până la reconectare."
local APPROVAL_HINT = "Deschide sesiunea pentru argumentele complete."
local APPROVAL_BLOCKED = "Afișarea integrală depășește limita locală. Numai Refuză este disponibil."
local CODE_PLACEHOLDER = "Lipește codul din daemon.log"
local CODE_HINT = "Apasă Enter ca să conectezi daemon-ul cu codul lipit."
-- Codul refuzat de daemon (401): sursa lui (`controller:tokenSource()`) spune de unde trebuie luat unul nou.
local CODE_REFUSED = { loader = "Codul livrat la instalare a fost refuzat. ", settings = "Codul salvat în setări a fost refuzat. ",
	manual = "Codul lipit a fost refuzat. " }

-- Un bloc al listei principale: piese apropiate (pas 8) care formează împreună un singur rând al dock-ului.
local function block(parent, name, order, gap)
	local root = Theme.new("Frame", { Name = name, BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y, LayoutOrder = order }, parent)
	Theme.list(root, gap or Theme.SPACE.sm)
	return root
end

-- Etichetă care se întinde doar cât textul ei, cu o limită peste care se trunchiază (antet, chip-uri de proprietar).
local function inlineLabel(parent, options, maxWidth)
	local label = Theme.label(parent, "", options)
	label.AutomaticSize = Enum.AutomaticSize.X
	label.Size = UDim2.fromOffset(0, options.height or 20)
	Theme.new("UISizeConstraint", { MaxSize = Vector2.new(maxWidth, options.height or 20) }, label)
	return label
end

-- Rând orizontal care trece pe linia următoare când nu mai încape (chip-urile unui card la 320 px).
local function wrapRow(parent, name, order)
	local root = Theme.new("Frame", { Name = name, BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y, LayoutOrder = order }, parent)
	local layout = Theme.list(root, Theme.SPACE.xs, true)
	layout.HorizontalAlignment = Enum.HorizontalAlignment.Left
	-- `Wraps` există din 2023; fără el rândul rămâne pe o linie și se lasă tăiat de marginea dock-ului.
	pcall(function() layout.Wraps = true end)
	return root
end

-- Calea unei modificări din jurnal: ultimele trei segmente când depășește 48 de caractere (DESIGN.md §3.3).
local function shortPath(path)
	local text = tostring(path or "")
	if #text <= 48 then return text end
	local segments = string.split(text, ".")
	if #segments > 3 then
		return "…" .. segments[#segments - 2] .. "." .. segments[#segments - 1] .. "." .. segments[#segments]
	end
	return Theme.fit(text, 47)
end

-- Căile unei intrări de jurnal: primele două, plus „+N” pentru restul.
local function journalPaths(paths)
	local shown = {}
	for index, path in ipairs(paths) do
		if index > 2 then break end
		table.insert(shown, shortPath(path))
	end
	local text = table.concat(shown, ", ")
	if #paths > 2 then text = text .. " +" .. (#paths - 2) end
	return if text ~= "" then text else "—"
end

-- Rezumatul argumentelor unui apel, fără JSON (HttpService nu are ce căuta într-un view): perechi `cheie: valoare` scurtate.
-- Argumentele complete rămân în fereastra sesiunii; aici este doar cât să recunoști apelul.
local function argumentSummary(arguments)
	if type(arguments) ~= "table" then return "Argumentele nu pot fi afișate." end
	local keys = {}
	for key in pairs(arguments) do
		if type(key) == "string" then table.insert(keys, key) end
	end
	table.sort(keys)
	local parts = {}
	for _, key in ipairs(keys) do
		if #parts >= 3 then break end
		local value = arguments[key]
		local text
		if type(value) == "string" then text = Theme.fit((string.gsub(value, "%s+", " ")), 36)
		elseif type(value) == "number" or type(value) == "boolean" then text = tostring(value)
		elseif type(value) == "table" then
			local count = 0
			for _ in pairs(value) do count += 1 end
			text = Theme.countLabel(count, "câmp", "câmpuri")
		else text = "…" end
		table.insert(parts, key .. ": " .. text)
	end
	if #parts == 0 then return "Fără argumente." end
	return Theme.fit(table.concat(parts, " · "), 80)
end

-- Modelul unui card de sesiune, comun ferestrelor proprii și sesiunilor citite din alt workspace.
local function windowModel(window)
	local _, label, role = Theme.sessionState(window)
	local owner = window.owner
	return { key = window.id, id = window.id, title = Theme.fit(window.name, 60), provider = window.provider,
		kind = if window.sessionKind == "studio" then "studio" else "terminal", owner = owner,
		who = if window.remote then Theme.fit(tostring((owner and owner.name) or "?"), 16) else "tu",
		state = label, role = role, approvals = #window.approvals, minimized = not window.visible, since = window.started }
end

local function sessionRowModel(row)
	local _, label, role = Theme.sessionState(row)
	return { key = row.jobId, id = row.jobId, title = Theme.fit(row.name, 60), provider = row.provider,
		kind = row.kind, owner = row.owner,
		who = if row.mine then "tu" else Theme.fit(tostring((row.owner and row.owner.name) or "?"), 16),
		state = label, role = role, approvals = if row.pendingApproval then 1 else 0, minimized = false, since = row.started }
end

function Hub.new(pluginObject, store, controller)
	local self = setmetatable({ connections = {}, destroyed = false, store = store, controller = controller,
		mineCards = {}, othersCards = {}, viewCards = {}, approvalCards = {}, claimRows = {}, journalRows = {},
		workspaceRows = {}, memberRows = {}, viewTab = "members", viewTabShown = "members", confirming = false,
		bannerKey = "", dismissedBanner = nil, confirmShown = false, providerShown = store.defaultProvider }, Hub)
	local saved, visible = pcall(function() return pluginObject:GetSetting(VISIBILITY_SETTING) end)
	local initialVisible = if saved and type(visible) == "boolean" then visible else true
	local info = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Left, initialVisible, true, 336, 800, 320, 480)
	self.widget = pluginObject:CreateDockWidgetPluginGuiAsync(WIDGET_ID, info)
	self.widget.Title, self.widget.Name = "Studio Harness", "StudioHarnessHub"
	self.root = Theme.frame(self.widget, "Hub")
	self.scroll = Theme.scroll(self.root, "Content")
	Theme.new("UIPadding", { PaddingLeft = UDim.new(0, Theme.SPACE.lg), PaddingRight = UDim.new(0, Theme.SPACE.lg),
		PaddingTop = UDim.new(0, Theme.SPACE.md), PaddingBottom = UDim.new(0, Theme.SPACE.lg) }, self.scroll)
	Theme.list(self.scroll, Theme.SPACE.xl)
	self:buildHeader(1)
	self.banner = Theme.banner(self.scroll, { name = "Banner", close = true })
	self.banner.root.LayoutOrder = 2
	Theme.click(self, self.banner.close, function()
		self.dismissedBanner = self.bannerKey
		self.banner.hide()
	end)
	self:buildWorkspace(3)
	self:buildAction(4)
	self:buildSessions(5)
	self:buildApprovals(6)
	self:buildClaims(7)
	self:buildJournal(8)
	self:buildFooter(9)
	Theme.connect(self, self.widget:GetPropertyChangedSignal("Enabled"), function()
		pcall(function() pluginObject:SetSetting(VISIBILITY_SETTING, self.widget.Enabled) end)
	end)
	self:update()
	return self
end

-- Activarea unui buton dintr-o intrare reciclabilă: conexiunea aparține intrării și moare odată cu ea.
function Hub:bind(button, callback)
	Theme.enabled(button, true)
	return button.Activated:Connect(function()
		if not self.destroyed and button:GetAttribute("EnabledAction") then callback() end
	end)
end

-- 1. Antet: avatar 28, numele contului, punctul stării hub, versiunea și butonul „Panou” (DESIGN.md §10.1 rândul 1).
function Hub:buildHeader(order)
	local root = Theme.new("Frame", { Name = "Header", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 44),
		LayoutOrder = order }, self.scroll)
	local identity = Theme.new("Frame", { Name = "Identity", BackgroundTransparency = 1,
		Size = UDim2.new(1, -HEADER_ACTIONS, 0, 28), ClipsDescendants = true }, root)
	Theme.list(identity, Theme.SPACE.sm, true)
	self.avatarHolder = Theme.new("Frame", { Name = "Avatar", BackgroundTransparency = 1, Size = UDim2.fromOffset(28, 28),
		LayoutOrder = 1 }, identity)
	self.name = inlineLabel(identity, { size = "md", font = "ui", height = 28, truncate = true, name = "Name" }, 112)
	self.name.LayoutOrder = 2
	self.hubDot = Theme.dot(identity, "neutral", 10)
	self.hubDot.LayoutOrder = 3
	-- Eticheta scurtă a stării hub-ului stă sub nume, nu lângă el: la 320 px un rând nu ține și numele, și starea.
	self.hubState = Theme.label(root, "", { size = "xs", token = "text2", height = 14, truncate = true, name = "HubState" })
	self.hubState.Position = UDim2.fromOffset(HEADER_INDENT, 30)
	self.hubState.Size = UDim2.new(1, -(HEADER_INDENT + HEADER_ACTIONS), 0, 14)
	local actions = Theme.new("Frame", { Name = "Actions", BackgroundTransparency = 1, AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, 0, 0.5, 0), Size = UDim2.new(0, 0, 0, 28), AutomaticSize = Enum.AutomaticSize.X }, root)
	Theme.list(actions, Theme.SPACE.sm, true)
	local version = inlineLabel(actions, { size = "xs", token = "text2", height = 28, name = "Version" }, 64)
	version.Text, version.LayoutOrder = "v" .. APP_VERSION, 1
	self.panel = Theme.button(actions, "Panou", { kind = "ghost", size = "sm", width = "auto", name = "Panel" })
	self.panel.LayoutOrder = 2
	Theme.click(self, self.panel, function() self.controller:openPanel() end)
end

-- 3. Workspace: jocul deschis (sau cel privit), prezenții, lista celorlalte workspace-uri și vizualizarea read-only (§10.1 rândul 3, §10.3).
function Hub:buildWorkspace(order)
	local root = block(self.scroll, "Workspace", order)
	self.back = Theme.button(root, "Înapoi", { kind = "ghost", size = "sm", width = "auto", name = "Back" })
	self.back.LayoutOrder = 1
	Theme.click(self, self.back, function() self.controller:viewWorkspace(nil) end)
	local title = Theme.new("Frame", { Name = "Title", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 22),
		LayoutOrder = 2 }, root)
	self.workspaceName = Theme.label(title, "", { size = "lg", font = "ui", height = 22, truncate = true, name = "Name" })
	self.readOnly = Theme.chip(title, "doar de citit", "neutral")
	self.readOnly.AnchorPoint, self.readOnly.Position = Vector2.new(1, 0.5), UDim2.new(1, 0, 0.5, 0)
	self.placeLine = Theme.label(root, "", { size = "xs", font = "mono", token = "text2", height = 16, truncate = true, name = "Place" })
	self.placeLine.LayoutOrder = 3
	self.presence = Theme.avatarRow(root, {}, { size = 24, name = "Presence" })
	self.presence.root.LayoutOrder = 4
	self.workspaceNote = Theme.empty(root, "", { name = "WorkspaceNote" })
	self.workspaceNote.root.LayoutOrder = 5
	self.workspaces = Theme.section(root, "Alte workspace-uri", { collapsible = true, open = false, name = "Workspaces" })
	self.workspaces.root.LayoutOrder = 6
	self.workspacesEmpty = Theme.empty(self.workspaces.body, EMPTY_WORKSPACES, { name = "NoWorkspaces" })
	self.workspacesEmpty.root.LayoutOrder = 1000
	Theme.click(self, self.workspaces.header, function()
		self.workspaces.setOpen(not self.workspaces.open)
		if self.workspaces.open then self.controller:refreshWorkspaces() end
	end)
	self.viewTabs = Theme.tab(root, { { key = "members", text = "Prezenți" }, { key = "sessions", text = "Sesiuni" } },
		self.viewTab, function(key)
			self.viewTab = key
			self:update()
		end)
	self.viewTabs.root.LayoutOrder = 7
	self.viewMembers = block(root, "ViewMembers", 8, Theme.SPACE.xs)
	self.viewMembersEmpty = Theme.empty(self.viewMembers, EMPTY_MEMBERS, { name = "NoMembers" })
	self.viewMembersEmpty.root.LayoutOrder = 1000
	self.viewSessions = block(root, "ViewSessions", 9)
	self.viewSessionsEmpty = Theme.empty(self.viewSessions, "", { name = "NoSessions" })
	self.viewSessionsEmpty.root.LayoutOrder = 1000
	self.viewError = Theme.label(root, "", { size = "sm", token = "text2", name = "ViewError" })
	self.viewError.LayoutOrder = 10
end

-- 4. Acțiunea principală: providerul sesiunilor noi, butonul primar și disponibilitatea CLI-urilor (§10.1 rândul 4).
function Hub:buildAction(order)
	local root = block(self.scroll, "Action", order)
	self.provider = Theme.segmented(root, { { key = "claude", text = Theme.names.claude }, { key = "codex", text = Theme.names.codex } },
		self.store.defaultProvider, function(key)
			self.store.defaultProvider = key
			self.store:emit(nil)
		end)
	self.provider.root.LayoutOrder = 1
	self.create = Theme.button(root, "Sesiune nouă", { kind = "primary", size = "lg", name = "NewSession" })
	self.create.LayoutOrder = 2
	Theme.click(self, self.create, function() self.controller:createWindow() end)
	local availability = block(root, "Availability", 3, Theme.SPACE.xs)
	self.cli = {}
	for index, provider in ipairs({ "claude", "codex" }) do
		local line = Theme.new("Frame", { Name = "Cli_" .. provider, BackgroundTransparency = 1,
			Size = UDim2.new(1, 0, 0, 16), LayoutOrder = index }, availability)
		Theme.list(line, Theme.SPACE.xs, true)
		local dot = Theme.dot(line, "neutral", 8)
		dot.LayoutOrder = 1
		local label = inlineLabel(line, { size = "xs", token = "text2", height = 16, truncate = true, name = "Text" }, 240)
		label.LayoutOrder = 2
		self.cli[provider] = { dot = dot, label = label, role = "neutral" }
	end
	self.createHint = Theme.label(root, "", { size = "xs", token = "text2", name = "Reason" })
	self.createHint.LayoutOrder = 4
end

-- 5. Sesiuni: „Ale tale” (ferestrele acestui dispozitiv) și „În workspace” (sesiunile colegilor, doar de citit).
function Hub:buildSessions(order)
	local root = block(self.scroll, "Sessions", order, Theme.SPACE.md)
	self.sessions = Theme.section(root, "Sesiuni", { name = "Mine" })
	self.sessions.root.LayoutOrder = 1
	self.sessions.subtitle("Ale tale")
	self.mineEmpty = Theme.empty(self.sessions.body, EMPTY_MINE, { name = "NoSessions" })
	self.mineEmpty.root.LayoutOrder = 1000
	self.others = Theme.section(root, "", { name = "Others" })
	self.others.root.LayoutOrder = 2
	self.others.header.Visible = false
	self.others.subtitle("În workspace")
	self.othersEmpty = Theme.empty(self.others.body, "", { name = "NoOthers" })
	self.othersEmpty.root.LayoutOrder = 1000
end

-- 6. Aprobare: un card per fereastră proprie care așteaptă o decizie (§10.1 rândul 6).
function Hub:buildApprovals(order)
	self.approvals = Theme.section(self.scroll, "Aprobare", { name = "Approvals" })
	self.approvals.root.LayoutOrder = order
end

-- 7. Claims: zonele revendicate din workspace-ul curent, cu „Eliberează” pe ale tale.
function Hub:buildClaims(order)
	self.claims = Theme.section(self.scroll, "Claims", { name = "Claims" })
	self.claims.root.LayoutOrder = order
	self.claimsEmpty = Theme.empty(self.claims.body, EMPTY_CLAIMS, { name = "NoClaims" })
	self.claimsEmpty.root.LayoutOrder = 1000
end

-- 8. Jurnal: ultimele modificări înregistrate în workspace.
function Hub:buildJournal(order)
	self.journal = Theme.section(self.scroll, "Jurnal", { name = "Journal" })
	self.journal.root.LayoutOrder = order
	self.journalEmpty = Theme.empty(self.journal.body, EMPTY_JOURNAL, { name = "NoJournal" })
	self.journalEmpty.root.LayoutOrder = 1000
end

-- 9. Subsol: linia daemon-ului și secțiunea pliabilă „Avansat” (cod de asociere, Studio țintă, deconectare) — §10.4.
function Hub:buildFooter(order)
	local root = block(self.scroll, "Footer", order, Theme.SPACE.md)
	local separator = Theme.new("Frame", { Name = "Separator", Size = UDim2.new(1, 0, 0, 1), BorderSizePixel = 0,
		LayoutOrder = 1 }, root)
	Theme.paint(separator, { BackgroundColor3 = "borderStrong" })
	self.daemonLine = Theme.label(root, "", { size = "xs", token = "text2", name = "Daemon" })
	self.daemonLine.LayoutOrder = 2
	self.daemonToken = "text2"
	self.advanced = Theme.section(root, "Avansat", { collapsible = true, open = false, name = "Advanced" })
	self.advanced.root.LayoutOrder = 3
	Theme.click(self, self.advanced.header, function() self.advanced.setOpen(not self.advanced.open) end)
	local body = self.advanced.body
	self.codeField = block(body, "Code", 1, Theme.SPACE.xs)
	Theme.label(self.codeField, "Cod de asociere", { size = "xs", token = "text2", height = 16, name = "Label" }).LayoutOrder = 1
	self.codeInput = Theme.input(self.codeField, { placeholder = CODE_PLACEHOLDER, mono = true, name = "Code" })
	self.codeInput.LayoutOrder = 2
	self.codeHint = Theme.label(self.codeField, CODE_HINT, { size = "xs", token = "text2", name = "Hint" })
	self.codeHint.LayoutOrder, self.codeHintText = 3, CODE_HINT
	Theme.connect(self, self.codeInput.FocusLost, function(enterPressed)
		if not enterPressed then return end
		local code = self.codeInput.Text
		if code == "" then return end
		self.codeInput.Text = ""
		self.controller:connect(code, "manual")
	end)
	local studioField = block(body, "Studio", 2, Theme.SPACE.xs)
	Theme.label(studioField, "Studio țintă", { size = "xs", token = "text2", height = 16, name = "Label" }).LayoutOrder = 1
	self.studioButton = Theme.button(studioField, "", { name = "TargetStudio" })
	self.studioButton.LayoutOrder = 2
	Theme.click(self, self.studioButton, function() self:cycleStudio() end)
	local deviceField = block(body, "Device", 3, Theme.SPACE.xs)
	Theme.label(deviceField, "Dispozitiv", { size = "xs", token = "text2", height = 16, name = "Label" }).LayoutOrder = 1
	self.deviceBox = Theme.input(deviceField, { mono = true, name = "DeviceId" })
	self.deviceBox.LayoutOrder, self.deviceBox.TextEditable = 2, false
	self.disconnect = Theme.button(body, "Deconectează daemon-ul", { kind = "danger", name = "Disconnect" })
	self.disconnect.LayoutOrder = 4
	Theme.click(self, self.disconnect, function()
		self.confirming = true
		self:update()
	end)
	self.reconnect = Theme.button(body, "Conectează daemon-ul", { name = "Reconnect" })
	self.reconnect.LayoutOrder = 5
	Theme.click(self, self.reconnect, function() self.controller:connect("") end)
	self.confirm = Theme.banner(body, { role = "danger", name = "DisconnectConfirm" })
	self.confirm.root.LayoutOrder = 6
	local choices = Theme.new("Frame", { Name = "Choices", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 28),
		LayoutOrder = 2 }, self.confirm.label.Parent)
	Theme.list(choices, Theme.SPACE.sm, true)
	local confirmButton = Theme.button(choices, "Deconectează", { kind = "danger", size = "sm",
		width = UDim.new(0.5, -Theme.SPACE.xs), name = "Confirm" })
	confirmButton.LayoutOrder = 1
	Theme.click(self, confirmButton, function()
		self.confirming = false
		self.controller:stop()
		self:update()
	end)
	local backButton = Theme.button(choices, "Înapoi", { size = "sm", width = UDim.new(0.5, -Theme.SPACE.xs), name = "Cancel" })
	backButton.LayoutOrder = 2
	Theme.click(self, backButton, function()
		self.confirming = false
		self:update()
	end)
end

-- Ciclează suprascrierea Studio-ului țintă: automat → prima instanță → … → automat.
function Hub:cycleStudio()
	local store = self.store
	if #store.studios == 0 then return end
	local index = 0
	for position, studio in ipairs(store.studios) do
		if studio.id == store.defaultStudioId then index = position end
	end
	local nextIndex = index + 1
	self.controller:setDefaultStudio(if nextIndex > #store.studios then nil else store.studios[nextIndex].id)
end

function Hub:update()
	if self.destroyed then return end
	self:updateHeader()
	self:updateBanner()
	self:updateWorkspace()
	self:updateAction()
	self:updateSessions()
	self:updateApprovals()
	self:updateClaims()
	self:updateJournal()
	self:updateFooter()
end

function Hub:updateHeader()
	local store = self.store
	local identity = store.identity
	local key = if identity then tostring(identity.userId) .. "|" .. tostring(identity.avatar or "") else "-"
	if key ~= self.avatarKey then
		self.avatarKey = key
		if self.avatar then self.avatar:Destroy() end
		self.avatar = Theme.avatar(self.avatarHolder, identity, { size = 28 })
	end
	self.name.Text = if identity then Theme.fit(identity.name, 16) else store.UNKNOWN_NAME
	local status = if store.connected then store.hub.status else nil
	local role = if status then (Theme.HUB_ROLE[status] or "neutral") else "neutral"
	if role ~= self.hubDotRole then
		self.hubDotRole = role
		Theme.recolor(self.hubDot, role)
	end
	-- Eticheta scurtă a stării apare doar când hub-ul nu este conectat (în Studio nu există tooltip-uri, §14.3).
	local label = if status and status ~= "approved" then (Theme.hubStates[status] or "") else ""
	self.hubState.Text = label
	self.hubState.Visible = label ~= ""
	Theme.enabled(self.panel, store.connected and not store.connecting and store.compatible and status ~= nil and status ~= "disabled")
end

-- Un singur banner pe ecran: starea hub-ului are prioritate, apoi eșecul metamorfozei, apoi actualizările (DESIGN.md §7.9, §12.2).
function Hub:updateBanner()
	local store = self.store
	local role, text, seconds = nil, nil, nil
	local status = store.hub.status
	if store.connected and (status == "pending" or status == "offline" or status == "revoked") then
		text, role = store:hubStatusText()
	elseif store.bundleError then
		role, text = "danger", "Interfața nouă nu a pornit: " .. store.bundleError .. ". Rulează versiunea anterioară."
	else
		local update = store.update
		if update.state == "installed" then
			role, seconds = "primary", 5
			text = "Actualizare aplicată: " .. (if update.current ~= "" then update.current else "versiune nouă")
				.. ". Interfața s-a reîncărcat fără repornire."
		-- Actualizarea doar disponibilă rămâne în subsol (§6.2): nu cere nimic de la utilizator, deci nu merită un banner.
		elseif update.restartRequired then
			role, text = "info", "Loader nou instalat. Repornește Studio o dată, când ai timp."
		end
	end
	local key = if text then role .. "|" .. text else ""
	if key == self.bannerKey then return end
	self.bannerKey = key
	if text == nil or key == self.dismissedBanner then
		self.banner.hide()
		return
	end
	-- Bannerele de stare persistente nu se închid: dispar când starea se schimbă. Excepție: eșecul metamorfozei, care nu se mai
	-- schimbă singur (rulăm versiunea anterioară până la un bundle nou), deci primește „×”.
	self.banner.close.Visible = role == "info" or seconds ~= nil or store.bundleError ~= nil
	self.banner.show(role, text, seconds)
end

function Hub:updateWorkspace()
	local store, controller = self.store, self.controller
	local viewing = store.viewWorkspace ~= nil
	local workspace = store:viewedWorkspace()
	self.back.Visible = viewing
	if viewing then
		self.back.Text = "Înapoi la " .. Theme.fit(store.workspaceName(store.workspace), 20)
	end
	self.workspaceName.Text = store:workspaceLabel()
	self.readOnly.Visible = viewing
	self.workspaceName.Size = UDim2.new(1, if viewing then -88 else 0, 1, 0)
	self.placeLine.Text = Theme.placeLine(workspace, store.identity)
	local members = store:membersInView()
	self.presence.set(members)
	self.presence.root.Visible = #members > 0
	local note = nil
	if not store.connected then note = DAEMON_DOWN
	elseif not viewing and (workspace == nil or workspace.key == store.LOCAL_WORKSPACE) then note = UNPUBLISHED end
	self.workspaceNote.root.Visible = note ~= nil
	if note then self.workspaceNote.label.Text = note end
	-- Nota UNPUBLISHED începe cu aceeași propoziție ca linia mono: o afișăm o singură dată (§1.4 — fără repetiții).
	self.placeLine.Visible = note ~= UNPUBLISHED
	self:updateWorkspaceList()
	local failure = if viewing then controller:viewError() else nil
	self.viewTabs.root.Visible = viewing
	if self.viewTab ~= self.viewTabShown then
		self.viewTabShown = self.viewTab
		self.viewTabs.set(self.viewTab)
	end
	self.viewError.Visible = failure ~= nil
	self.viewError.Text = failure or ""
	self.viewMembers.Visible = viewing and failure == nil and self.viewTab == "members"
	self.viewSessions.Visible = viewing and failure == nil and self.viewTab == "sessions"
	self:updateMemberRows(if self.viewMembers.Visible then members else {})
	local sessions = if self.viewSessions.Visible then store:sessionsInView() else {}
	local models = {}
	for _, row in ipairs(sessions) do table.insert(models, sessionRowModel(row)) end
	self:syncCards(self.viewSessions, self.viewCards, models, false)
	self.viewSessionsEmpty.root.Visible = self.viewSessions.Visible and #models == 0
	self.viewSessionsEmpty.label.Text = "Nicio sesiune activă în " .. Theme.fit(store:workspaceLabel(), 24) .. "."
	self.viewMembersEmpty.root.Visible = self.viewMembers.Visible and #members == 0
end

function Hub:updateWorkspaceList()
	local store = self.store
	local rows = store.workspaces
	-- Butonul apare doar când există și altceva de privit; contorul numără efectiv celelalte workspace-uri: imediat după
	-- deschiderea unui loc nou (sau pentru un fișier nepublicat) cel curent încă nu apare în sumarul hub-ului.
	local current = store:workspaceKey()
	local others = 0
	for _, workspace in ipairs(rows) do
		if workspace.key ~= current then others += 1 end
	end
	self.workspaces.root.Visible = others > 0 or self.workspaces.open
	self.workspaces.setCount(if others > 0 then others else nil)
	local listed = self.workspaces.open and others > 0
	self.workspacesEmpty.root.Visible = self.workspaces.open and not listed
	if not listed then
		for key, entry in pairs(self.workspaceRows) do
			entry.connection:Disconnect()
			entry.row.root:Destroy()
			self.workspaceRows[key] = nil
		end
		return
	end
	local seen = {}
	for order, workspace in ipairs(rows) do
		local key = workspace.key
		seen[key] = true
		local entry = self.workspaceRows[key]
		if not entry then
			local row = Theme.row(self.workspaces.body, { name = "Workspace", lines = 2, lead = "dot",
				role = "neutral", clickable = true })
			entry = { row = row, role = "neutral" }
			entry.connection = self:bind(row.button, function()
				self.controller:viewWorkspace(if key == store:workspaceKey() then nil else key)
			end)
			self.workspaceRows[key] = entry
		end
		entry.row.root.LayoutOrder = order
		local role = if workspace.membersOnline > 0 then "primary" else "neutral"
		if role ~= entry.role then
			entry.role = role
			Theme.recolor(entry.row.lead, role)
		end
		local meta = Theme.countLabel(workspace.membersOnline, "prezent", "prezenți")
			.. " · " .. Theme.countLabel(workspace.sessionsActive, "sesiune", "sesiuni")
		if workspace.claims > 0 then meta ..= " · " .. Theme.countLabel(workspace.claims, "claim", "claims") end
		entry.row.set(Theme.fit(workspace.name, 40) .. (if key == current then " · curent" else ""), meta)
	end
	for key, entry in pairs(self.workspaceRows) do
		if not seen[key] then
			entry.connection:Disconnect()
			entry.row.root:Destroy()
			self.workspaceRows[key] = nil
		end
	end
end

function Hub:updateMemberRows(members)
	local seen = {}
	for order, member in ipairs(members) do
		-- Contul intră în cheie: avatarul rândului se construiește o singură dată, deci alt cont pe același PC cere alt rând.
		local key = member.deviceId .. "|" .. tostring(member.userId)
		seen[key] = true
		local entry = self.memberRows[key]
		if not entry then
			entry = { row = Theme.row(self.viewMembers, { name = "Member", lead = "avatar", identity = member }) }
			self.memberRows[key] = entry
		end
		entry.row.root.LayoutOrder = order
		entry.row.set(Theme.fit(member.name, 24) .. (if member.me then " · tu" else ""),
			if member.online then "Online" else "Offline")
	end
	for key, entry in pairs(self.memberRows) do
		if not seen[key] then
			entry.row.root:Destroy()
			self.memberRows[key] = nil
		end
	end
end

function Hub:updateAction()
	local store = self.store
	if store.defaultProvider ~= self.providerShown then
		self.providerShown = store.defaultProvider
		self.provider.set(store.defaultProvider)
	end
	for _, provider in ipairs({ "claude", "codex" }) do
		local entry = self.cli[provider]
		local found = store.connected and store:providerAvailable(provider)
		local role = if found then "primary" else "neutral"
		if role ~= entry.role then
			entry.role = role
			Theme.recolor(entry.dot, role)
		end
		entry.label.Text = Theme.names[provider] .. " · " .. (if found then "CLI găsit" else "CLI negăsit")
	end
	local reason = self:createReason()
	Theme.enabled(self.create, reason == nil)
	self.createHint.Visible = reason ~= nil
	self.createHint.Text = reason or ""
end

-- Motivul pentru care „Sesiune nouă” este dezactivat; nil înseamnă că poate fi apăsat.
function Hub:createReason()
	local store = self.store
	if not store.connected then return DAEMON_DOWN end
	if store.connecting then return "Se conectează la daemon…" end
	if not store.compatible then return OLD_DAEMON end
	if #store.order >= MAX_WINDOWS then
		return "Limita este de " .. MAX_WINDOWS .. " de ferestre. Închide o sesiune inactivă înainte de a crea alta."
	end
	if not store:providerAvailable(store.defaultProvider) then
		return "CLI-ul oficial " .. (Theme.names[store.defaultProvider] or store.defaultProvider) .. " nu a fost găsit de daemon."
	end
	return nil
end

-- Un card de sesiune: titlu + timp, apoi chip-urile, proprietarul și eticheta stării (DESIGN.md §7.1).
function Hub:newCard(parent, clickable)
	local card = Theme.card(parent, { name = "Session", clickable = clickable })
	local head = Theme.new("Frame", { Name = "Head", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 18),
		LayoutOrder = 1 }, card.body)
	local title = Theme.label(head, "", { size = "md", font = "ui", height = 18, truncate = true, name = "Title" })
	title.Size = UDim2.new(1, -68, 1, 0)
	local time = Theme.label(head, "", { size = "xs", token = "text2", height = 18, truncate = true, align = "right", name = "Time" })
	time.AnchorPoint, time.Position, time.Size = Vector2.new(1, 0), UDim2.new(1, 0, 0, 0), UDim2.fromOffset(64, 18)
	local meta = wrapRow(card.body, "Meta", 2)
	local entry = { card = card, title = title, time = time, firstSeen = os.time(), role = "neutral" }
	entry.providerChip = Theme.chip(meta, "", "neutral")
	entry.providerChip.LayoutOrder = 1
	entry.kindChip = Theme.chip(meta, "", "neutral")
	entry.kindChip.LayoutOrder = 2
	entry.approvalChip = Theme.chip(meta, "", "warning")
	entry.approvalChip.LayoutOrder, entry.approvalChip.Visible = 3, false
	entry.minimizedChip = Theme.chip(meta, "Minimizată", "neutral")
	entry.minimizedChip.LayoutOrder, entry.minimizedChip.Visible = 4, false
	local owner = Theme.new("Frame", { Name = "Owner", BackgroundTransparency = 1, Size = UDim2.new(0, 0, 0, 20),
		AutomaticSize = Enum.AutomaticSize.X, LayoutOrder = 5 }, meta)
	Theme.list(owner, Theme.SPACE.xs, true)
	entry.ownerHolder = Theme.new("Frame", { Name = "Avatar", BackgroundTransparency = 1, Size = UDim2.fromOffset(16, 16),
		LayoutOrder = 1 }, owner)
	entry.ownerLabel = inlineLabel(owner, { size = "xs", token = "text2", height = 20, truncate = true, name = "Who" }, 240)
	entry.ownerLabel.LayoutOrder = 2
	if clickable then
		entry.connection = self:bind(card.root, function()
			if entry.id then self.store:restore(entry.id) end
		end)
	end
	return entry
end

function Hub:fillCard(entry, model)
	entry.id = model.id
	if model.role ~= entry.role then
		entry.role = model.role
		entry.card.setRole(model.role)
	end
	if entry.title.Text ~= model.title then entry.title.Text = model.title end
	local elapsed = Theme.elapsed(model.since or entry.firstSeen)
	if entry.time.Text ~= elapsed then entry.time.Text = elapsed end
	local providerName = Theme.names[model.provider] or model.provider
	if entry.providerChip.Text ~= providerName then
		entry.providerChip.Text = providerName
		Theme.recolor(entry.providerChip, model.provider)
	end
	entry.kindChip.Text = Theme.kinds[model.kind] or Theme.kinds.studio
	entry.approvalChip.Visible = model.approvals > 0
	if model.approvals > 0 then entry.approvalChip.Text = Theme.countLabel(model.approvals, "aprobare", "aprobări") end
	entry.minimizedChip.Visible = model.minimized
	local owner = model.owner
	local avatarKey = if owner then tostring(owner.userId) .. "|" .. tostring(owner.name) else "-"
	if avatarKey ~= entry.avatarKey then
		entry.avatarKey = avatarKey
		if entry.avatar then entry.avatar:Destroy() end
		entry.avatar = Theme.avatar(entry.ownerHolder, owner, { size = 16 })
	end
	local who = model.who .. " · " .. model.state
	if entry.ownerLabel.Text ~= who then entry.ownerLabel.Text = who end
end

function Hub:syncCards(parent, cache, models, clickable)
	local seen = {}
	for order, model in ipairs(models) do
		seen[model.key] = true
		local entry = cache[model.key]
		if not entry then
			entry = self:newCard(parent, clickable)
			cache[model.key] = entry
		end
		entry.card.root.LayoutOrder = order
		self:fillCard(entry, model)
	end
	for key, entry in pairs(cache) do
		if not seen[key] then
			if entry.connection then entry.connection:Disconnect() end
			entry.card.root:Destroy()
			cache[key] = nil
		end
	end
end

function Hub:updateSessions()
	local store = self.store
	local mine, others = store:sessionsMine(), store:sessionsOthers()
	self.sessions.setCount(#mine + #others)
	local mineModels, otherModels = {}, {}
	for _, window in ipairs(mine) do table.insert(mineModels, windowModel(window)) end
	for _, window in ipairs(others) do table.insert(otherModels, windowModel(window)) end
	self:syncCards(self.sessions.body, self.mineCards, mineModels, true)
	self:syncCards(self.others.body, self.othersCards, otherModels, true)
	self.mineEmpty.root.Visible = #mineModels == 0
	self.othersEmpty.root.Visible = #otherModels == 0
	self.othersEmpty.label.Text = "Nimeni altcineva nu lucrează acum în "
		.. Theme.fit(store.workspaceName(store.workspace), 24) .. "."
end

function Hub:updateApprovals()
	local store = self.store
	local pending = {}
	for _, window in ipairs(store:sessionsMine()) do
		local approval = window.approvals[1]
		if approval and not approval.inactive then table.insert(pending, { window = window, approval = approval }) end
	end
	self.approvals.root.Visible = #pending > 0
	self.approvals.setCount(if #pending > 0 then #pending else nil)
	local seen = {}
	for order, item in ipairs(pending) do
		local window, approval = item.window, item.approval
		local key = window.id .. "|" .. approval.id
		seen[key] = true
		local entry = self.approvalCards[key]
		if not entry then
			entry = self:newApprovalCard(window.id, approval.id)
			self.approvalCards[key] = entry
		end
		entry.card.root.LayoutOrder = order
		entry.tool.Text = Theme.fit(tostring(approval.tool or "Instrument necunoscut"), 40)
		entry.arguments.Text = argumentSummary(approval.arguments)
		entry.session.Text = Theme.fit(window.name, 40) .. " · " .. (Theme.names[window.provider] or window.provider)
		local decided = window.decisionId ~= nil
		local canDecide = store.connected and not store.connecting and not window.remote and not window.cancelling and not decided
		Theme.enabled(entry.allow, canDecide and approval.inspectable == true and not approval.blocked)
		Theme.enabled(entry.refuse, canDecide)
		local hint = if approval.blocked then APPROVAL_BLOCKED elseif approval.inspectable ~= true then APPROVAL_HINT else ""
		entry.hint.Text = hint
		entry.hint.Visible = hint ~= ""
	end
	for key, entry in pairs(self.approvalCards) do
		if not seen[key] then
			for _, connection in ipairs(entry.connections) do connection:Disconnect() end
			entry.card.root:Destroy()
			self.approvalCards[key] = nil
		end
	end
end

function Hub:newApprovalCard(windowId, approvalId)
	local card = Theme.card(self.approvals.body, { name = "Approval", role = "warning" })
	local tool = Theme.label(card.body, "", { size = "md", font = "mono", height = 18, truncate = true, name = "Tool" })
	tool.LayoutOrder = 1
	local arguments = Theme.label(card.body, "", { size = "xs", font = "mono", token = "text2", name = "Arguments" })
	arguments.LayoutOrder = 2
	local session = Theme.label(card.body, "", { size = "xs", token = "text2", height = 16, truncate = true, name = "Session" })
	session.LayoutOrder = 3
	local choices = Theme.new("Frame", { Name = "Choices", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 32),
		LayoutOrder = 4 }, card.body)
	Theme.list(choices, Theme.SPACE.sm, true)
	local allow = Theme.button(choices, "Permite", { kind = "primary", width = UDim.new(0.6, -Theme.SPACE.xs), name = "Allow" })
	allow.LayoutOrder = 1
	local refuse = Theme.button(choices, "Refuză", { width = UDim.new(0.4, -Theme.SPACE.xs), name = "Refuse" })
	refuse.LayoutOrder = 2
	-- `text2`, nu `text3`: tokenul al treilea are sub 4,5:1 pe toate suprafețele (DESIGN.md §2.5), deci nu poate purta text.
	local hint = Theme.label(card.body, "", { size = "xs", token = "text2", name = "Hint" })
	hint.LayoutOrder = 5
	local entry = { card = card, tool = tool, arguments = arguments, session = session, allow = allow, refuse = refuse, hint = hint }
	entry.connections = {
		self:bind(allow, function() self.controller:decide(windowId, approvalId, true) end),
		self:bind(refuse, function() self.controller:decide(windowId, approvalId, false) end),
	}
	return entry
end

function Hub:updateClaims()
	local store = self.store
	self.claims.setCount(if #store.claims > 0 then #store.claims else nil)
	self.claimsEmpty.root.Visible = #store.claims == 0
	local seen = {}
	for order, claim in ipairs(store.claims) do
		local own = store:ownsClaim(claim)
		local key = claim.jobId .. "|" .. claim.path .. "|" .. tostring(own)
		seen[key] = true
		local entry = self.claimRows[key]
		if not entry then
			entry = { row = Theme.row(self.claims.body, { name = "Claim", mono = true, lines = 2,
				action = if own then { text = "Eliberează", kind = "ghost" } else nil }) }
			if entry.row.action then
				local jobId, path = claim.jobId, claim.path
				entry.connection = self:bind(entry.row.action, function()
					self.controller:releaseClaims(jobId, { path })
				end)
			end
			self.claimRows[key] = entry
		end
		entry.row.root.LayoutOrder = order
		local holder = if own then "tu" else Theme.fit(claim.developer, 16)
		local meta = holder .. " · " .. Theme.elapsed(claim.since)
		if claim.reason then meta ..= " · " .. Theme.fit(claim.reason, 40) end
		entry.row.set(shortPath(claim.path), meta)
		if entry.row.action then
			local owner = store:findJob(claim.jobId)
			Theme.enabled(entry.row.action, store.connected and not store.connecting and not (owner and owner.releasing))
		end
	end
	for key, entry in pairs(self.claimRows) do
		if not seen[key] then
			if entry.connection then entry.connection:Disconnect() end
			entry.row.root:Destroy()
			self.claimRows[key] = nil
		end
	end
end

function Hub:updateJournal()
	local store = self.store
	local identity = store.identity
	local myId = if identity then identity.userId else 0
	self.journalEmpty.root.Visible = #store.journal == 0
	local seen = {}
	for order, item in ipairs(store.journal) do
		local key = tostring(item.seq or "") .. "|" .. tostring(item.time or "") .. "|" .. item.tool .. "|" .. tostring(item.jobId or "")
		seen[key] = true
		local entry = self.journalRows[key]
		if not entry then
			local row = Theme.row(self.journal.body, { name = "Entry", lines = 2 })
			row.secondary.Font = Theme.font("mono")
			entry = { row = row }
			self.journalRows[key] = entry
		end
		entry.row.root.LayoutOrder = order
		local who = if myId > 0 and item.userId == myId then "tu" else Theme.fit(item.developer, 16)
		entry.row.set(Theme.clock(item.time) .. "  " .. who .. " · " .. Theme.fit(item.tool, 24), journalPaths(item.paths))
	end
	for key, entry in pairs(self.journalRows) do
		if not seen[key] then
			entry.row.root:Destroy()
			self.journalRows[key] = nil
		end
	end
end

function Hub:updateFooter()
	local store = self.store
	local text = store.status
	if store.connected and not store.connecting and not store.statusError then
		local update = store.update
		if update.state == "downloading" then text ..= " · actualizare în curs"
		elseif update.state == "available" and update.available then
			text ..= " · actualizare " .. update.available .. " disponibilă (se instalează când nu rulează sesiuni)"
		elseif update.state == "error" and update.message ~= "" then text ..= " · actualizare: " .. update.message
		else text ..= " · la zi" end
	end
	self.daemonLine.Text = Theme.fit(text, 240)
	local token = if store.statusError then "dangerText" else "text2"
	if token ~= self.daemonToken then
		self.daemonToken = token
		Theme.paint(self.daemonLine, { TextColor3 = token })
	end
	local needsToken = self.controller:needsToken()
	self.codeField.Visible = needsToken
	if needsToken then
		local hint = (CODE_REFUSED[self.controller:tokenSource()] or "") .. CODE_HINT
		if hint ~= self.codeHintText then self.codeHintText, self.codeHint.Text = hint, hint end
	end
	local studio = store:studio(store.defaultStudioId)
	if #store.studios == 0 then self.studioButton.Text = "Niciun Studio conectat"
	elseif studio then self.studioButton.Text = "Studio țintă: " .. Theme.fit(studio.name, 18)
	elseif #store.studios == 1 then self.studioButton.Text = "Automat: " .. Theme.fit(store.studios[1].name, 24)
	else self.studioButton.Text = "Automat · " .. Theme.countLabel(#store.studios, "instanță", "instanțe") end
	Theme.enabled(self.studioButton, store.connected and #store.studios > 0)
	-- Id-ul complet al dispozitivului, de copiat; nu se rescrie cât timp utilizatorul îl are selectat (§7.12).
	if not self.deviceBox:IsFocused() then self.deviceBox.Text = store.hub.deviceId or "—" end
	if not store.connected then self.confirming = false end
	self.disconnect.Visible = store.connected and not self.confirming
	-- Doar oprirea manuală suspendă reconectarea automată (DESIGN.md §10.4): la o cădere temporară subsolul spune deja
	-- „nu răspunde (reîncerc în N s)”, deci un buton de conectare ar fi o acțiune inutilă care contrazice mesajul.
	self.reconnect.Visible = not store.connected and self.controller:manualStopped()
	if self.confirming ~= self.confirmShown then
		self.confirmShown = self.confirming
		if self.confirming then self.confirm.show("danger", DISCONNECT_CONFIRM) else self.confirm.hide() end
	end
end

function Hub:destroy()
	Theme.cleanup(self)
	for _, cache in ipairs({ self.mineCards, self.othersCards, self.viewCards, self.approvalCards, self.claimRows,
		self.journalRows, self.workspaceRows, self.memberRows }) do
		for _, entry in pairs(cache) do
			if entry.connection then entry.connection:Disconnect() end
			for _, connection in ipairs(entry.connections or {}) do connection:Disconnect() end
		end
		table.clear(cache)
	end
	self.widget:Destroy()
end

return Hub
