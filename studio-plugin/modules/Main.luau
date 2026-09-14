-- Studio Harness App 1.0.0. Aplicația pluginului (Store, Controller, temă, ferestre): exclusiv UI nativ, citește DataModel-ul pentru inventar, nu modifică scena.
-- Loader-ul o pornește cu `Main.start(plugin, handoff?, toolbar?, button?)`, îi cere `app:snapshot()` și o oprește cu `app:destroy()` la metamorfoză.
local Store = require(script.Parent.SessionStore)
local Controller = require(script.Parent.BridgeController)
local Theme = require(script.Parent.Theme)
local HubView = require(script.Parent.HubView)
local SessionView = require(script.Parent.SessionView)
local Main = { VERSION = "1.0.0" }
local App = {}
App.__index = App

function App:ensure(id)
	local store = self.store
	if not self.alive or not store.windows[id] or self.views[id] or self.constructing[id] or self.failedViews[id] then return end
	self.constructing[id] = true
	local ok, view = pcall(SessionView.new, self.plugin, store, self.controller, id)
	self.constructing[id] = nil
	if ok then
		if self.alive and store.windows[id] then self.views[id] = view else view:destroy() end
	else
		self.failedViews[id] = true
		self.controller:status("Fereastra nativă nu a putut fi creată: " .. tostring(view), true)
	end
end

function App:prune()
	for id, view in pairs(self.views) do
		if not self.store.windows[id] then view:destroy(); self.views[id] = nil; self.failedViews[id] = nil end
	end
end

function App:sync(id)
	if not self.alive then return end
	local store = self.store
	if id then
		if store.windows[id] then self:ensure(id) else self:prune() end
	else
		-- Tabla poate crea sau elimina ferestre din terminal fără un id anume.
		self:prune()
		for _, windowId in ipairs(store.order) do self:ensure(windowId) end
	end
	if not self.alive then return end
	if id then
		if self.views[id] then self.views[id]:update() end
	else
		for _, view in pairs(self.views) do view:update() end
	end
	self.hub:update()
end

-- Handoff-ul pentru bundle-ul următor: store-ul serializat (fără cod local), starea legăturii și vizibilitatea hub-ului.
function App:snapshot()
	local hubVisible = self.hub ~= nil and self.hub.widget.Enabled == true
	return { version = Main.VERSION, store = self.store:serialize(), controller = self.controller:snapshot(), hubVisible = hubVisible }
end

-- Idempotent: este chemat de loader la metamorfoză și de `plugin.Unloading`; toolbar-ul primit rămâne al loader-ului.
function App:destroy()
	if not self.alive then return end
	self.alive = false
	if self.unsubscribe then self.unsubscribe() end
	self.controller:destroy()
	-- `Theme.cleanup` marchează owner-ul ca distrus și deconectează tot ce a intrat prin `Theme.connect` (inclusiv `ThemeChanged`).
	Theme.cleanup(self)
	for _, view in pairs(self.views) do pcall(view.destroy, view) end
	table.clear(self.views)
	if self.hub then pcall(self.hub.destroy, self.hub) end
	if self.ownsToolbar then pcall(function() self.toolbar:Destroy() end) end
end

-- Handoff-ul loader-ului 1.1.0 este `{ controller = { token, source } }`; cel de la metamorfoză adaugă `store` și `hubVisible`.
function Main.start(pluginObject, handoff, toolbar, button)
	handoff = if type(handoff) == "table" then handoff else nil
	local store = Store.deserialize(if handoff then handoff.store else nil)
	-- Loader-ul pune aici motivul pentru care bundle-ul nou nu a pornit; rulăm versiunea anterioară și o spunem în banner (§12.2).
	if handoff and type(handoff.bundleError) == "string" and handoff.bundleError ~= "" then
		store.bundleError = string.sub(handoff.bundleError, 1, 200)
	end
	local controller = Controller.new(pluginObject, store, if handoff then handoff.controller else nil)
	local self = setmetatable({ plugin = pluginObject, store = store, controller = controller, alive = true, destroyed = false,
		toolbar = toolbar, button = button, ownsToolbar = false, views = {}, constructing = {}, failedViews = {}, connections = {} }, App)
	local ok, failure = pcall(function()
		-- Tema Studio se citește o singură dată, înaintea oricărui view; la `ThemeChanged` paleta se repictează și view-urile se recalculează.
		Theme.install(self, function() self:sync(nil) end)
		if not self.toolbar then self.toolbar, self.ownsToolbar = pluginObject:CreateToolbar("Studio Harness"), true end
		if not self.button then
			self.button = self.toolbar:CreateButton("StudioHarnessMatrixToggle", "Deschide hubul sesiunilor", "", "Hub")
			self.button.ClickableWhenViewportHidden = true
		end
		self.hub = HubView.new(pluginObject, store, controller)
		if handoff and type(handoff.hubVisible) == "boolean" then self.hub.widget.Enabled = handoff.hubVisible end
		self.unsubscribe = store:subscribe(function(id) self:sync(id) end)
		Theme.connect(self, self.button.Click, function()
			self.hub.widget.Enabled = not self.hub.widget.Enabled
		end)
		Theme.connect(self, self.hub.widget:GetPropertyChangedSignal("Enabled"), function()
			self.button:SetActive(self.hub.widget.Enabled)
		end)
		Theme.connect(self, pluginObject.Unloading, function() self:destroy() end)
		self.button:SetActive(self.hub.widget.Enabled)
		self:sync(nil)
	end)
	if not ok then self:destroy(); error(failure, 0) end
	return self
end

return Main
