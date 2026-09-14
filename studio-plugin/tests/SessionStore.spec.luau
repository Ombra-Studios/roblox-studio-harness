-- Teste pure. Runnerul injectează modulul SessionStore; nu necesită DataModel sau HTTP.
return function(Store)
	local passed = 0
	local function test(name, run)
		local ok, message = pcall(run)
		assert(ok, name .. ": " .. tostring(message))
		passed += 1
	end
	local function ready()
		local store = Store.new()
		store.connected, store.compatible, store.bridgeId = true, true, "bridge-A"
		store.providers = { claude = { available = true }, codex = { available = true } }
		store:setStudios({ { id = "studio-A", name = "Scena A" }, { id = "studio-B", name = "Scena B" } })
		return store
	end
	local function window(store, id, provider)
		return store:create(id, provider or "claude", store:studio("studio-A"))
	end
	local function send(store, id, jobId, sessionId)
		store:setDraft(id, "Inspectează scena.")
		local request = assert(store:beginRequest(id, "request-" .. id, { selection = {} }))
		assert(store:acceptRequest(id, request.id, { job_id = jobId, session_id = sessionId, state = "queued" }))
		return request
	end
	-- Formele 1.0 din contract (§4.3, §4.6): identitate Roblox, meta workspace, hub, prezenți, sumarul workspace-urilor.
	local identity = { user_id = 12345, name = "ellob", avatar = "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48" }
	local workspace = { key = "game:987654", game_id = 987654, place_id = 1291603, name = "Ball", creator_id = 555, creator_type = "User" }
	local hub = { url = "https://hub.example/roblox/harness", status = "approved", hub_id = "hub-1", device_id = "dev-ellob-0123456789", error = nil, last_sync = 5, enrollment = "approve" }
	local members = { { device_id = "dev-ellob-0123456789", roblox_user_id = 12345, roblox_name = "ellob", machine = "PC-ELLOB", last_seen = 1, me = true, online = true },
		{ device_id = "dev-ana", roblox_user_id = 777, roblox_name = "ana", machine = "PC-ANA", last_seen = 0, me = false, online = true }, { bad = true } }
	local workspaces = { { key = "game:987654", name = "Ball", members_online = 2, sessions_active = 1, claims = 1, mine = true },
		{ key = "game:1", name = "Arena", members_online = 1, sessions_active = 1, claims = 0, mine = false } }
	-- `NONE` în `extra` șterge un câmp implicit (JSON `null`): un `nil` în constructor ar lăsa valoarea implicită.
	local NONE = {}
	local function merge(result, extra)
		for key, value in pairs(extra or {}) do result[key] = if value == NONE then nil else value end
		return result
	end
	local function board(sessions, extra)
		return merge({ ok = true, bridge_id = "bridge-A", developer = "ellob", default_studio_id = "studio-A",
			studios = { { id = "studio-A", name = "Scena A" }, { id = "studio-B", name = "Scena B" } },
			identity = identity, workspace = workspace, hub = hub, members = members, workspaces = workspaces,
			sessions = sessions or {}, claims = {}, journal = {} }, extra)
	end
	local function status(extra)
		local result = { ok = true, version = "1.0.0", bridge_id = "bridge-A", developer = "ellob", machine = "PC-ELLOB", plugin_connected = true,
			features = { queued_sessions = true, batch_events = true, terminal_sessions = true, claims = true, board = true, identity = true, workspaces = true, panel = true },
			providers = { claude = { available = true }, codex = { available = false } },
			identity = identity, workspace = workspace, hub = hub, panel_url = "https://hub.example/roblox/harness/panel", members = members, workspaces = workspaces,
			update = { current = "1.0.0", available = nil, state = "idle", message = "", restart_required = false },
			project = { snapshot_id = "snap-1", count = 138, taken = 3, place_name = "Ball" } }
		return merge(result, extra)
	end
	local function terminalSession(jobId, state, fields)
		local session = { job_id = jobId, kind = "terminal", provider = "codex", developer = "ellob", name = "Terminal B",
			state = state or "running", studio_id = "studio-B", cwd = "C:\\proiect", claims = { "Workspace.Map.Zone3" }, pending_approval = false,
			workspace = "game:987654", device_id = "dev-ellob-0123456789", roblox_user_id = 12345, machine = "PC-ELLOB", remote = false, mine = true }
		for key, value in pairs(fields or {}) do session[key] = value end
		return session
	end
	local function remoteSession(jobId, state, fields)
		local session = { job_id = jobId, kind = "studio", provider = "claude", developer = "ana", machine = "PC-ANA", device_id = "dev-ana", roblox_user_id = 777,
			remote = true, mine = false, name = "Sesiune Ana", state = state or "running", studio_id = "studio-ana", claims = { "Workspace.Map.Zone1" }, pending_approval = false,
			workspace = "game:987654" }
		for key, value in pairs(fields or {}) do session[key] = value end
		return session
	end
	local function ownSession(jobId, fields)
		local session = { job_id = jobId, kind = "studio", provider = "claude", developer = "ellob", state = "running", claims = {},
			workspace = "game:987654", device_id = "dev-ellob-0123456789", roblox_user_id = 12345, machine = "PC-ELLOB", remote = false, mine = true }
		for key, value in pairs(fields or {}) do session[key] = value end
		return session
	end

	test("Ferestre draft offline fără trimitere", function()
		local store = Store.new()
		local a = store:create("A", "claude", nil)
		assert(a and a.draft == "" and a.job == nil and a.kind == "studio")
		assert(a.mine and not a.remote and a.owner == nil and a.workspace == nil)
		assert(store:canSend(a) == false)
	end)
	test("Același provider nu partajează conversația", function()
		local store = ready()
		local a, b = window(store, "A"), window(store, "B")
		send(store, "A", "job-A", "session-A")
		send(store, "B", "job-B", "session-B")
		assert(a.sessionId ~= b.sessionId and a.job ~= b.job and a.messages ~= b.messages and a.approvals ~= b.approvals)
	end)
	test("Contextul și cererea sunt copiate o singură dată", function()
		local store = ready()
		local a = window(store, "A")
		store:setDraft("A", "Mesaj original")
		local context = { selection = { "Workspace.Part" } }
		local request = assert(store:beginRequest("A", "stable-id", context))
		context.selection[1] = "ALTĂ_SCENĂ"
		store:setDraft("A", "Draft nou")
		store:requestFailed("A", "stable-id", "Timeout", true)
		assert(a.request == request and request.id == "stable-id")
		assert(request.payload.prompt == "Mesaj original" and request.payload.context.selection[1] == "Workspace.Part")
		assert(store:beginRequest("A", "different-id", {}) == nil)
	end)
	test("Un draft editat în zbor nu este șters la confirmare", function()
		local store = ready()
		local a = window(store, "A")
		store:setDraft("A", "Prima cerere")
		store:beginRequest("A", "r1", {})
		store:setDraft("A", "Cererea următoare")
		assert(store:acceptRequest("A", "r1", { job_id = "j1", session_id = "s1" }))
		assert(a.draft == "Cererea următoare")
	end)
	test("Ținta nu se schimbă după prima trimitere", function()
		local store = ready()
		window(store, "A")
		send(store, "A", "job-A", "session-A")
		assert(store:bindStudio("A", store:studio("studio-B")) == false)
		assert(store.windows.A.studioId == "studio-A")
	end)
	test("Minimizarea păstrează jobul și primește evenimente", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		store:setVisible("A", false)
		store:applyEvents({ job_id = "job-A", state = "running", events = { { seq = 1, type = "text", text = "Răspuns" } } })
		assert(not a.visible and a.job.after == 1 and a.job.message.text == "Răspuns")
	end)
	test("Rutare după job, nu după fereastra focalizată", function()
		local store = ready()
		local a, b = window(store, "A"), window(store, "B", "codex")
		send(store, "A", "job-A", "session-A"); send(store, "B", "job-B", "session-B")
		store:applyEvents({ job_id = "job-B", state = "running", events = { { seq = 2, type = "text", text = "Doar B" } } })
		assert(a.job.after == 0 and a.job.message == nil)
		assert(b.job.after == 2 and b.job.message.text == "Doar B")
	end)
	test("Cursor independent și evenimente deduplicate", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		local batch = { job_id = "job-A", events = { { seq = 2, type = "text", text = "B" }, { seq = 1, type = "text", text = "A" } } }
		store:applyEvents(batch); store:applyEvents(batch)
		assert(a.job.after == 2 and a.job.message.text == "AB")
	end)
	test("Aprobarea expirată elimină numai ID-ul și jobul potrivit", function()
		local store = ready()
		local a, b = window(store, "A"), window(store, "B")
		send(store, "A", "job-A", "session-A"); send(store, "B", "job-B", "session-B")
		store:applyEvents({ job_id = "job-A", events = {
			{ seq = 1, type = "approval", approval_id = "approve-1", tool = "execute_luau", arguments = { code = "return 1" } },
			{ seq = 2, type = "approval", approval_id = "approve-2", tool = "execute_luau", arguments = { code = "return 2" } },
		} })
		store:applyEvents({ job_id = "job-B", events = { { seq = 1, type = "approval", approval_id = "approve-1", tool = "execute_luau", arguments = {} } } })
		store:removeApproval("A", "job-A", "approve-1")
		assert(#a.approvals == 1 and a.approvals[1].id == "approve-2")
		assert(#b.approvals == 1)
		store:applyEvents({ job_id = "job-A", events = { { seq = 3, type = "status", approval_id = "approve-2", approval_active = false } } })
		assert(#a.approvals == 0 and #b.approvals == 1)
	end)
	test("Aprobările pornesc neinspectabile; ApprovalView le marchează după afișarea integrală", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		store:applyEvents({ job_id = "job-A", events = { { seq = 1, type = "approval", approval_id = "ap-1", tool = "multi_edit", arguments = { file_path = "ServerScriptService.Main" } } } })
		assert(a.approvals[1].inspectable == false)
		local emitted = {}
		store:subscribe(function(id) table.insert(emitted, id) end)
		assert(store:setApprovalInspectable("A", "ap-1", true) and a.approvals[1].inspectable == true and #emitted == 1 and emitted[1] == "A")
		assert(store:setApprovalInspectable("A", "ap-1", true) and #emitted == 1)
		assert(store:setApprovalInspectable("A", "ap-1", "da") and a.approvals[1].inspectable == false and #emitted == 2)
		assert(not store:setApprovalInspectable("A", "ap-9", true) and not store:setApprovalInspectable("Z", "ap-1", true) and #emitted == 2)
		store:setApprovalInspectable("A", "ap-1", true)
		local restored = Store.deserialize(store:serialize())
		assert(restored.windows.A.approvals[1].id == "ap-1" and restored.windows.A.approvals[1].inspectable == false)
	end)
	test("Anularea nu afectează alte ferestre", function()
		local store = ready()
		local a, b = window(store, "A"), window(store, "B")
		send(store, "A", "job-A", "session-A"); send(store, "B", "job-B", "session-B")
		store:finish(a, "cancelled")
		assert(a.job == nil and a.state == "cancelled" and b.job.id == "job-B")
	end)
	test("Un snapshot 404 nu oprește aplicarea celorlalte", function()
		local store = ready()
		local a, b = window(store, "A"), window(store, "B")
		send(store, "A", "job-A", "session-A"); send(store, "B", "job-B", "session-B")
		store:applyEvents({ ok = false, status = 404, job_id = "job-A" })
		store:applyEvents({ job_id = "job-B", state = "running", events = { { seq = 1, type = "text", text = "B" } } })
		assert(a.job == nil and b.job.message.text == "B")
	end)
	test("Restart daemon păstrează textele, nu identitățile remote", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		store:setDraft("A", "Păstrează ăîșț 😀")
		local count = #a.messages
		assert(store:remoteChanged("bridge-B"))
		assert(a.job == nil and a.sessionId == nil and a.request == nil)
		assert(a.draft == "Păstrează ăîșț 😀" and #a.messages > count)
		assert(store:applyEvents({ job_id = "job-A", events = { { seq = 10, type = "text", text = "VECHI" } } }) == nil)
	end)
	test("Reconectarea la același daemon păstrează cursorul", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		a.job.after = 27
		assert(not store:remoteChanged("bridge-A"))
		assert(a.job.after == 27 and a.sessionId == "session-A")
	end)
	test("Închiderea nu poate șterge un job neterminal", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		assert(not store:remove("A"))
		store:finish(a, "cancelled")
		assert(store:remove("A") and store.windows.A == nil)
	end)
	test("Limitarea istoricului nu elimină joburile", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		for index = 1, Store.MAX_MESSAGES + 10 do store:message(a, "Status", tostring(index)) end
		assert(#a.messages == Store.MAX_MESSAGES and a.job.id == "job-A")
	end)
	test("Restaurarea explicită nu schimbă jobul sau cursorul", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		a.job.after = 9
		store:setVisible("A", false)
		store:restore("A"); store:restore("A")
		assert(a.visible and a.restoreVersion == 2 and a.job.after == 9)
	end)
	test("Restartul păstrează și mesajul original neconfirmat", function()
		local store = ready()
		local a = window(store, "A")
		store:setDraft("A", "Original")
		local request = assert(store:beginRequest("A", "r1", {}))
		store:setDraft("A", "Draft nou")
		store:remoteChanged("bridge-B")
		assert(a.request == nil and a.draft == "Draft nou")
		assert(table.find(a.messages, request.message) and request.message.text == "Original")
	end)
	test("Un 4xx definitiv după neconfirmare eliberează fereastra", function()
		local store = ready()
		local a = window(store, "A")
		store:setDraft("A", "Cerere")
		assert(store:beginRequest("A", "r1", {}))
		store:requestFailed("A", "r1", "Timeout", true)
		assert(a.request and a.state == "uncertain" and not store:canSend(a))
		a.closeAfterCancel = true
		store:requestFailed("A", "r1", "409 payload diferit", false)
		assert(a.request == nil and a.state == "failed" and not a.closeAfterCancel)
		assert(store:canSend(a) and store:remove("A"))
	end)
	test("Respingerea definitivă deblochează ținta fără conversație", function()
		local store = ready()
		local a = window(store, "A")
		store:setDraft("A", "Cerere")
		assert(store:beginRequest("A", "r1", {}) and a.locked)
		store:requestFailed("A", "r1", "400", false)
		assert(not a.locked and store:bindStudio("A", store:studio("studio-B")) and a.studioId == "studio-B")
		send(store, "A", "job-A", "session-A")
		store:setDraft("A", "A doua")
		assert(store:beginRequest("A", "r2", {}) == nil)
		store:finish(a, "completed")
		assert(store:beginRequest("A", "r2", {}))
		store:requestFailed("A", "r2", "400", false)
		assert(a.locked and not store:bindStudio("A", store:studio("studio-A")))
	end)
	test("Lista de scene rămâne până sosește una nouă", function()
		local store = ready()
		store.defaultStudioId = "studio-B"
		store:setStudios({ { id = "studio-B", name = "Scena B" }, { id = "studio-C", name = "Scena C" } })
		assert(store.defaultStudioId == "studio-B" and #store.studios == 2)
		store:setStudios({})
		assert(store.defaultStudioId == nil and #store.studios == 0)
	end)
	test("Prefixele nu rup Unicode", function()
		local text = "aă😀ț"
		for limit = 1, #text do assert(utf8.len(Store.prefix(text, limit)) ~= nil) end
	end)
	test("Trimiterea nu depinde de autentificare, ci de CLI-ul găsit", function()
		local store = ready()
		local a = window(store, "A")
		store:setDraft("A", "Cerere")
		assert(store:canSend(a))
		store.providers = { claude = { available = false }, codex = { available = true } }
		local allowed, reason = store:canSend(a)
		assert(not allowed and string.find(reason, "Claude Code"))
		store.providers = { claude = { auth = { checked = true, logged_in = true, subscription = true } } }
		assert(not store:canSend(a))
	end)
	test("Motivele canSend sunt textele 1.0 (fără buton de conectare, fără „scenă”)", function()
		local store = Store.new()
		local a = store:create("A", "claude", nil)
		local _, reason = store:canSend(a)
		assert(string.find(reason, "Daemon-ul nu răspunde", 1, true) and string.find(reason, "draft", 1, true))
		store.connected = true
		_, reason = store:canSend(a)
		assert(string.find(reason, "1.0", 1, true) and not string.find(reason, "0.4", 1, true))
		store.compatible, store.providers = true, { claude = { available = true } }
		_, reason = store:canSend(a)
		assert(string.find(reason, "Avansat", 1, true))
		a.studioId = "studio-gone"
		_, reason = store:canSend(a)
		assert(string.find(reason, "nu mai este conectat", 1, true))
	end)
	test("Compatibilitatea cere feature-urile 1.0 (identity, workspaces, panel)", function()
		local features = { queued_sessions = true, batch_events = true, terminal_sessions = true, claims = true, board = true, identity = true, workspaces = true, panel = true }
		assert(#Store.REQUIRED_FEATURES == 8 and table.find(Store.REQUIRED_FEATURES, "identity") and table.find(Store.REQUIRED_FEATURES, "workspaces") and table.find(Store.REQUIRED_FEATURES, "panel"))
		assert(Store.isCompatible({ bridge_id = "b", features = features }))
		assert(Store.isCompatible(status()))
		-- Daemon 0.8: fără identity/workspaces/panel (avea `team`) → incompatibil, chiar cu feature-urile vechi complete.
		assert(not Store.isCompatible({ bridge_id = "b", version = "0.8.0", features = { queued_sessions = true, batch_events = true, terminal_sessions = true, claims = true, board = true, team = true } }))
		features.panel = nil
		assert(not Store.isCompatible({ bridge_id = "b", features = features }))
		features.panel, features.board = true, nil
		assert(not Store.isCompatible({ bridge_id = "b", features = features }))
		features.board = true
		assert(not Store.isCompatible({ features = features }))
		assert(not Store.isCompatible({ bridge_id = "", features = features }))
		assert(not Store.isCompatible(nil))
	end)
	test("Statusul 1.0 mapează identitatea, workspace-ul, hub-ul, prezenții și workspace-urile", function()
		local store = ready()
		local emitted = 0
		store:subscribe(function(id) emitted += 1; assert(id == nil) end)
		assert(store:applyStatus(status()) and emitted == 1)
		assert(store.identity.userId == 12345 and store.identity.name == "ellob" and store.identity.avatar == "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48")
		assert(store.workspace.key == "game:987654" and store.workspace.name == "Ball" and store.workspace.placeId == 1291603 and store.workspace.gameId == 987654)
		assert(store.workspace.creatorId == 555 and store.workspace.creatorType == "User" and store:workspaceKey() == "game:987654")
		assert(store.hub.status == "approved" and store.hub.url == "https://hub.example/roblox/harness" and store.hub.hubId == "hub-1" and store.hub.deviceId == "dev-ellob-0123456789")
		assert(store.hub.error == nil and store.hub.lastSync == 5 and store.hub.enrollment == "approve" and store.hub.panelUrl == "https://hub.example/roblox/harness/panel")
		assert(#store.members == 2 and store.members[1].name == "ellob" and store.members[1].me and store.members[1].userId == 12345 and store.members[1].machine == "PC-ELLOB")
		assert(store.members[2].deviceId == "dev-ana" and not store.members[2].me and store.members[2].lastSeen == 0 and store.members[2].online)
		assert(#store.workspaces == 2 and store.workspaces[1].name == "Ball" and store.workspaces[1].mine and store.workspaces[1].membersOnline == 2 and store.workspaces[1].sessionsActive == 1 and store.workspaces[1].claims == 1)
		assert(store.workspaces[2].key == "game:1" and not store.workspaces[2].mine and store:workspaceRow("game:1").name == "Arena" and store:workspaceRow("game:9") == nil)
		assert(store.machine == "PC-ELLOB" and store.developer == "ellob" and store.pluginConnected and store.providers.codex.available == false)
		assert(store.daemonProject.snapshotId == "snap-1" and store.daemonProject.count == 138 and store.daemonProject.placeName == "Ball" and store.daemonProject.taken == 3)
		assert(store.update.current == "1.0.0" and store.update.state == "idle")
		assert(store.team == nil)
	end)
	test("Identitatea necunoscută, avatarul derivat și valorile invalide au implicite sigure", function()
		local store = ready()
		store:applyStatus(status())
		store:applyStatus(status({ identity = { user_id = 0, name = "Studio", avatar = NONE }, workspace = { key = "local", game_id = 0, place_id = 0, name = "", creator_id = 0, creator_type = NONE },
			hub = { status = "bogus", url = "https://hub.example", error = "linie\nnouă  " }, panel_url = NONE, members = "nu", workspaces = { { key = "" }, { name = "fără cheie" }, { key = "place:5", members_online = { {}, {} }, sessions_active = -2, claims = "x" } },
			project = NONE, plugin_connected = "da", machine = 7 }))
		assert(store.identity.userId == 0 and store.identity.name == "Studio" and store.identity.avatar == nil)
		assert(store.workspace.key == "local" and store.workspace.name == "local" and store.workspace.creatorType == nil and store.workspace.placeId == 0)
		assert(store.hub.status == "connecting" and store.hub.error == "linie nouă" and store.hub.panelUrl == "https://hub.example/panel" and store.hub.deviceId == nil)
		assert(#store.members == 0 and #store.workspaces == 1 and store.workspaces[1].name == "place:5" and store.workspaces[1].membersOnline == 2 and store.workspaces[1].sessionsActive == 0 and store.workspaces[1].claims == 0)
		assert(store.daemonProject == nil and store.pluginConnected == false and store.machine == "PC-ELLOB")
		store:applyStatus(status({ identity = { user_id = 42 }, workspace = NONE, hub = { status = "disabled", url = "https://hub.example", error = "hub_url invalid (sursa: env/config)" }, panel_url = NONE }))
		assert(store.identity.userId == 42 and store.identity.name == "user_42" and store.identity.avatar == "rbxthumb://type=AvatarHeadShot&id=42&w=48&h=48")
		assert(store.workspace == nil and store:workspaceKey() == nil and store.hub.status == "disabled" and store.hub.panelUrl == nil)
		assert(store:applyStatus("text") == false and store:applyStatus(nil) == false)
		assert(Store.identityFrom(nil) == nil and Store.identityFrom({ user_id = "12" }).userId == 12 and Store.workspaceFrom({ name = "x" }) == nil)
		assert(Store.avatarUrl(0) == nil and Store.avatarUrl("x") == nil and Store.avatarUrl(7.9) == "rbxthumb://type=AvatarHeadShot&id=7&w=48&h=48")
	end)
	test("Modul de aprobare vine din daemon și rezistă la răspunsuri fără el sau cu valori străine", function()
		local store = Store.new()
		assert(store.autoApprove == "ask", "implicit, fiecare operație se aprobă de om")
		store:applyStatus({ settings = { auto_approve = "all" } })
		assert(store.autoApprove == "all")
		-- Un daemon mai vechi nu trimite `settings`: păstrăm ultima valoare cunoscută, nu o resetăm sub agent.
		store:applyStatus({})
		assert(store.autoApprove == "all")
		for _, bad in ipairs({ "da", "", 1, true }) do
			store:applyStatus({ settings = { auto_approve = bad } })
			assert(store.autoApprove == "all", "o valoare necunoscută nu schimbă modul")
		end
		store:applyStatus({ settings = { auto_approve = "edits" } })
		assert(store.autoApprove == "edits")
		store:applyStatus({ settings = "text" })
		assert(store.autoApprove == "edits")
	end)
	test("Identitatea locală supraviețuiește unui status fără identitate (înainte de primul /v1/identity)", function()
		local store = Store.new()
		-- Controllerul publică local contul citit din StudioService înainte ca daemon-ul să îl cunoască.
		store.identity = { userId = 12345, name = "ellob", avatar = "rbxthumb://type=AvatarHeadShot&id=12345&w=48&h=48" }
		store:applyStatus(status({ identity = NONE }))
		assert(store.identity ~= nil and store.identity.userId == 12345 and store.identity.name == "ellob")
		-- Confirmarea daemon-ului are întâietate; un store gol rămâne gol.
		store:applyStatus(status({ identity = { user_id = 777, name = "ana" } }))
		assert(store.identity.userId == 777 and store.identity.name == "ana")
		local empty = Store.new()
		empty:applyStatus(status({ identity = NONE }))
		assert(empty.identity == nil)
	end)
	test("hubStatusText acoperă toate stările, în română, cu rolul de culoare", function()
		local store = Store.new()
		local text, role = store:hubStatusText()
		assert(string.find(text, "Daemon-ul nu răspunde", 1, true) and role == "neutral")
		store.connected = true
		text, role = store:hubStatusText()
		assert(text == "Se conectează la hub…" and role == "neutral")
		store.hub = Store.hubFrom({ status = "pending", device_id = "ab12cd34ef567890" })
		text, role = store:hubStatusText()
		assert(text == "Așteaptă aprobarea adminului · dispozitiv ab12cd34" and role == "warning")
		store.hub = Store.hubFrom({ status = "pending" })
		assert(store:hubStatusText() == "Așteaptă aprobarea adminului · dispozitiv —")
		store.hub = Store.hubFrom({ status = "approved", url = "https://hub.example" })
		text, role = store:hubStatusText()
		assert(text == "Hub conectat" and role == "primary")
		store.hub = Store.hubFrom({ status = "offline", error = "timeout după 5 s." })
		text, role = store:hubStatusText()
		assert(text == "Hub offline: timeout după 5 s. Sesiunile tale merg local; claims-urile sunt doar locale până revine." and role == "danger")
		store.hub = Store.hubFrom({ status = "offline" })
		assert(string.find(store:hubStatusText(), "Hub offline: hub-ul nu răspunde. ", 1, true))
		store.hub = Store.hubFrom({ status = "offline", error = string.rep("e", 200) })
		assert(#store:hubStatusText() < 200)
		store.hub = Store.hubFrom({ status = "offline", error = "Hub-ul rulează o versiune mai veche." })
		text, role = store:hubStatusText()
		assert(text == "Hub-ul rulează o versiune mai veche. Actualizează hub-ul ca să apari în workspace." and role == "danger")
		store.hub = Store.hubFrom({ status = "revoked" })
		text, role = store:hubStatusText()
		assert(text == "Accesul acestui dispozitiv a fost revocat. Cere adminului să-l aprobe din nou." and role == "danger")
		store.hub = Store.hubFrom({ status = "disabled" })
		text, role = store:hubStatusText()
		assert(text == "Hub dezactivat (daemon pornit cu --no-hub)." and role == "neutral")
		for state in pairs(Store.HUB_STATES) do
			local line, lineRole = Store.hubText({ status = state })
			assert(type(line) == "string" and line ~= "" and lineRole == Store.HUB_ROLES[state])
		end
		assert(Store.hubText(nil) == "Se conectează la hub…")
	end)
	test("workspaceLabel: numele jocului, fișier nepublicat, workspace necunoscut, workspace privit", function()
		local store = Store.new()
		assert(store:workspaceLabel() == "Workspace necunoscut" and Store.workspaceName(nil) == "Workspace necunoscut")
		store.workspace = Store.workspaceFrom({ key = "local", name = "local" })
		assert(store:workspaceLabel() == "Fișier nepublicat")
		store.workspace = Store.workspaceFrom({ key = "local", name = "Baseplate" })
		assert(store:workspaceLabel() == "Baseplate")
		store.workspace = Store.workspaceFrom(workspace)
		assert(store:workspaceLabel() == "Ball" and store:viewedWorkspace() == store.workspace)
		store:applyWorkspaces(workspaces)
		assert(store:setViewWorkspace("game:1") and store.viewWorkspace == "game:1" and store:workspaceLabel() == "Arena")
		assert(store:viewedWorkspace().key == "game:1" and store:viewedWorkspace().name == "Arena")
		assert(store:setViewWorkspace("game:77") and store:workspaceLabel() == "game:77")
		assert(store:setViewWorkspace(nil) and store.viewWorkspace == nil and store:workspaceLabel() == "Ball")
		assert(Store.workspaceName({ key = "game:5" }) == "game:5" and Store.workspaceName({ name = "Doar nume" }) == "Doar nume" and Store.workspaceName({}) == "Workspace necunoscut")
	end)
	test("Vizualizarea altui workspace: setViewWorkspace, applyWorkspaceView, membersInView, sessionsInView", function()
		local store = ready()
		store:applyStatus(status())
		local emitted = 0
		store:subscribe(function() emitted += 1 end)
		assert(not store:setViewWorkspace("") and not store:setViewWorkspace(7) and not store:setViewWorkspace(string.rep("k", 65)) and emitted == 0)
		assert(store:setViewWorkspace("game:987654") and store.viewWorkspace == nil and emitted == 0)
		assert(#store:membersInView() == 2 and store:membersInView()[1].me and #store:sessionsInView() == 0)
		assert(store:setViewWorkspace("game:1") and store.viewWorkspace == "game:1" and store.view == nil and emitted == 1)
		assert(store:setViewWorkspace("game:1") and emitted == 1)
		assert(#store:membersInView() == 0 and #store:sessionsInView() == 0 and store:workspaceLabel() == "Arena")
		local data = { ok = true, workspace = { key = "game:1", game_id = 1, place_id = 88120044, name = "Arena", creator_id = 555, creator_type = "Group", first_seen = 0, last_seen = 0 },
			members = { { device_id = "dev-mihai", roblox_user_id = 9, roblox_name = "mihai", machine = "PC-MIHAI", last_seen = 2, online = true },
				{ device_id = "dev-ellob-0123456789", roblox_user_id = 12345, roblox_name = "ellob", machine = "PC-ELLOB", last_seen = 9, online = true },
				{ device_id = "dev-zed", roblox_user_id = 0, roblox_name = "", machine = "PC-ZED", last_seen = 1, online = true }, "x" },
			sessions = { { job_id = "job-old", kind = "terminal", provider = "codex", developer = "mihai", state = "completed", started = 50, device_id = "dev-mihai", roblox_user_id = 9, machine = "PC-MIHAI", workspace = "game:1" },
				{ job_id = "job-new", kind = "studio", provider = "claude", developer = "mihai", name = "Terrain Arena", state = "running", started = 10, device_id = "dev-mihai", roblox_user_id = 9, machine = "PC-MIHAI", workspace = "game:1", claims = { "Workspace.Terrain" } },
				{ job_id = "job-mine", kind = "terminal", provider = "gemini", developer = "ellob", state = "bogus", started = 20, device_id = "dev-ellob-0123456789", roblox_user_id = 12345, machine = "PC-ELLOB" }, { kind = "studio" } },
			claims = { { path = "Workspace.Terrain", job_id = "job-new" } }, journal = {}, project = nil }
		assert(not store:applyWorkspaceView("game:2", data) and store.view == nil and emitted == 1)
		assert(store:applyWorkspaceView("game:1", data) and emitted == 2)
		assert(store.view.key == "game:1" and store.view.workspace.name == "Arena" and store.view.workspace.creatorType == "Group" and store.view.workspace.placeId == 88120044 and store.view.claims == 1)
		local present = store:membersInView()
		assert(#present == 3 and present[1].name == "ellob" and present[1].me and present[2].name == "mihai" and present[3].name == "PC-ZED" and present[3].userId == 0)
		local sessions = store:sessionsInView()
		assert(#sessions == 3 and sessions[1].jobId == "job-mine" and sessions[2].jobId == "job-new" and sessions[3].jobId == "job-old")
		assert(sessions[1].mine and sessions[1].provider == "claude" and sessions[1].state == "running" and sessions[1].name == "Terminal" and sessions[1].kind == "terminal")
		assert(not sessions[2].mine and sessions[2].owner.name == "mihai" and sessions[2].owner.userId == 9 and sessions[2].claims[1] == "Workspace.Terrain" and sessions[2].workspace == "game:1")
		assert(sessions[3].state == "completed" and store:viewedWorkspace() == store.view.workspace)
		assert(store:sessionsInView() ~= store.view.sessions and store:membersInView() ~= store.view.members)
		assert(store:setViewWorkspace(nil) and store.view == nil and #store:membersInView() == 2 and store:workspaceLabel() == "Ball")
		assert(not store:applyWorkspaceView("game:1", data) and store.view == nil)
		store:setViewWorkspace("game:1")
		assert(not store:applyWorkspaceView("game:1", "text") and store:applyWorkspaceView("game:1", {}) and store.view.workspace.name == "Arena" and #store.view.members == 0)
		store:setViewWorkspace("game:9")
		assert(store:applyWorkspaceView("game:9", {}) and store.view.workspace.key == "game:9" and store.view.workspace.name == "game:9")
	end)
	test("Prezenții sunt sortați cu „tu” primul, apoi după ultima activitate și nume", function()
		local store = ready()
		store:applyStatus(status({ members = { { device_id = "d3", roblox_name = "zoe", last_seen = 5 }, { device_id = "d2", roblox_name = "ana", last_seen = 5 },
			{ device_id = "d4", machine = "PC-4", last_seen = 50 }, { device_id = "dev-ellob-0123456789", roblox_name = "ellob", last_seen = 1 }, { roblox_name = "fără dispozitiv" } } }))
		assert(#store.members == 4 and store.members[4].me and store.members[3].name == "PC-4")
		local sorted = store:membersInView()
		assert(sorted[1].name == "ellob" and sorted[2].name == "PC-4" and sorted[3].name == "ana" and sorted[4].name == "zoe")
		assert(store.members[1].name == "zoe")
	end)
	test("Tabla creează ferestre din terminal doar de citit", function()
		local store = ready()
		assert(store:syncBoard(board({ terminalSession("job-T") })))
		local id = Store.terminalWindowId("job-T")
		local t = store.windows[id]
		assert(t and t.kind == "terminal" and t.provider == "codex" and t.developer == "ellob" and t.name == "Terminal B" and not t.remote and t.mine)
		assert(t.owner.name == "ellob" and t.owner.deviceId == "dev-ellob-0123456789" and t.owner.userId == 12345 and t.owner.machine == "PC-ELLOB" and t.workspace == "game:987654")
		assert(t.job and t.job.id == "job-T" and t.job.after == 0 and t.state == "running" and t.locked)
		assert(t.studioId == "studio-B" and t.studioName == "Scena B" and t.claims[1] == "Workspace.Map.Zone3")
		assert(store:canSend(t) == false and store:bindStudio(id, store:studio("studio-A")) == false)
		store:setDraft(id, "text"); store:rename(id, "alt nume")
		assert(t.draft == "" and t.name == "Terminal B")
		assert(store.developer == "ellob" and store.defaultStudioId == "studio-A")
		assert(#store:jobs() == 1 and store:jobs()[1].job_id == "job-T")
		t.job.after = 5
		store:syncBoard(board({ terminalSession("job-T", "running", { claims = {}, name = "Redenumită" }) }))
		assert(store.windows[id] == t and t.job.after == 5 and #t.claims == 0 and t.name == "Redenumită")
	end)
	test("Evenimentele prompt și claim ajung în feed-ul ferestrei terminal", function()
		local store = ready()
		store:syncBoard(board({ terminalSession("job-T") }))
		local t = store.windows[Store.terminalWindowId("job-T")]
		local before = #t.messages
		store:applyEvents({ job_id = "job-T", state = "running", events = {
			{ seq = 1, type = "prompt", text = "Construiește o platformă." },
			{ seq = 2, type = "claim", action = "claimed", paths = { "Workspace.Map.Zone3" } },
			{ seq = 3, type = "text", text = "Încep." },
			{ seq = 4, type = "claim", action = "denied", paths = { "Workspace.Map" }, holder = "dev-a" },
		} })
		assert(#t.messages == before + 4 and t.job.after == 4)
		assert(t.messages[before + 1].author == "ellob · terminal" and t.messages[before + 1].kind == "user")
		assert(t.messages[before + 2].kind == "claim" and string.find(t.messages[before + 2].text, "Claim: Workspace.Map.Zone3", 1, true))
		assert(t.messages[before + 3].kind == "assistant" and t.messages[before + 3].text == "Încep.")
		assert(string.find(t.messages[before + 4].text, "deținut de dev-a", 1, true))
	end)
	test("Aprobările sosesc și pe joburile din terminal", function()
		local store = ready()
		store:syncBoard(board({ terminalSession("job-T", "waiting_approval", { pending_approval = true }) }))
		local t = store.windows[Store.terminalWindowId("job-T")]
		assert(t.pendingApproval and t.state == "waiting_approval")
		store:applyEvents({ job_id = "job-T", state = "waiting_approval", events = {
			{ seq = 1, type = "approval", approval_id = "gen-1", tool = "generate_mesh", arguments = { prompt = "copac" } } } })
		assert(#t.approvals == 1 and t.approvals[1].jobId == "job-T" and not t.approvals[1].blocked and t.approvals[1].inspectable == false)
		store:applyEvents({ job_id = "job-T", state = "running", events = { { seq = 2, type = "status", approval_id = "gen-1", approval_active = false } } })
		assert(#t.approvals == 0 and t.state == "running")
	end)
	test("Fereastra terminal închisă nu reapare cât jobul e în board", function()
		local store = ready()
		store:syncBoard(board({ terminalSession("job-T") }))
		local id = Store.terminalWindowId("job-T")
		assert(not store:dismissTerminal(id) and store.windows[id] and not store.windows[id].visible)
		store:syncBoard(board({ terminalSession("job-T", "completed") }))
		local t = store.windows[id]
		assert(t.job == nil and t.state == "completed")
		assert(store:dismissTerminal(id) and store.windows[id] == nil)
		store:syncBoard(board({ terminalSession("job-T", "completed") }))
		assert(store.windows[id] == nil)
		store:syncBoard(board({ terminalSession("job-T2") }))
		assert(store.windows[Store.terminalWindowId("job-T2")] and store.windows[id] == nil)
	end)
	test("Sesiunea dispărută din board este eliminată", function()
		local store = ready()
		store:syncBoard(board({ terminalSession("job-T") }))
		local id = Store.terminalWindowId("job-T")
		store:syncBoard(board({}))
		assert(store.windows[id] == nil and #store.order == 0 and next(store.dismissedTerminal) == nil)
	end)
	test("Tabla actualizează claims-urile ferestrelor locale, claims și jurnal", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		store:syncBoard(board({ ownSession("job-A", { claims = { "ServerScriptService.Main" } }) }, {
			claims = { { path = "ServerScriptService.Main", job_id = "job-A", developer = "ellob", since = 1, expires = 601, reason = "editare", workspace = "game:987654", mine = true }, { path = "" } },
			journal = { { seq = 812, time = 1, job_id = "job-A", developer = "ellob", roblox_user_id = 12345, provider = "claude", tool = "multi_edit", paths = { "ServerScriptService.Main" }, summary = "3 editări", workspace = "game:987654" }, { bad = true } },
		}))
		assert(a.claims[1] == "ServerScriptService.Main" and #store.claims == 1 and store.claims[1].jobId == "job-A")
		assert(store.claims[1].mine == true and store.claims[1].workspace == "game:987654" and store.claims[1].expires == 601 and store.claims[1].reason == "editare" and store:ownsClaim(store.claims[1]))
		assert(#store.journal == 1 and store.journal[1].tool == "multi_edit" and store.journal[1].paths[1] == "ServerScriptService.Main")
		assert(store.journal[1].seq == 812 and store.journal[1].workspace == "game:987654" and store.journal[1].provider == "claude" and store.journal[1].userId == 12345 and store.journal[1].summary == "3 editări")
		store:finish(a, "completed")
		assert(#a.claims == 0)
		-- Jurnal local (daemon fără hub): fără seq și fără workspace; providerul necunoscut devine nil.
		store:syncBoard(board({}, { journal = { { time = 2, job_id = "job-A", developer = "ellob", provider = "x", tool = "execute_luau", paths = {}, summary = "" } } }))
		assert(store.journal[1].seq == nil and store.journal[1].workspace == nil and store.journal[1].provider == nil and store.journal[1].userId == 0)
	end)
	test("Jurnalul păstrează ultimele 10 intrări, cea mai nouă prima", function()
		local store = ready()
		local entries = {}
		for index = 1, 25 do table.insert(entries, { seq = index, time = index, tool = "tool" .. index, developer = "ana", paths = {}, summary = "" }) end
		store:syncBoard(board({}, { journal = entries }))
		assert(#store.journal == Store.MAX_JOURNAL and store.journal[1].seq == 25 and store.journal[10].seq == 16)
	end)
	test("Restartul daemon-ului elimină ferestrele terminal, tabla și hub-ul", function()
		local store = ready()
		window(store, "A")
		store:syncBoard(board({ terminalSession("job-T") }, { claims = { { path = "Workspace", job_id = "job-T", developer = "dev-b" } } }))
		assert(store.hub.status == "approved" and #store.members == 2 and #store.workspaces == 2 and store.identity.name == "ellob")
		store:setViewWorkspace("game:1")
		assert(store:remoteChanged("bridge-B"))
		assert(store.windows[Store.terminalWindowId("job-T")] == nil and store.windows.A and #store.claims == 0 and #store.order == 1)
		assert(store.hub.status == "connecting" and store.hub.url == nil and #store.members == 0 and #store.workspaces == 0 and store.viewWorkspace == nil and store.view == nil)
		assert(store.identity.name == "ellob" and store.workspace.key == "game:987654" and store.daemonProject == nil and not store.pluginConnected)
	end)
	test("Deconectarea daemon-ului uită hub-ul și prezenții, dar nu jocul deschis", function()
		local store = ready()
		store:applyStatus(status())
		store:setViewWorkspace("game:1")
		store:resetHub()
		assert(store.hub.status == "connecting" and store.hub.panelUrl == nil and #store.members == 0 and #store.workspaces == 0 and store.viewWorkspace == nil)
		assert(store.identity.userId == 12345 and store.workspace.name == "Ball" and store.daemonProject == nil and store.pluginConnected == false)
		store.connected = false
		assert(string.find(store:hubStatusText(), "Daemon-ul nu răspunde", 1, true))
	end)
	test("Scena implicită se schimbă doar către o scenă conectată", function()
		local store = ready()
		assert(store:setDefaultStudio("studio-B") and store.defaultStudioId == "studio-B")
		assert(not store:setDefaultStudio("studio-X") and store.defaultStudioId == "studio-B")
		store:syncBoard(board({}, { default_studio_id = "studio-A" }))
		assert(store.defaultStudioId == "studio-A")
	end)
	test("Sesiunile remote sunt doar de citit, cu proprietar și mașină", function()
		local store = ready()
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		assert(store:syncBoard(board({ remoteSession("job-R"), ownSession("job-A") }, {
			claims = { { path = "Workspace.Map.Zone1", job_id = "job-R", developer = "ana", since = 1, mine = false }, { path = "Lighting", job_id = "job-A", developer = "ellob", since = 1 },
				{ path = "Workspace.Map.Zone9", job_id = "job-gone", developer = "ana", since = 1 }, { path = "Workspace.Map.Zone8", job_id = "job-gone2", developer = "ellob", since = 1 } } })))
		local id = Store.terminalWindowId("job-R")
		local r = store.windows[id]
		assert(r and r.remote and not r.mine and r.kind == "terminal" and r.sessionKind == "studio" and r.locked)
		assert(r.developer == "ana" and r.machine == "PC-ANA" and r.provider == "claude" and r.name == "Sesiune Ana" and r.workspace == "game:987654")
		assert(r.owner.name == "ana" and r.owner.deviceId == "dev-ana" and r.owner.userId == 777 and r.owner.machine == "PC-ANA")
		assert(r.job and r.job.id == "job-R" and r.state == "running" and r.claims[1] == "Workspace.Map.Zone1")
		assert(store:canSend(r) == false and store:bindStudio(id, store:studio("studio-A")) == false)
		assert(#store:jobs() == 2 and store:findJob("job-R") == r and store.windows.A == a)
		assert(#store.claims == 4 and not store:ownsClaim(store.claims[1]) and store:ownsClaim(store.claims[2]) and not store:ownsClaim(store.claims[3]) and store:ownsClaim(store.claims[4]))
		assert(store.claims[1].mine == false and store.claims[2].mine == nil)
		local mine, others = store:sessionsMine(), store:sessionsOthers()
		assert(#mine == 1 and mine[1] == a and #others == 1 and others[1] == r)
		local before = #r.messages
		store:applyEvents({ job_id = "job-R", state = "running", remote = true, events = {
			{ seq = 1, type = "prompt", text = "Ridică zidul." }, { seq = 2, type = "text", text = "Lucrez." },
			{ seq = 3, type = "approval", approval_id = "ap-1", tool = "generate_mesh", arguments = { prompt = "zid" } } } })
		assert(r.job.after == 3 and #r.messages == before + 3 and r.messages[before + 1].author == "ana · terminal")
		assert(#r.approvals == 0 and r.messages[before + 3].kind == "approval" and string.find(r.messages[before + 3].text, "aprobarea lui ana", 1, true))
		store:syncBoard(board({ remoteSession("job-R", "completed") }))
		assert(store.windows[id] == r and r.job == nil and r.state == "completed" and #r.claims == 0)
	end)
	test("`mine` de la daemon are prioritate; fără el, developerul diferit este tratat ca remote", function()
		local store = ready()
		store:syncBoard(board({ { job_id = "job-T", kind = "terminal", provider = "codex", developer = "dev-b", machine = "PC-B", state = "running" } }))
		local t = store.windows[Store.terminalWindowId("job-T")]
		assert(t and t.remote and not t.mine and t.machine == "PC-B" and t.sessionKind == "terminal" and t.owner.name == "dev-b" and t.owner.deviceId == nil)
		local store2 = ready()
		store2:syncBoard(board({ terminalSession("job-T") }))
		assert(not store2.windows[Store.terminalWindowId("job-T")].remote)
		-- Același nume Roblox pe două dispozitive: daemon-ul decide prin `mine`.
		local store3 = ready()
		store3:syncBoard(board({ terminalSession("job-T", "running", { mine = false, remote = true, device_id = "dev-other", machine = "PC-OTHER" }) }))
		local twin = store3.windows[Store.terminalWindowId("job-T")]
		assert(twin.remote and not twin.mine and twin.owner.deviceId == "dev-other")
		local store4 = ready()
		store4:syncBoard(board({ terminalSession("job-T", "running", { developer = "alt-nume", mine = true, remote = false }) }))
		assert(not store4.windows[Store.terminalWindowId("job-T")].remote)
	end)
	test("Ferestrele proprii primesc owner și workspace din identitate, tablă și răspunsul la trimitere", function()
		local store = ready()
		local a = window(store, "A")
		assert(a.owner == nil and a.workspace == nil)
		store:applyStatus(status())
		assert(a.owner and a.owner.name == "ellob" and a.owner.userId == 12345 and a.owner.deviceId == "dev-ellob-0123456789" and a.owner.machine == "PC-ELLOB")
		local b = window(store, "B")
		assert(b.owner.name == "ellob" and b.workspace == "game:987654" and b.owner ~= a.owner)
		store:setDraft("A", "Cerere")
		assert(store:beginRequest("A", "r1", {}))
		assert(store:acceptRequest("A", "r1", { job_id = "job-A", session_id = "s-A", state = "queued", workspace = "game:987654" }))
		assert(a.workspace == "game:987654")
		store:syncBoard(board({ ownSession("job-A", { workspace = "game:555" }) }))
		assert(a.workspace == "game:555" and a.owner.deviceId == "dev-ellob-0123456789")
		store:applyStatus(status({ identity = { user_id = 0, name = "Studio" }, hub = { status = "pending", device_id = "dev-x" } }))
		assert(a.owner.name == "Studio" and a.owner.userId == 0 and a.owner.deviceId == "dev-x")
		local fresh = Store.new()
		fresh.machine = "PC-ONLY"
		local c = fresh:create("C", "claude", nil)
		assert(c.owner.name == "PC-ONLY" and c.owner.userId == 0 and c.owner.deviceId == nil)
	end)
	test("sessionsMine și sessionsOthers păstrează ordinea ferestrelor și separă sesiunile colegilor", function()
		local store = ready()
		window(store, "A")
		store:syncBoard(board({ remoteSession("job-R"), terminalSession("job-T") }))
		window(store, "B")
		local mine, others = store:sessionsMine(), store:sessionsOthers()
		assert(#mine == 3 and mine[1].id == "A" and mine[2].id == Store.terminalWindowId("job-T") and mine[3].id == "B")
		assert(#others == 1 and others[1].id == Store.terminalWindowId("job-R"))
		store:syncBoard(board({}))
		assert(#store:sessionsMine() == 2 and #store:sessionsOthers() == 0)
	end)
	test("Starea actualizării este validată și implicită", function()
		local store = Store.new()
		local update = store.update
		assert(update.current == "" and update.available == nil and update.state == "idle" and update.message == "" and update.restartRequired == false)
		store:applyUpdate({ current = "0.6.0", available = "0.6.1", state = "available", message = "", checked = 1, restart_required = false })
		assert(store.update ~= update and store.update.current == "0.6.0" and store.update.available == "0.6.1" and store.update.state == "available")
		assert(store.update.message == "" and store.update.restartRequired == false and store.update.checked == nil)
		store:applyUpdate({ current = "0.6.0", available = nil, state = "installed", message = "Actualizare 0.6.1 instalată;\nrepornește Studio.", checked = 2, restart_required = true })
		assert(store.update.state == "installed" and store.update.available == nil and store.update.restartRequired)
		assert(store.update.message == "Actualizare 0.6.1 instalată; repornește Studio." and not string.find(store.update.message, "\n", 1, true))
		store:applyUpdate({ current = "0.6.0", available = "", state = "error", message = string.rep("x", 400), restart_required = false })
		assert(store.update.state == "error" and store.update.available == nil and #store.update.message == 200)
		store:applyUpdate({ current = 6, available = 7, state = "bogus", message = { "nu" }, restart_required = "da" })
		assert(store.update.current == "" and store.update.available == nil and store.update.state == "idle" and store.update.message == "" and store.update.restartRequired == false)
		store:applyUpdate({ current = "0.6.0", state = "downloading" })
		assert(store.update.state == "downloading" and store.update.current == "0.6.0")
		store:applyUpdate("0.6.1")
		assert(store.update.state == "idle" and store.update.current == "")
		store:applyUpdate({ current = "0.6.0", state = "available", available = "0.6.1" })
		store:applyUpdate(nil)
		assert(store.update.state == "idle" and store.update.current == "" and store.update.available == nil)
	end)
	test("Deconectarea și restartul daemon-ului resetează starea actualizării", function()
		local store = ready()
		store:applyUpdate({ current = "0.6.0", available = "0.6.1", state = "installed", message = "gata", restart_required = true })
		assert(store.update.restartRequired and store.update.available == "0.6.1")
		store:resetUpdate()
		assert(store.update.current == "" and store.update.available == nil and store.update.state == "idle" and store.update.message == "" and store.update.restartRequired == false)
		store:applyUpdate({ current = "0.6.0", available = "0.6.1", state = "available", restart_required = false })
		assert(not store:remoteChanged("bridge-A") and store.update.available == "0.6.1")
		assert(store:remoteChanged("bridge-B"))
		assert(store.update.current == "" and store.update.available == nil and store.update.state == "idle")
	end)
	test("Sesiunea remote pierdută revine de la ultimul cursor", function()
		local store = ready()
		store:syncBoard(board({ remoteSession("job-R") }))
		local r = store.windows[Store.terminalWindowId("job-R")]
		store:applyEvents({ job_id = "job-R", events = { { seq = 4, type = "text", text = "Pas." } } })
		store:syncBoard(board({ remoteSession("job-R", "lost") }))
		assert(r.job == nil and r.state == "lost" and string.find(r.messages[#r.messages].text, "hub", 1, true))
		store:syncBoard(board({ remoteSession("job-R", "running") }))
		assert(r.job and r.job.after == 4 and r.state == "running")
	end)
	test("Inventarul proiectului: stare validată, câmpuri îmbinate, valori invalide implicite", function()
		local store = Store.new()
		local project = store.project
		assert(project.state == "idle" and project.count == 0 and project.truncated == false and project.snapshotId == nil and project.sentAt == nil and project.message == "")
		local emitted = 0
		store:subscribe(function(id) emitted += 1; assert(id == nil) end)
		store:setProject("sending", { snapshotId = "snap-1", count = 1234.7, truncated = true })
		assert(emitted == 1 and store.project ~= project and store.project.state == "sending" and store.project.snapshotId == "snap-1")
		assert(store.project.count == 1234 and store.project.truncated and store.project.sentAt == nil and store.project.message == "")
		store:setProject("sent", { sentAt = 1726000000 })
		assert(store.project.state == "sent" and store.project.sentAt == 1726000000 and store.project.count == 1234 and store.project.truncated and store.project.snapshotId == "snap-1")
		store:setProject("error", { message = "Daemon-ul\nnu a răspuns " .. string.rep("x", 300) })
		assert(store.project.state == "error" and #store.project.message == 200 and not string.find(store.project.message, "\n", 1, true))
		assert(string.sub(store.project.message, 1, 23) == "Daemon-ul nu a răspuns" and store.project.count == 1234 and store.project.snapshotId == "snap-1")
		store:setProject("bogus", { count = "nu", truncated = "da", snapshotId = 7, sentAt = "ieri", message = { "nu" } })
		assert(store.project.state == "idle" and store.project.count == 0 and store.project.truncated == false and store.project.snapshotId == nil)
		assert(store.project.sentAt == nil and store.project.message == "")
		store:setProject("sent", { snapshotId = string.rep("s", 80), count = -3 })
		assert(#store.project.snapshotId == 64 and store.project.count == 0)
		assert(emitted == 5 and store:setProject("sending").state == "sending")
	end)
	test("Deconectarea și restartul daemon-ului resetează inventarul", function()
		local store = ready()
		store:setProject("sent", { snapshotId = "snap-1", count = 10, sentAt = 5, truncated = true })
		store:resetProject()
		assert(store.project.state == "idle" and store.project.snapshotId == nil and store.project.count == 0 and store.project.truncated == false)
		store:setProject("sent", { snapshotId = "snap-2", count = 3, sentAt = 6 })
		assert(not store:remoteChanged("bridge-A") and store.project.snapshotId == "snap-2")
		assert(store:remoteChanged("bridge-B") and store.project.state == "idle" and store.project.snapshotId == nil and store.project.count == 0)
	end)
	test("Handoff: round-trip fără token, cu ferestre studio, terminal și remote, identitate, workspace și hub", function()
		local store = ready()
		store.defaultProvider = "codex"
		store:applyStatus(status())
		local a = window(store, "A")
		send(store, "A", "job-A", "session-A")
		store:applyEvents({ job_id = "job-A", state = "running", events = {
			{ seq = 1, type = "text", text = "Primul " },
			{ seq = 2, type = "approval", approval_id = "ap-1", tool = "execute_luau", arguments = { code = "return 1", scope = { "Workspace" } } } } })
		store:setDraft("A", "Draft în lucru ăîșț")
		store:setVisible("A", false)
		store:syncBoard(board({ terminalSession("job-T"), remoteSession("job-R"), ownSession("job-A", { claims = { "Workspace.Map" } }) }))
		window(store, "B")
		store:setDraft("B", "Doar draft")
		store:setViewWorkspace("game:1")
		local data = store:serialize()
		local function scan(value, seen)
			if type(value) ~= "table" or seen[value] then return end
			seen[value] = true
			for key, child in pairs(value) do
				assert(type(child) ~= "function" and not (type(key) == "string" and string.find(string.lower(key), "token", 1, true)), tostring(key))
				scan(child, seen)
			end
		end
		scan(data, {})
		assert(data.version == Store.HANDOFF_VERSION and #data.windows == 4 and data.developer == "ellob" and data.defaultProvider == "codex" and data.bridgeId == "bridge-A")
		assert(data.defaultStudioId == "studio-A" and #data.studios == 2 and #data.dismissedTerminal == 0 and data.machine == "PC-ELLOB" and data.viewWorkspace == "game:1")
		assert(data.identity ~= store.identity and data.identity.userId == 12345 and data.workspace ~= store.workspace and data.workspace.key == "game:987654" and data.hub ~= store.hub and data.hub.status == "approved")
		assert(data.members == nil and data.workspaces == nil and data.view == nil)
		local restored = Store.deserialize(data)
		assert(restored ~= store and #restored.order == 4 and restored.order[1] == "A" and restored.order[4] == "B")
		assert(not restored.connected and not restored.connecting and not restored.compatible and restored.bridgeId == "bridge-A" and #restored.listeners == 0)
		assert(restored.developer == "ellob" and restored.defaultProvider == "codex" and restored.defaultStudioId == "studio-A" and restored:studio("studio-B").name == "Scena B")
		assert(restored.machine == "PC-ELLOB" and restored.identity.userId == 12345 and restored.identity.name == "ellob" and restored.identity.avatar == store.identity.avatar)
		assert(restored.workspace.key == "game:987654" and restored.workspace.name == "Ball" and restored.workspace.placeId == 1291603 and restored.workspace.gameId == 987654 and restored.workspace.creatorId == 555 and restored.workspace.creatorType == "User")
		assert(restored.hub.status == "approved" and restored.hub.url == store.hub.url and restored.hub.hubId == "hub-1" and restored.hub.deviceId == "dev-ellob-0123456789" and restored.hub.panelUrl == store.hub.panelUrl and restored.hub.enrollment == "approve" and restored.hub.lastSync == 5)
		assert(restored.viewWorkspace == "game:1" and restored.view == nil and #restored.members == 0 and #restored.workspaces == 0 and restored:workspaceLabel() == "game:1")
		local ra = restored.windows.A
		assert(ra.kind == "studio" and ra.provider == "claude" and ra.name == a.name and ra.studioId == "studio-A" and ra.studioName == "Scena A")
		assert(ra.mine and not ra.remote and ra.owner.name == "ellob" and ra.owner.deviceId == "dev-ellob-0123456789" and ra.owner.userId == 12345 and ra.workspace == "game:987654")
		assert(ra.sessionId == "session-A" and ra.job and ra.job.id == "job-A" and ra.job.after == 2 and ra.job.state == "running" and ra.state == "running" and ra.locked)
		assert(ra.draft == "Draft în lucru ăîșț" and ra.visible == false and #ra.messages == #a.messages and ra.messages[3].kind == "assistant" and ra.claims[1] == "Workspace.Map")
		assert(#ra.approvals == 1 and ra.approvals[1].id == "ap-1" and ra.approvals[1].jobId == "job-A" and ra.approvals[1].arguments.scope[1] == "Workspace" and not ra.approvals[1].blocked)
		assert(ra.approvals[1].arguments ~= a.approvals[1].arguments and ra.messages ~= a.messages and ra.messages[1] ~= a.messages[1] and ra.job.message == nil)
		local rt = restored.windows[Store.terminalWindowId("job-T")]
		assert(rt and rt.kind == "terminal" and rt.terminalJobId == "job-T" and rt.provider == "codex" and rt.developer == "ellob" and not rt.remote and rt.mine and rt.locked)
		assert(rt.job and rt.job.id == "job-T" and rt.state == "running" and rt.studioId == "studio-B" and rt.claims[1] == "Workspace.Map.Zone3" and rt.cwd == "C:\\proiect" and rt.workspace == "game:987654")
		local rr = restored.windows[Store.terminalWindowId("job-R")]
		assert(rr and rr.remote and not rr.mine and rr.sessionKind == "studio" and rr.developer == "ana" and rr.machine == "PC-ANA" and rr.job.id == "job-R")
		assert(rr.owner.name == "ana" and rr.owner.deviceId == "dev-ana" and rr.owner.userId == 777 and rr.owner.machine == "PC-ANA" and rr.workspace == "game:987654")
		local rb = restored.windows.B
		assert(rb.state == "draft" and rb.draft == "Doar draft" and rb.job == nil and not rb.locked and rb.visible and #rb.messages == 1 and rb.owner.name == "ellob")
		assert(#restored:jobs() == 3 and restored:findJob("job-R") == rr)
		local empty = Store.new():serialize()
		assert(#empty.windows == 0 and #empty.studios == 0 and empty.defaultProvider == "claude" and empty.developer == nil and empty.bridgeId == nil)
		assert(empty.identity == nil and empty.workspace == nil and empty.hub.status == "connecting" and empty.viewWorkspace == nil and empty.machine == nil)
	end)
	test("Handoff: evenimentele continuă de la cursor și răspunsul asistentului nu se rupe", function()
		local store = ready()
		window(store, "A")
		send(store, "A", "job-A", "session-A")
		store:applyEvents({ job_id = "job-A", state = "running", events = { { seq = 1, type = "text", text = "Încep" } } })
		local restored = Store.deserialize(store:serialize())
		local a = restored.windows.A
		assert(a.job.after == 1 and a.job.message == a.messages[#a.messages] and a.job.message.kind == "assistant")
		assert(not restored:remoteChanged("bridge-A"))
		restored:applyEvents({ job_id = "job-A", state = "running", events = { { seq = 1, type = "text", text = "DUPLICAT" }, { seq = 2, type = "text", text = " și continui." } } })
		assert(a.job.after == 2 and a.job.message.text == "Încep și continui." and #a.messages == 3)
		restored:applyEvents({ job_id = "job-A", state = "completed", events = {} })
		assert(a.job == nil and a.state == "completed" and a.cursor == 2)
		-- Restartul daemon-ului între două bundle-uri este detectat din bridgeId-ul păstrat.
		local again = Store.deserialize(store:serialize())
		assert(again:remoteChanged("bridge-B") and again.windows.A.job == nil and again.windows.A.state == "draft")
	end)
	test("Handoff: cererea în zbor devine neconfirmată, cu același ID și mesaj", function()
		local store = ready()
		local a = window(store, "A")
		store:setDraft("A", "Mesaj trimis")
		assert(store:beginRequest("A", "req-1", { selection = { "Workspace.Part" } }))
		store:setDraft("A", "Draft următor")
		local restored = Store.deserialize(store:serialize())
		local ra = restored.windows.A
		assert(ra.request and ra.request.id == "req-1" and ra.request.state == "uncertain" and ra.state == "uncertain" and ra.locked)
		assert(ra.request.payload.prompt == "Mesaj trimis" and ra.request.payload.client_request_id == "req-1" and ra.request.payload.context.selection[1] == "Workspace.Part")
		assert(ra.request.bridgeId == "bridge-A" and ra.request.message == ra.messages[2] and ra.messages[2].author == "Tu · neconfirmat" and ra.messages[2].text == "Mesaj trimis")
		assert(ra.draft == "Draft următor" and not restored:canSend(ra) and a.request.message.author == "Tu · trimitere")
		assert(restored:acceptRequest("A", "req-1", { job_id = "job-1", session_id = "s-1", state = "queued" }))
		assert(ra.job.id == "job-1" and ra.request == nil and ra.messages[2].author == "Tu" and ra.draft == "Draft următor")
	end)
	test("Handoff: câmpurile lipsă revin la implicit și nimic nu aruncă eroare", function()
		for _, data in ipairs({ "text", 42, true, {}, { windows = "nu", studios = 3, dismissedTerminal = "x", defaultProvider = 1, developer = {}, identity = "eu", workspace = 5, hub = true, viewWorkspace = 9, machine = {} },
			{ identity = {}, workspace = {}, hub = {}, viewWorkspace = "" },
			{ windows = { 1, "x", {}, { id = "" }, { id = 5 }, { id = "terminal:", kind = "terminal" }, { id = "t", kind = "terminal" }, { id = string.rep("i", 201) } } } }) do
			local store = Store.deserialize(data)
			assert(#store.order == 0 and next(store.windows) == nil and store.defaultProvider == "claude" and store.developer == nil and store.bridgeId == nil and store.defaultStudioId == nil)
			assert(not store.connected and store.hub.status == "connecting" and store.workspace == nil and store.viewWorkspace == nil and store.machine == nil)
			assert(store.update.state == "idle" and store.project.state == "idle" and #store.claims == 0 and #store.members == 0 and #store.workspaces == 0 and store.team == nil)
			assert(store.identity == nil or (store.identity.userId == 0 and store.identity.name == "Studio"))
		end
		assert(#Store.deserialize(nil).order == 0 and #Store.deserialize().order == 0)
		local minimal = Store.deserialize({ windows = { { id = "X" }, { id = "terminal:job-Z", kind = "terminal" } } })
		local x = minimal.windows.X
		assert(#minimal.order == 2 and x.kind == "studio" and x.provider == "claude" and x.name == "Sesiune 1" and x.state == "draft" and x.draft == "" and x.visible and not x.locked)
		assert(#x.messages == 0 and #x.approvals == 0 and #x.claims == 0 and x.cursor == 0 and x.job == nil and x.request == nil and x.restoreVersion == 0 and x.sessionKind == "studio")
		assert(x.mine and not x.remote and x.owner == nil and x.workspace == nil)
		local z = minimal.windows["terminal:job-Z"]
		assert(z.kind == "terminal" and z.terminalJobId == "job-Z" and z.name == "Terminal" and z.state == "completed" and z.job == nil and z.locked)
		assert(minimal:canSend(x) == false)
		minimal:syncBoard(board({ terminalSession("job-Z") }))
		assert(z.job and z.job.id == "job-Z" and z.job.after == 0 and z.state == "running" and z.name == "Terminal B")
	end)
	test("Handoff 0.8: snapshotul fără identitate, hub sau owner se deserializează cu implicite", function()
		-- Forma exactă a lui serialize() din 0.8: `team` nu era în handoff; ferestrele aveau memberId/developer/machine.
		local old = { version = 1, developer = "ellob", bridgeId = "bridge-A", defaultProvider = "claude", defaultStudioId = "studio-A",
			studios = { { id = "studio-A", name = "Scena A" } }, dismissedTerminal = { "job-U" },
			windows = { { id = "A", kind = "studio", sessionKind = "studio", name = "Sesiune 1", provider = "claude", developer = nil, remote = false, machine = nil, memberId = nil,
				studioId = "studio-A", studioName = "Scena A", cursor = 0, sessionId = "session-A", locked = true, state = "running",
				job = { id = "job-A", after = 3, state = "running" }, draft = "", draftRevision = 1, visible = true, messages = { { author = "Tu", text = "Cerere", kind = "user", clipped = false } },
				approvals = { { id = "ap-1", jobId = "job-A", tool = "execute_luau", arguments = { code = "1" }, blocked = false, inactive = false } }, claims = { "Workspace" } },
				{ id = "terminal:job-R", kind = "terminal", sessionKind = "studio", name = "Sesiune Ana", provider = "claude", developer = "ana", remote = true, machine = "PC-ANA", memberId = "m-ana",
				studioId = "studio-ana", cursor = 4, terminalJobId = "job-R", locked = true, state = "running", job = { id = "job-R", after = 4, state = "running" },
				draft = "", draftRevision = 0, visible = true, messages = {}, approvals = {}, claims = {} } } }
		local store = Store.deserialize(old)
		assert(#store.order == 2 and store.identity == nil and store.workspace == nil and store.hub.status == "connecting" and store.machine == nil and store.viewWorkspace == nil)
		local a = store.windows.A
		assert(a.mine and not a.remote and a.owner == nil and a.workspace == nil and a.job.id == "job-A" and a.approvals[1].inspectable == false and a.claims[1] == "Workspace")
		local r = store.windows["terminal:job-R"]
		assert(r.remote and not r.mine and r.owner.name == "ana" and r.owner.deviceId == "m-ana" and r.owner.machine == "PC-ANA" and r.owner.userId == 0 and r.workspace == nil and r.job.after == 4)
		assert(store.dismissedTerminal["job-U"] and store:studio("studio-A") and store.defaultStudioId == "studio-A")
		store.connected, store.compatible = true, true
		assert(store:applyStatus(status()) and store.identity.name == "ellob" and a.owner.name == "ellob" and r.owner.name == "ana")
	end)
	test("Handoff: datele invalide sunt tăiate, sărite sau aduse la implicit", function()
		local windows = {}
		for index = 1, Store.MAX_WINDOWS + 5 do
			table.insert(windows, { id = "W" .. index, provider = "gemini", kind = "altceva", name = "  ", visible = "da", cursor = -4, draft = 7, claims = { "Ok", 3, "" }, owner = "eu", workspace = 12 })
		end
		local messages = {}
		for index = 1, Store.MAX_MESSAGES + 30 do table.insert(messages, { text = "m" .. index, kind = "text" }) end
		table.insert(messages, { text = 5 }); table.insert(messages, "x")
		windows[1].messages = messages
		windows[1].job = { id = "job-1", after = 3.9, state = "running" }
		windows[1].approvals = { { id = "ap-1", jobId = "job-1", tool = "execute_luau", arguments = { code = "1" } }, { id = "ap-2", jobId = "job-ALT", tool = "x", arguments = {} },
			{ id = "", jobId = "job-1" }, "nu", { id = "ap-3", jobId = "job-1", tool = "", arguments = "nu" } }
		windows[2].job = { id = "job-1", after = 0, state = "running" }
		windows[3].job = { id = "job-3", after = 2, state = "completed" }
		windows[3].state = "running"
		windows[4].id = "W1"
		windows[5].job = { id = "job-5", after = "nu", state = "bogus" }
		windows[5].state = "sending"
		windows[6].state = "queued"
		windows[7].request = { id = "r7", payload = { prompt = "text" }, messageIndex = 99, bridgeId = 12, draftRevision = "x" }
		windows[8].request = { id = "r8", payload = "nu" }
		windows[9].job = { id = "job-9", after = 1, state = "running" }
		windows[9].request = { id = "r9", payload = { prompt = "text" } }
		windows[10].kind, windows[10].id, windows[10].job = "terminal", "terminal:job-10", { id = "job-ALT", after = 1, state = "running" }
		windows[11].owner = { name = string.rep("n", 100), deviceId = 5, userId = -3, machine = "PC" }
		local store = Store.deserialize({ windows = windows, defaultProvider = "gemini", developer = string.rep("d", 100), bridgeId = "",
			studios = { { id = "s1", name = "S1" }, { id = "s1" }, "x" }, defaultStudioId = "s9", dismissedTerminal = { "job-D", 4, "" },
			identity = { userId = "x", name = 4 }, workspace = { key = 7 }, hub = { status = "approved", url = 9, deviceId = "" }, viewWorkspace = string.rep("v", 65) })
		assert(#store.order == Store.MAX_WINDOWS and store.windows.W1 and store.windows.W2 == nil and store.windows.W3 and store.windows.W34 and not store.windows.W35)
		assert(store.defaultProvider == "claude" and #store.developer == 60 and store.bridgeId == nil and #store.studios == 1 and store.defaultStudioId == "s1")
		assert(store.dismissedTerminal["job-D"] and next(store.dismissedTerminal, "job-D") == nil)
		assert(store.identity.userId == 0 and store.identity.name == "Studio" and store.workspace == nil and store.hub.status == "approved" and store.hub.url == nil and store.hub.deviceId == nil and store.hub.panelUrl == nil and store.viewWorkspace == nil)
		local w1 = store.windows.W1
		assert(w1.provider == "claude" and w1.kind == "studio" and w1.name == "Sesiune 1" and w1.visible and w1.cursor == 0 and w1.draft == "" and #w1.claims == 1 and w1.owner == nil and w1.workspace == nil)
		assert(#w1.messages == Store.MAX_MESSAGES and w1.messages[1].text == "m31" and w1.messages[Store.MAX_MESSAGES].text == "m150" and w1.messages[1].author == "Status")
		assert(w1.job.id == "job-1" and w1.job.after == 3 and w1.state == "running" and #w1.approvals == 2 and w1.approvals[1].id == "ap-1" and not w1.approvals[1].blocked and w1.approvals[2].blocked)
		assert(store.windows.W3.job == nil and store.windows.W3.state == "completed")
		local w5 = store.windows.W5
		assert(w5.job.id == "job-5" and w5.job.after == 0 and w5.job.state == "running" and w5.state == "running")
		assert(store.windows.W6.state == "draft")
		local w7 = store.windows.W7
		assert(w7.request and w7.request.state == "uncertain" and w7.request.message == nil and w7.request.bridgeId == nil and w7.request.draftRevision == -1 and w7.state == "uncertain")
		assert(store.windows.W8.request == nil and store.windows.W8.state == "draft")
		assert(store.windows.W9.job.id == "job-9" and store.windows.W9.request == nil)
		local w10 = store.windows["terminal:job-10"]
		assert(w10.kind == "terminal" and w10.terminalJobId == "job-10" and w10.job == nil and w10.state == "completed")
		local w11 = store.windows.W11
		assert(#w11.owner.name == 60 and w11.owner.deviceId == nil and w11.owner.userId == 0 and w11.owner.machine == "PC")
		assert(#store:jobs() == 3)
	end)
	test("Handoff: ferestrele terminal închise nu reapar, iar cele active se reiau din board", function()
		local store = ready()
		store:syncBoard(board({ terminalSession("job-T"), terminalSession("job-U", "completed") }))
		local idU = Store.terminalWindowId("job-U")
		assert(store:dismissTerminal(idU))
		local restored = Store.deserialize(store:serialize())
		assert(restored.dismissedTerminal["job-U"] and restored.windows[idU] == nil)
		local t = restored.windows[Store.terminalWindowId("job-T")]
		assert(t.job and t.job.id == "job-T" and #restored:jobs() == 1)
		restored:syncBoard(board({ terminalSession("job-T", "completed"), terminalSession("job-U", "completed") }))
		assert(restored.windows[idU] == nil and t.job == nil and t.state == "completed")
	end)
	test("Tabla fără hub aprobat: prezenți goi, hub offline, claims locale fără workspace", function()
		local store = ready()
		store:syncBoard(board({ terminalSession("job-T") }, {
			hub = { url = "https://hub.example", status = "offline", device_id = "dev-ellob-0123456789", error = "timeout după 5 s" },
			members = {}, workspaces = {}, claims = { { path = "Workspace.Map", job_id = "job-T", developer = "ellob", since = 1, mine = true } } }))
		assert(store.hub.status == "offline" and store.hub.error == "timeout după 5 s" and store.hub.panelUrl == "https://hub.example/panel" and store.hub.lastSync == nil)
		assert(#store.members == 0 and #store.workspaces == 0 and #store.claims == 1 and store.claims[1].workspace == nil and store:ownsClaim(store.claims[1]))
		local t = store.windows[Store.terminalWindowId("job-T")]
		assert(t.mine and t.owner.deviceId == "dev-ellob-0123456789" and t.workspace == "game:987654")
		local text, role = store:hubStatusText()
		assert(role == "danger" and string.find(text, "Hub offline: timeout după 5 s. ", 1, true))
	end)
	test("`started` este momentul real al sesiunii și supraviețuiește handoff-ului", function()
		local store = ready()
		local now = os.time()
		-- Fereastra proprie primește momentul creării, ca „de X min” să nu repornească la metamorfoză.
		local a = window(store, "A")
		assert(type(a.started) == "number" and a.started >= now and a.started <= now + 5)
		-- Sesiunile din terminal și cele remote poartă `started` din `/v1/board`.
		store:syncBoard(board({ terminalSession("job-T", nil, { started = 1700000000 }),
			terminalSession("job-R", nil, { started = 1700000500, device_id = "dev-ana", mine = false, remote = true, developer = "ana" }) }))
		local t = store.windows[Store.terminalWindowId("job-T")]
		local r = store.windows[Store.terminalWindowId("job-R")]
		assert(t.started == 1700000000 and r.started == 1700000500 and r.remote)
		-- O tablă fără `started` nu șterge valoarea deja cunoscută.
		store:syncBoard(board({ terminalSession("job-T", nil, { started = nil }) }))
		assert(t.started == 1700000000)
		local restored = Store.deserialize(store:serialize())
		assert(restored.windows["A"].started == a.started and restored.windows[Store.terminalWindowId("job-T")].started == 1700000000)
		-- Handoff-ul 0.8 nu are câmpul: rămâne nil, iar view-ul cade pe momentul primei afișări.
		assert(Store.deserialize({ version = Store.HANDOFF_VERSION, windows = { { id = "B", kind = "studio", provider = "claude", name = "Sesiune B" } } }).windows["B"].started == nil)
	end)
	return passed
end
