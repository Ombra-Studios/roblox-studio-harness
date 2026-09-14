-- Studio Harness SessionView 1.0.0 — fereastra unei sesiuni: antet, flux de mesaje, composer și cardul de aprobare.
-- Structura și textele urmează design/DESIGN.md §10.6–§10.8 și §12; toate culorile vin din token-urile Theme.
local Theme = require(script.Parent.Theme)
local ApprovalView = require(script.Parent.ApprovalView)
local SessionView = {}
SessionView.__index = SessionView

local PAD = Theme.SPACE.md -- 12: marginea ferestrei, a antetului și a composer-ului
local GAP = Theme.SPACE.sm -- 8: distanța dintre controale
local TITLE_HEIGHT = 36
local ROW_HEIGHT = 20
local BUTTON_ROW = 32
local SPLIT_WIDTH = 820 -- peste această lățime aprobarea stă lângă flux (50/50), sub ea dedesubt (30/70)
local MIN_APPROVAL = 190 -- cardul de aprobare cu o cutie de argumente utilizabilă (142 px fixe + 48 px de text)
local MIN_FLOW = 60 -- fluxul nu dispare niciodată complet de sub aprobare
local FOLLOW_SLACK = 60 -- toleranța în px pentru „utilizatorul era la capătul fluxului” (§13)

-- Felurile de bulă (§6.5) pentru felurile de mesaj ale store-ului: „user” este ce a scris developerul,
-- iar „text” rămâne doar nota locală de deschidere a ferestrei, deci se desenează ca notiță.
local BUBBLE_KIND = { user = "text", text = "notice" }

-- Mesajul store-ului tradus în forma cerută de `Theme.bubble`; autorul „Tu” dispare din bula proprie (wireframe §10.6).
local function bubbleFields(message)
	local kind = BUBBLE_KIND[message.kind] or message.kind or "notice"
	local author = if type(message.author) == "string" then message.author else ""
	if kind == "text" and author == "Tu" then author = "" end
	return { author = author, text = message.text, kind = kind }
end

-- Numele afișat al proprietarului ferestrei: „tu” pentru sesiunile proprii, contul Roblox pentru ale altora.
local function ownerName(window, owner)
	if window.remote ~= true and window.mine ~= false then return "tu" end
	if owner and type(owner.name) == "string" and owner.name ~= "" then return Theme.fit(owner.name, 24) end
	return "?"
end

function SessionView.new(pluginObject, store, controller, id)
	local window = store.windows[id]
	local terminal, remote = window.kind == "terminal", window.remote == true
	local self = setmetatable({ connections = {}, destroyed = false, store = store, controller = controller,
		id = id, terminal = terminal, remote = remote, messageViews = {}, refreshing = false, followChat = true,
		retryConfirm = false, restoreVersion = window.restoreVersion, stateRole = nil, avatarKey = nil, confirmText = nil }, SessionView)
	local info = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Float, window.visible, true, 480, 740, 380, 640)
	self.widget = pluginObject:CreateDockWidgetPluginGuiAsync("StudioHarnessMatrixSession_" .. id, info)
	self.widget.Name, self.widget.Title = "StudioHarnessSession_" .. id, window.name
	-- Ca la hub: panoul supraviețuiește instanței anterioare (Play, metamorfoză), deci îi golim conținutul rămas.
	for _, child in ipairs(self.widget:GetChildren()) do child:Destroy() end
	self.root = Theme.frame(self.widget, "SessionContent")
	self:buildHeader(window)
	self:buildBody()
	self:buildComposer()
	self:buildConfirm()
	self:bind()
	self:update()
	return self
end

-- Antet fix: titlu editabil inline, butoane mici, chip-uri (provider, tip, stare), proprietar și claims (§10.6).
function SessionView:buildHeader(window)
	local header = Theme.frame(self.root, "Header", "bg")
	header.Size = UDim2.new(1, 0, 0, 0)
	header.AutomaticSize = Enum.AutomaticSize.Y
	Theme.padding(header, PAD, PAD)
	Theme.list(header, Theme.SPACE.xs + 2)
	self.header = header
	self.headerLine = Theme.frame(self.root, "HeaderLine", "border")
	self.headerLine.Size = UDim2.new(1, 0, 0, 1)

	local titleRow = Theme.new("Frame", { Name = "TitleRow", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, TITLE_HEIGHT), LayoutOrder = 1 }, header)
	local reserved = 0
	self.closeButton = Theme.button(titleRow, "×", { kind = "ghost", size = "sm", name = "CloseSession", width = UDim.new(0, 28) })
	self.closeButton.AnchorPoint = Vector2.new(1, 0.5)
	self.closeButton.Position = UDim2.new(1, -reserved, 0.5, 0)
	reserved += 28 + Theme.SPACE.xs
	self.hideButton = Theme.button(titleRow, "_", { kind = "ghost", size = "sm", name = "MinimizeSession", width = UDim.new(0, 28) })
	self.hideButton.AnchorPoint = Vector2.new(1, 0.5)
	self.hideButton.Position = UDim2.new(1, -reserved, 0.5, 0)
	reserved += 28 + Theme.SPACE.xs
	if not self.terminal then
		self.newButton = Theme.button(titleRow, "Nou", { kind = "ghost", size = "sm", name = "NewConversation", width = UDim.new(0, 44) })
		self.newButton.AnchorPoint = Vector2.new(1, 0.5)
		self.newButton.Position = UDim2.new(1, -reserved, 0.5, 0)
		reserved += 44 + Theme.SPACE.xs
	end
	-- Titlul este un câmp fără chenar până la hover sau focus; padding-ul lui intern se compensează cu −10 px.
	self.title = Theme.input(titleRow, { placeholder = "Numele sesiunii", height = TITLE_HEIGHT, name = "SessionName" })
	self.title.TextSize = Theme.TEXT.xl
	self.title.Font = Theme.font("ui")
	self.title.TextEditable = not (self.terminal or self.remote)
	self.title.Position = UDim2.fromOffset(-10, 0)
	self.title.Size = UDim2.new(1, -(reserved + GAP - 10), 1, 0)
	self.title.BackgroundTransparency = 1
	self.titleStroke = self.title:FindFirstChildWhichIsA("UIStroke")
	if self.titleStroke then self.titleStroke.Transparency = 1 end

	local chips = Theme.new("Frame", { Name = "Chips", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, ROW_HEIGHT), LayoutOrder = 2 }, header)
	Theme.list(chips, Theme.SPACE.xs, true)
	local provider = Theme.chip(chips, Theme.names[window.provider] or window.provider, window.provider)
	provider.LayoutOrder = 1
	if self.terminal then
		local kind = Theme.chip(chips, Theme.kinds[window.sessionKind] or Theme.kinds.terminal, "neutral")
		kind.LayoutOrder = 2
	end
	self.stateChip = Theme.chip(chips, "", "neutral")
	self.stateChip.LayoutOrder = 3
	if self.remote then
		local readOnly = Theme.chip(chips, "doar de citit", "neutral")
		readOnly.LayoutOrder = 4
	end

	self.ownerRow = Theme.new("Frame", { Name = "Owner", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, ROW_HEIGHT), LayoutOrder = 3 }, header)
	self.meta = Theme.label(self.ownerRow, "", { size = "xs", token = "text2", height = ROW_HEIGHT, truncate = true, name = "Meta" })
	self.meta.Position = UDim2.fromOffset(16 + GAP, 0)
	self.target = Theme.button(self.ownerRow, "Țintă nealeasă", { kind = "ghost", size = "sm", name = "SessionStudio", width = UDim.new(0, 120) })
	self.target.AnchorPoint = Vector2.new(1, 0.5)
	self.target.Position = UDim2.new(1, 0, 0.5, 0)
	self.target.Visible = false

	self.claims = Theme.label(header, "", { size = "xs", token = "text2", height = 18, truncate = true, name = "Claims" })
	self.claims.LayoutOrder = 4
end

-- Fluxul de mesaje și cardul de aprobare împart zona dintre antet și composer (§10.6, §10.8).
function SessionView:buildBody()
	self.body = Theme.new("Frame", { Name = "Flow", BackgroundTransparency = 1 }, self.root)
	self.chat = Theme.scroll(self.body, "SessionMessages")
	self.chatLayout = Theme.list(self.chat, Theme.SPACE.md)
	self.empty = Theme.empty(self.chat, if self.terminal then "Sesiunea nu a trimis încă niciun eveniment."
		else "Scrie primul pas mai jos. Agentul vede selecția ta din Studio.")
	self.empty.root.LayoutOrder = 0
	self.approval = ApprovalView.new(self.body, self.store, self.id, function(approvalId, allow)
		if not allow or self.approval:inspectable(approvalId) then self.controller:decide(self.id, approvalId, allow) end
	end)
end

-- Composer fix jos: prompt + „Trimite”/„Oprește” (Mod S) sau „Oprește”/„Eliberează claims” (Mod T); sesiunea altuia nu are niciunul.
function SessionView:buildComposer()
	local composer = Theme.frame(self.root, "Composer", "surface")
	composer.AnchorPoint = Vector2.new(0, 1)
	composer.Position = UDim2.new(0, 0, 1, 0)
	composer.Size = UDim2.new(1, 0, 0, 0)
	composer.AutomaticSize = Enum.AutomaticSize.Y
	Theme.padding(composer, PAD, PAD)
	Theme.list(composer, GAP)
	self.composer = composer
	self.footerLine = Theme.frame(self.root, "FooterLine", "border")
	self.footerLine.Size = UDim2.new(1, 0, 0, 1)
	if self.remote then
		composer.Visible = false
		return
	end
	local buttons = Theme.new("Frame", { Name = "Actions", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, BUTTON_ROW), LayoutOrder = 2 }, composer)
	if self.terminal then
		self.stop = Theme.button(buttons, "Oprește", { name = "StopSession", width = UDim.new(0.5, -Theme.SPACE.xs) })
		self.release = Theme.button(buttons, "Eliberează claims", { name = "ReleaseClaims", width = UDim.new(0.5, -Theme.SPACE.xs) })
		self.release.Position = UDim2.new(0.5, Theme.SPACE.xs, 0, 0)
		return
	end
	self.draft = Theme.textarea(composer, { placeholder = "Scrie următorul pas…", height = 72, name = "Prompt" })
	self.draft.LayoutOrder = 1
	self.send = Theme.button(buttons, "Trimite", { kind = "primary", name = "SendMessage", width = UDim.new(0.6, -Theme.SPACE.xs) })
	self.stop = Theme.button(buttons, "Oprește", { name = "StopSession", width = UDim.new(0.4, -Theme.SPACE.xs) })
	self.stop.Position = UDim2.new(0.6, Theme.SPACE.xs, 0, 0)
	self.hint = Theme.label(composer, "", { size = "xs", token = "text2", name = "Hint" })
	self.hint.LayoutOrder = 3
end

-- Confirmările (închidere, reluarea unei cereri neconfirmate) înlocuiesc composer-ul, inline, nu într-un dialog (§13).
function SessionView:buildConfirm()
	local confirm = Theme.frame(self.root, "Confirm", "surface")
	confirm.AnchorPoint = Vector2.new(0, 1)
	confirm.Position = UDim2.new(0, 0, 1, 0)
	confirm.Size = UDim2.new(1, 0, 0, 0)
	confirm.AutomaticSize = Enum.AutomaticSize.Y
	confirm.Visible = false
	Theme.padding(confirm, PAD, PAD)
	Theme.list(confirm, GAP)
	self.confirm = confirm
	self.confirmBanner = Theme.banner(confirm, { role = "warning", name = "ConfirmBanner" })
	self.confirmBanner.root.LayoutOrder = 1
	local buttons = Theme.new("Frame", { Name = "Actions", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, BUTTON_ROW), LayoutOrder = 2 }, confirm)
	self.confirmClose = Theme.button(buttons, "Închide definitiv", { kind = "danger", name = "ConfirmSessionAction",
		width = UDim.new(0.6, -Theme.SPACE.xs) })
	self.confirmRetry = Theme.button(buttons, "Reia aceeași cerere", { kind = "primary", name = "RetrySessionRequest",
		width = UDim.new(0.6, -Theme.SPACE.xs) })
	self.confirmRetry.Visible = false
	self.confirmNo = Theme.button(buttons, "Înapoi", { name = "DismissSessionAction", width = UDim.new(0.4, -Theme.SPACE.xs) })
	self.confirmNo.Position = UDim2.new(0.6, Theme.SPACE.xs, 0, 0)
end

-- Legăturile: butoanele antetului, composer-ul, confirmările, redenumirea, derularea fluxului și relayout-ul.
function SessionView:bind()
	local store, controller, id = self.store, self.controller, self.id
	if self.newButton then
		Theme.click(self, self.newButton, function()
			local current = store.windows[id]
			if current then controller:createWindow(current.provider, store:studio(current.studioId)) end
		end)
	end
	Theme.click(self, self.hideButton, function() store:setVisible(id, false) end)
	Theme.click(self, self.closeButton, function()
		local current = store.windows[id]
		if not current then return end
		if self.terminal then
			controller:close(id)
		else
			current.closeConfirm, self.retryConfirm = true, false
			store:emit(id)
		end
	end)
	Theme.click(self, self.target, function()
		if not self.terminal and not self.remote then store:bindStudio(id, store:studio(store.defaultStudioId)) end
	end)
	if self.send then
		Theme.click(self, self.send, function()
			local current = store.windows[id]
			if current and current.request and current.request.state == "uncertain" then
				self.retryConfirm, current.closeConfirm = true, false
				self:update()
			else
				controller:send(id)
			end
		end)
	end
	if self.stop then Theme.click(self, self.stop, function() controller:cancel(id, false) end) end
	if self.release then
		Theme.click(self, self.release, function() controller:releaseClaims(store:jobIdOf(store.windows[id])) end)
	end
	Theme.click(self, self.confirmClose, function()
		local current = store.windows[id]
		if not current then return end
		current.closeConfirm = false
		controller:close(id, true)
		if store.windows[id] then self:update() end
	end)
	Theme.click(self, self.confirmRetry, function()
		if not self.retryConfirm then return end
		self.retryConfirm = false
		controller:retry(id)
		self:update()
	end)
	Theme.click(self, self.confirmNo, function()
		self.retryConfirm = false
		local current = store.windows[id]
		if current then
			current.closeConfirm, current.closeAfterCancel = false, false
			store:emit(id)
		end
	end)
	-- Titlul își arată chenarul doar când poate fi editat și doar sub cursor sau în focus.
	local function titleChrome(active)
		local on = active == true and self.title.TextEditable
		Theme.tween(self.title, { BackgroundTransparency = if on then 0 else 1 })
		if self.titleStroke then Theme.tween(self.titleStroke, { Transparency = if on then 0 else 1 }) end
	end
	Theme.connect(self, self.title.MouseEnter, function() titleChrome(true) end)
	Theme.connect(self, self.title.MouseLeave, function() titleChrome(self.title:IsFocused()) end)
	Theme.connect(self, self.title.Focused, function() titleChrome(true) end)
	Theme.connect(self, self.title.FocusLost, function()
		titleChrome(false)
		if not self.terminal and not self.remote then store:rename(id, self.title.Text) end
	end)
	if self.draft then
		Theme.connect(self, self.draft:GetPropertyChangedSignal("Text"), function()
			if not self.refreshing then
				store:setDraft(id, self.draft.Text)
				self:updateButtons()
			end
		end)
	end
	Theme.connect(self, self.widget:GetPropertyChangedSignal("Enabled"), function()
		if not self.refreshing then store:setVisible(id, self.widget.Enabled) end
	end)
	Theme.connect(self, self.root:GetPropertyChangedSignal("AbsoluteSize"), function() self:layout() end)
	Theme.connect(self, self.header:GetPropertyChangedSignal("AbsoluteSize"), function() self:layout() end)
	Theme.connect(self, self.composer:GetPropertyChangedSignal("AbsoluteSize"), function() self:layout() end)
	Theme.connect(self, self.confirm:GetPropertyChangedSignal("AbsoluteSize"), function() self:layout() end)
	Theme.connect(self, self.chat:GetPropertyChangedSignal("CanvasPosition"), function()
		local content = self.chatLayout.AbsoluteContentSize.Y
		self.followChat = self.chat.CanvasPosition.Y >= math.max(0, content - self.chat.AbsoluteWindowSize.Y) - FOLLOW_SLACK
	end)
	Theme.connect(self, self.chatLayout:GetPropertyChangedSignal("AbsoluteContentSize"), function() self:follow() end)
end

-- Fluxul urmărește capătul doar dacă utilizatorul era deja acolo (§13).
function SessionView:follow()
	if self.destroyed or not self.followChat then return end
	local content = self.chatLayout.AbsoluteContentSize.Y
	self.chat.CanvasPosition = Vector2.new(0, math.max(0, content - self.chat.AbsoluteWindowSize.Y + GAP))
end

-- Împarte fereastra: antet (auto), flux, composer/confirmare (auto), cu câte o linie de 1 px între ele.
function SessionView:layout()
	if self.destroyed then return end
	local headerHeight = math.max(1, self.header.AbsoluteSize.Y)
	self.headerLine.Position = UDim2.new(0, 0, 0, headerHeight)
	local footer = if self.confirm.Visible then self.confirm elseif self.composer.Visible then self.composer else nil
	local footerHeight = if footer then footer.AbsoluteSize.Y else 0
	self.footerLine.Visible = footer ~= nil
	self.footerLine.Position = UDim2.new(0, 0, 1, -(footerHeight + 1))
	local area = self.root.AbsoluteSize.Y - headerHeight - 1 - (if footer then footerHeight + 1 else 0)
	local bodyHeight = math.max(140, area - PAD * 2)
	self.body.Position = UDim2.fromOffset(PAD, headerHeight + 1 + PAD)
	self.body.Size = UDim2.new(1, -PAD * 2, 0, bodyHeight)
	local window = self.store.windows[self.id]
	local approvals = if window then #window.approvals else 0
	if self.remote or approvals == 0 then
		self.chat.Position, self.chat.Size = UDim2.fromOffset(0, 0), UDim2.fromScale(1, 1)
	elseif self.root.AbsoluteSize.X >= SPLIT_WIDTH then
		self.chat.Position, self.chat.Size = UDim2.fromOffset(0, 0), UDim2.new(0.5, -GAP, 1, 0)
		self.approval.root.Position = UDim2.new(0.5, GAP, 0, 0)
		self.approval.root.Size = UDim2.new(0.5, -GAP, 1, 0)
	else
		-- Sub 820 px: 30 % flux / 70 % aprobare, dar cardul primește cel puțin cât îi trebuie ca să-și arate butoanele.
		local available = math.max(0, bodyHeight - PAD)
		local approvalHeight = math.min(math.max(math.floor(available * 0.7), MIN_APPROVAL), math.max(0, available - MIN_FLOW))
		local chatHeight = math.max(0, available - approvalHeight)
		self.chat.Position, self.chat.Size = UDim2.fromOffset(0, 0), UDim2.new(1, 0, 0, chatHeight)
		self.approval.root.Position = UDim2.fromOffset(0, chatHeight + PAD)
		self.approval.root.Size = UDim2.new(1, 0, 0, approvalHeight)
	end
end

-- Avatarul proprietarului se reconstruiește doar când se schimbă contul (poll-ul nu trebuie să clipească).
function SessionView:setAvatar(owner)
	local key = tostring(if owner then owner.userId or 0 else 0) .. "|" .. tostring(if owner then owner.name or "" else "")
	if self.avatarKey == key then return end
	self.avatarKey = key
	if self.avatar then self.avatar:Destroy() end
	self.avatar = Theme.avatar(self.ownerRow, owner, { size = 16 })
	self.avatar.Position = UDim2.new(0, 0, 0.5, -8)
end

function SessionView:updateButtons()
	local window = self.store.windows[self.id]
	if self.destroyed or not window then return end
	local store = self.store
	if self.stop then
		self.stop.Text = if window.cancelling then Theme.TRANSIENT.cancelling else "Oprește"
		Theme.enabled(self.stop, store.connected and window.job ~= nil and not window.cancelling)
	end
	if self.release then
		self.release.Text = if window.releasing then Theme.TRANSIENT.releasing else "Eliberează claims"
		Theme.enabled(self.release, store.connected and not store.connecting and window.job ~= nil
			and #window.claims > 0 and not window.releasing)
	end
	if self.newButton then Theme.enabled(self.newButton, #store.order < store.MAX_WINDOWS) end
	if self.terminal or self.remote then return end
	local allowed, reason = store:canSend(window)
	local request = window.request
	local uncertain = request ~= nil and request.state == "uncertain"
	if request ~= nil and request.state == "sending" then
		self.send.Text = "Se trimite…"
		Theme.enabled(self.send, false)
	elseif uncertain then
		self.send.Text = "Reia aceeași cerere"
		Theme.enabled(self.send, self.controller:canRetry(window))
	else
		self.send.Text = "Trimite"
		Theme.enabled(self.send, allowed and string.find(window.draft, "%S") ~= nil)
	end
	self.hint.Text = if window.cancelling then "Anulare în curs, doar pentru acest job."
		elseif window.state == "queued" then "În coadă. Daemon-ul lucrează pe rând."
		elseif uncertain then "Fără retry automat; reluarea cere confirmare."
		elseif allowed then "Doar această sesiune. Modificările cer claim și aprobare."
		else reason or "Doar această sesiune. Modificările cer claim și aprobare."
end

-- Antetul: titlu, chip-uri, proprietar și claims, recalculate la fiecare poll fără să recreeze instanțe.
function SessionView:updateHeader(window)
	local store = self.store
	if not self.title:IsFocused() and self.title.Text ~= window.name then self.title.Text = window.name end
	local _, label, role = Theme.sessionState(window)
	if self.stateChip.Text ~= label then self.stateChip.Text = label end
	if self.stateRole ~= role then
		self.stateRole = role
		Theme.recolor(self.stateChip, role)
	end
	local owner = window.owner or (if self.remote then nil else store:ownIdentity())
	self:setAvatar(owner)
	local target
	if self.remote then
		target = if owner and type(owner.machine) == "string" and owner.machine ~= "" then Theme.fit(owner.machine, 24) else "altă mașină"
	elseif window.studioId then
		target = Theme.fit(window.studioName or "Studio", 32) .. " / Edit"
	elseif self.terminal then
		target = "țintă aleasă de daemon"
	end
	local meta = ownerName(window, owner) .. (if target then " · " .. target else "")
	if self.meta.Text ~= meta then self.meta.Text = meta end
	-- „Țintă nealeasă” apare ca buton ghost doar în sesiunile proprii din Studio, până când ținta este legată (§10.6).
	local showTarget = not self.terminal and not self.remote and window.studioId == nil
	self.target.Visible = showTarget
	Theme.enabled(self.target, showTarget and not window.locked and window.request == nil
		and store:studio(store.defaultStudioId) ~= nil)
	local reserved = if showTarget then 120 + GAP else 0
	self.meta.Size = UDim2.new(1, -(16 + GAP + reserved), 1, 0)
	local claims = if #window.claims > 0 then "Claims: " .. table.concat(window.claims, ", ")
		else "Fără claims. Modificările cer hub_claim."
	if self.claims.Text ~= claims then self.claims.Text = claims end
	self.claims.Font = Theme.font(if #window.claims > 0 then "mono" else "body")
end

-- Fluxul: o bulă per mesaj, păstrată între poll-uri (identitatea instanței ține scroll-ul pe loc).
function SessionView:updateFlow(window)
	local present = {}
	for order, message in ipairs(window.messages) do
		present[message] = true
		local view = self.messageViews[message]
		if not view then
			view = Theme.bubble(self.chat, bubbleFields(message), window.provider)
			self.messageViews[message] = view
		end
		view.root.LayoutOrder = order
		view.set(bubbleFields(message))
	end
	for message, view in pairs(self.messageViews) do
		if not present[message] then
			view.root:Destroy()
			self.messageViews[message] = nil
		end
	end
	self.empty.root.Visible = #window.messages == 0
end

function SessionView:update()
	if self.destroyed then return end
	local window = self.store.windows[self.id]
	if not window then return end
	local store = self.store
	self.refreshing = true
	if self.restoreVersion ~= window.restoreVersion then
		self.restoreVersion = window.restoreVersion
		self.widget.Enabled = false
	end
	self.widget.Enabled, self.widget.Title = window.visible, window.name
	if self.draft and self.draft.Text ~= window.draft and not self.draft:IsFocused() then self.draft.Text = window.draft end
	self:updateHeader(window)
	self:updateFlow(window)
	self.approval:update(window, store.connected and store.compatible and not self.remote)
	if self.remote then self.approval.root.Visible = false end
	local confirming = not self.terminal and (window.closeConfirm or self.retryConfirm)
	self.confirm.Visible = confirming
	self.composer.Visible = not confirming and not self.remote
	if confirming then
		local text, closing
		if self.retryConfirm then
			text, closing = "Reiei exact mesajul original, cu același ID. Daemon-ul deduplică cererea; dacă prima încercare nu a ajuns, o va trimite acum. Draftul nou nu este trimis.", false
			self.confirmRetry.Text = "Reia aceeași cerere"
			Theme.enabled(self.confirmRetry, self.controller:canRetry(window))
		elseif window.job or window.request then
			text, closing = "Închizi definitiv această fereastră? Jobul ei trebuie anulat înainte de ștergere. Celelalte sesiuni rămân active.", true
			self.confirmClose.Text = "Anulează și închide"
			Theme.enabled(self.confirmClose, not window.cancelling)
		else
			text, closing = "Închizi definitiv această fereastră și istoricul ei local? Folosește Minimizează dacă vrei să revii.", true
			self.confirmClose.Text = "Închide definitiv"
			Theme.enabled(self.confirmClose, true)
		end
		self.confirmClose.Visible, self.confirmRetry.Visible = closing, not closing
		if self.confirmText ~= text then
			self.confirmText = text
			self.confirmBanner.show("warning", text)
		end
	elseif self.confirmText ~= nil then
		self.confirmText = nil
		self.confirmBanner.hide()
	end
	self:updateButtons()
	self:layout()
	self.refreshing = false
end

-- `permanent` este adevărat doar când fereastra dispare din store (sesiune închisă de utilizator): atunci id-ul nu se mai
-- repetă și panoul poate fi distrus. La oprirea aplicației (Play, metamorfoză) panoul rămâne, altfel `CreateDockWidgetPluginGuiAsync`
-- ar întoarce mai târziu un panou distrus pentru aceeași sesiune, iar fereastra nu s-ar mai deschide.
function SessionView:destroy(permanent)
	Theme.cleanup(self)
	self.approval:destroy()
	if permanent then
		self.widget:Destroy()
	else
		if self.root then pcall(function() self.root:Destroy() end) end
		self.root = nil
	end
end

return SessionView
