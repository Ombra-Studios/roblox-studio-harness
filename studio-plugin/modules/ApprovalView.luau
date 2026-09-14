-- Studio Harness ApprovalView 1.0.0 — cardul de aprobare al unei sesiuni (design/DESIGN.md §10.8).
-- Regula 0.8 rămâne: „Permite” este activ numai când argumentele au încăput integral în cutia derulabilă.
-- Argumentele se serializează aici, în Luau curat: `HttpService` este permis doar în BridgeController și în loader.
local Theme = require(script.Parent.Theme)
local TextService = game:GetService("TextService")
local ApprovalView = {}
ApprovalView.__index = ApprovalView

local MAX_BYTES, MAX_HEIGHT = 120000, 16000
local CODE_SIZE = Theme.TEXT.sm -- 13: argumentele sunt identificatori, deci mono mic
local GUTTER = Theme.SPACE.xs + 2 -- marginea interioară a cutiei de argumente
local MAX_DEPTH = 32
-- Înălțimea părților fixe ale cardului: padding 2 × 12, titlu 24, detaliu 20, hint 18, butoane 32 și 4 spații de 6.
local FIXED_HEIGHT = 142
local ESCAPES = { ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }

-- Un șir JSON: ghilimele, backslash și caracterele de control devin secvențe de evadare; restul octeților rămân neatinși.
local function quote(text)
	local escaped = string.gsub(text, '[%c"\\]', function(character)
		local mapped = ESCAPES[character]
		if mapped then return mapped end
		local byte = string.byte(character)
		if byte and (byte < 32 or byte == 127) then return string.format("\\u%04x", byte) end
		return character
	end)
	return '"' .. escaped .. '"'
end

-- Adaugă o bucată la ieșire; peste limita locală de afișare se oprește tot (cardul va oferi numai „Refuză”).
local function push(state, text)
	if state.overflow then return false end
	state.size += #text
	if state.size > MAX_BYTES then
		state.overflow = true
		return false
	end
	table.insert(state.parts, text)
	return true
end

-- Cheile unui obiect, sortate, ca două afișări ale aceluiași apel să arate identic.
local function sortedKeys(value)
	local keys = {}
	for key in pairs(value) do table.insert(keys, key) end
	table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
	return keys
end

local function encode(state, value, depth)
	if state.overflow then return end
	local kind = type(value)
	if kind == "string" then
		push(state, quote(value))
	elseif kind == "boolean" then
		push(state, tostring(value))
	elseif kind == "number" then
		if value ~= value or value == math.huge or value == -math.huge then push(state, "null")
		elseif value % 1 == 0 and math.abs(value) < 1e15 then push(state, string.format("%d", value))
		else push(state, tostring(value)) end
	elseif kind ~= "table" then
		push(state, "null")
	elseif depth >= MAX_DEPTH then
		push(state, '"…"')
	elseif next(value) == nil then
		push(state, "{}")
	else
		local indent, inner = string.rep("  ", depth), string.rep("  ", depth + 1)
		local count = #value
		if count > 0 then
			push(state, "[\n")
			for index = 1, count do
				push(state, inner)
				encode(state, value[index], depth + 1)
				push(state, if index < count then ",\n" else "\n")
			end
			push(state, indent .. "]")
		else
			local keys = sortedKeys(value)
			push(state, "{\n")
			for index, key in ipairs(keys) do
				push(state, inner .. quote(tostring(key)) .. ": ")
				encode(state, value[key], depth + 1)
				push(state, if index < #keys then ",\n" else "\n")
			end
			push(state, indent .. "}")
		end
	end
end

-- Textul integral al unui apel propus: codul Luau (dacă există), instrumentul și argumentele complete, indentate.
local function formatArguments(approval)
	if type(approval.arguments) ~= "table" then return "Argumentele complete nu pot fi afișate. Refuză apelul.", false end
	local state = { parts = {}, size = 0, overflow = false }
	encode(state, approval.arguments, 0)
	if state.overflow then
		return "Argumentele depășesc limita de afișare. Aprobarea este dezactivată; refuză și cere un apel mai mic.", false
	end
	local text = "Instrument: " .. tostring(approval.tool) .. "\n\nArgumente complete:\n" .. table.concat(state.parts)
	if type(approval.arguments.code) == "string" then text = "Cod Luau:\n" .. approval.arguments.code .. "\n\n" .. text end
	if #text > MAX_BYTES then return "Afișarea integrală depășește limita locală. Numai Refuză este disponibil.", false end
	return text, true
end

function ApprovalView.new(parent, store, windowId, decide)
	local self = setmetatable({ connections = {}, destroyed = false, store = store, windowId = windowId,
		current = nil, canDecide = false, measured = false, windowVisible = false,
		text = "", fullText = false, width = 0, height = 0, reportedId = nil, reported = nil }, ApprovalView)
	local card = Theme.card(parent, { name = "Approval", role = "warning" })
	self.card, self.root = card, card.root
	self.root.AutomaticSize = Enum.AutomaticSize.None
	self.root.Visible = false

	local titleRow = Theme.new("Frame", { Name = "TitleRow", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 24), LayoutOrder = 1 }, card.body)
	self.title = Theme.label(titleRow, "Modificarea propusă", { size = "lg", font = "ui", token = "warningText",
		height = 24, truncate = true, name = "Title" })
	self.title.Size = UDim2.new(1, -124, 1, 0)
	self.meta = Theme.label(titleRow, "", { size = "xs", token = "text2", height = 24, truncate = true,
		align = "right", name = "Meta" })
	self.meta.AnchorPoint = Vector2.new(1, 0)
	self.meta.Position = UDim2.new(1, 0, 0, 0)
	self.meta.Size = UDim2.new(0, 120, 1, 0)

	local detailRow = Theme.new("Frame", { Name = "DetailRow", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 20), LayoutOrder = 2 }, card.body)
	Theme.list(detailRow, Theme.SPACE.xs, true)
	self.tool = Theme.label(detailRow, "", { size = "md", font = "mono", height = 20, truncate = true, name = "Tool" })
	self.tool.AutomaticSize = Enum.AutomaticSize.X
	self.tool.Size = UDim2.fromOffset(0, 20)
	self.tool.LayoutOrder = 1
	local scope = Theme.label(detailRow, "· doar acest apel", { size = "xs", token = "text2", height = 20, name = "Scope" })
	scope.AutomaticSize = Enum.AutomaticSize.X
	scope.Size = UDim2.fromOffset(0, 20)
	scope.LayoutOrder = 2

	self.scroll = Theme.scroll(card.body, "CompleteArguments")
	self.scroll.LayoutOrder = 3
	self.scroll.Size = UDim2.new(1, 0, 0, 120)
	self.scroll.AutomaticCanvasSize = Enum.AutomaticSize.None
	self.scroll.BackgroundTransparency = 0
	Theme.paint(self.scroll, { BackgroundColor3 = { dark = "bg", light = "raised" } })
	Theme.corner(self.scroll, "control")
	Theme.stroke(self.scroll, "border")
	self.code = Theme.new("TextBox", { Name = "Arguments", Position = UDim2.fromOffset(GUTTER, GUTTER),
		Size = UDim2.fromOffset(1, 1), BackgroundTransparency = 1, BorderSizePixel = 0, Text = "", TextEditable = false,
		TextSize = CODE_SIZE, MultiLine = true, TextWrapped = true, ClearTextOnFocus = false, RichText = false,
		TextXAlignment = Enum.TextXAlignment.Left, TextYAlignment = Enum.TextYAlignment.Top }, self.scroll)
	self.code.Font = Theme.font("mono")
	Theme.paint(self.code, { TextColor3 = "text" })

	self.hint = Theme.label(card.body, "", { size = "xs", token = "text2", height = 18, name = "Hint" })
	self.hint.LayoutOrder = 4
	local buttons = Theme.new("Frame", { Name = "Actions", BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 32), LayoutOrder = 5 }, card.body)
	self.approve = Theme.button(buttons, "Permite", { kind = "primary", name = "ApproveCall",
		width = UDim.new(0.6, -Theme.SPACE.xs) })
	self.refuse = Theme.button(buttons, "Refuză", { name = "RefuseCall", width = UDim.new(0.4, -Theme.SPACE.xs) })
	self.refuse.Position = UDim2.new(0.6, Theme.SPACE.xs, 0, 0)

	Theme.click(self, self.approve, function()
		if self.current and self:inspectable(self.current.id) then decide(self.current.id, true) end
	end)
	Theme.click(self, self.refuse, function() if self.current then decide(self.current.id, false) end end)
	Theme.connect(self, self.root:GetPropertyChangedSignal("AbsoluteSize"), function() self:layout() end)
	Theme.connect(self, self.scroll:GetPropertyChangedSignal("AbsoluteWindowSize"), function() self:measure() end)
	Theme.connect(self, self.code:GetPropertyChangedSignal("TextFits"), function() self:refreshButtons() end)
	Theme.connect(self, self.code:GetPropertyChangedSignal("AbsoluteSize"), function() self:refreshButtons() end)
	return self
end

-- Cutia de argumente primește toată înălțimea rămasă din card; restul rândurilor au înălțimi fixe,
-- deci „Refuză” rămâne vizibil oricât de mică ar fi fereastra (o cutie prea mică lasă doar „Refuză”).
function ApprovalView:layout()
	if self.destroyed then return end
	self.scroll.Size = UDim2.new(1, 0, 0, math.max(0, self.root.AbsoluteSize.Y - FIXED_HEIGHT))
	self:measure()
end

function ApprovalView:inspectable(id)
	return not self.destroyed and self.windowVisible and self.current ~= nil and self.current.id == id and self.root.Visible
		and self.fullText and self.measured and self.scroll.AbsoluteWindowSize.Y >= 24
		and self.code.Text == self.text and self.code.TextFits
		and math.floor(self.scroll.AbsoluteWindowSize.X - GUTTER * 2) == self.width
		and math.abs(self.code.AbsoluteSize.X - self.width) <= 1 and self.code.AbsoluteSize.Y >= self.height
end

function ApprovalView:measure()
	if self.destroyed then return end
	self.measured = false
	local width = math.max(1, math.floor(self.scroll.AbsoluteWindowSize.X - GUTTER * 2))
	local success, bounds = pcall(function()
		return TextService:GetTextSize(self.code.Text, CODE_SIZE, Theme.font("mono"), Vector2.new(width, MAX_HEIGHT + 1))
	end)
	local fits = success and width >= 64 and bounds.X >= 0 and bounds.X <= width and bounds.Y >= 0 and bounds.Y < MAX_HEIGHT - 16
	self.width, self.height = width, if fits then math.ceil(bounds.Y) + 16 else MAX_HEIGHT
	self.code.Size = UDim2.fromOffset(self.width, self.height)
	self.scroll.CanvasSize = UDim2.fromOffset(0, self.height + GUTTER * 2)
	self.measured = fits
	self:refreshButtons()
end

-- Store-ul află dacă apelul a putut fi citit integral; HubView activează „Permite” din cardul lui pe același criteriu.
function ApprovalView:report(approval, ok)
	if not approval then
		self.reportedId, self.reported = nil, nil
		return
	end
	if self.reportedId == approval.id and self.reported == ok then return end
	self.reportedId, self.reported = approval.id, ok
	self.store:setApprovalInspectable(self.windowId, approval.id, ok)
end

function ApprovalView:refreshButtons()
	if self.destroyed then return end
	local approval = self.current
	local inspectable = approval ~= nil and self:inspectable(approval.id)
	Theme.enabled(self.approve, approval ~= nil and self.canDecide and not approval.inactive and not approval.blocked and inspectable)
	Theme.enabled(self.refuse, approval ~= nil and (self.canDecide or approval.inactive == true))
	self.refuse.Text = if approval and approval.inactive then "Închide" else "Refuză"
	self.hint.Text = if inspectable then "Cod integral. Derulează înainte de decizie." else "Afișare incompletă: numai Refuză."
	self:report(approval, inspectable)
end

function ApprovalView:update(window, connected)
	if self.destroyed then return end
	local approval = window.approvals[1]
	self.root.Visible = approval ~= nil
	self.windowVisible = window.visible
	self.canDecide = connected and window.visible and not window.decisionId and not window.cancelling
	if self.current ~= approval then
		self.current = approval
		self.text, self.fullText = "", false
		self.reportedId, self.reported = nil, nil
		if approval then self.text, self.fullText = formatArguments(approval) end
		self.code.Text = self.text
		self.scroll.CanvasPosition = Vector2.zero
		self:measure()
	end
	if approval then
		self.title.Text = if approval.inactive then "Apelul nu mai este activ" else "Modificarea propusă"
		self.meta.Text = if #window.approvals > 1 then "1 din " .. #window.approvals .. " în așteptare" else ""
		self.tool.Text = Theme.fit(tostring(approval.tool or "Instrument necunoscut"), 48)
	end
	self:refreshButtons()
end

function ApprovalView:destroy()
	Theme.cleanup(self)
	self.root:Destroy()
end

return ApprovalView
