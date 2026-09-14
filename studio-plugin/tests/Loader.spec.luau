-- Teste pure pentru logica de decizie a loader-ului. Runnerul injectează tabelul Loader (scriptul îl întoarce în afara Studio); Instance este simulat cu tabele.
return function(Loader)
	local passed = 0
	local function test(name, run)
		local ok, message = pcall(run)
		assert(ok, name .. ": " .. tostring(message))
		passed += 1
	end
	-- Instanță falsă: Parent adaugă în lista copiilor părintelui; `Source` poate fi făcut să eșueze (permisiune refuzată).
	local function fake(className, options)
		local instance = { ClassName = className, Name = "", children = {} }
		function instance:GetChildren() return self.children end
		function instance:FindFirstChild(name)
			for _, child in ipairs(self.children) do if child.Name == name then return child end end
			return nil
		end
		return setmetatable(instance, { __newindex = function(self, key, value)
			if key == "Source" and options and options.refuseSource then error("Script injection refuzat") end
			if key == "Parent" and value then table.insert(value.children, self) end
			rawset(self, key, value)
		end })
	end
	local function create(className) return fake(className) end
	local function module(name, source)
		local instance = fake("ModuleScript")
		instance.Name, instance.Source = name, source
		return instance
	end
	-- Scriptul loader fals cu (sau fără) un copil `LocalToken` de clasa și valoarea date.
	local function loaderScript(className, value)
		local container = fake("Script")
		if className then
			local child = fake(className)
			child.Name, child.Value = "LocalToken", value
			child.Parent = container
		end
		return container
	end
	local function bundle(fields)
		local result = { ok = true, version = "1.0.0", revision = "abc123", entry = "Main", modules = { Main = "return {}", Theme = "return 1" } }
		for key, value in pairs(fields or {}) do result[key] = value end
		return result
	end
	local TOKEN = "tok_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_abc"

	test("validToken: 8–512 caractere fără spații, caractere de control sau backslash, diferit de placeholder", function()
		assert(Loader.validToken("abcdefgh") == true and Loader.validToken(string.rep("a", 512)) == true and Loader.validToken("A-z_09.~") == true and Loader.validToken(TOKEN) == true)
		assert(Loader.validToken("abcdefg") == false and Loader.validToken(string.rep("a", 513)) == false and Loader.validToken("") == false)
		assert(Loader.validToken("abcd efgh") == false and Loader.validToken("abcd\tefgh") == false and Loader.validToken("abcdefgh\n") == false and Loader.validToken("abcd\\efgh") == false)
		assert(Loader.validToken(Loader.TOKEN_PLACEHOLDER) == false and Loader.validToken(nil) == false and Loader.validToken(12345678) == false and Loader.validToken({}) == false)
	end)
	test("localToken: StringValue LocalToken valid → codul; placeholder, absent, altă clasă sau valoare invalidă → nil", function()
		assert(Loader.localToken(loaderScript("StringValue", TOKEN)) == TOKEN)
		assert(Loader.localToken(loaderScript("StringValue", Loader.TOKEN_PLACEHOLDER)) == nil)
		assert(Loader.localToken(loaderScript(nil)) == nil)
		assert(Loader.localToken(loaderScript("IntValue", 12345678)) == nil and Loader.localToken(loaderScript("StringValue", 12345678)) == nil)
		assert(Loader.localToken(loaderScript("StringValue", "")) == nil and Loader.localToken(loaderScript("StringValue", "scurt")) == nil)
		assert(Loader.localToken(loaderScript("StringValue", "cu spatiu aici")) == nil and Loader.localToken(loaderScript("StringValue", "cod\\invalid")) == nil)
		local other = loaderScript(nil)
		local sibling = fake("StringValue")
		sibling.Name, sibling.Value = "AltValue", TOKEN
		sibling.Parent = other
		assert(Loader.localToken(other) == nil)
		assert(Loader.localToken(nil) == nil and Loader.localToken("text") == nil)
		assert(Loader.localToken(setmetatable({}, { __index = function() error("inaccesibil") end })) == nil)
	end)
	test("resolveToken: codul livrat are prioritate, apoi setarea salvată, altfel gol fără sursă", function()
		local token, source = Loader.resolveToken(TOKEN, "saved-token-1")
		assert(token == TOKEN and source == "loader")
		token, source = Loader.resolveToken(nil, "saved-token-1")
		assert(token == "saved-token-1" and source == "settings")
		token, source = Loader.resolveToken(Loader.TOKEN_PLACEHOLDER, "saved-token-1")
		assert(token == "saved-token-1" and source == "settings")
		token, source = Loader.resolveToken("scurt", "cu spatiu")
		assert(token == "" and source == nil)
		token, source = Loader.resolveToken(nil, nil)
		assert(token == "" and source == nil)
		token, source = Loader.resolveToken("", "")
		assert(token == "" and source == nil)
	end)
	test("shouldSwap: fără status, fără plugin_bundle sau cu revizie invalidă nu schimbă nimic", function()
		assert(Loader.shouldSwap(nil, nil) == false and Loader.shouldSwap("a", "text") == false)
		assert(Loader.shouldSwap(nil, { ok = true }) == false and Loader.shouldSwap(nil, { plugin_bundle = "x" }) == false)
		assert(Loader.shouldSwap(nil, { plugin_bundle = { revision = "" } }) == false)
		assert(Loader.shouldSwap(nil, { plugin_bundle = { revision = 12 } }) == false)
		assert(Loader.shouldSwap(nil, { plugin_bundle = { revision = "a b" } }) == false)
		assert(Loader.shouldSwap(nil, { plugin_bundle = { revision = "../x" } }) == false)
		assert(Loader.shouldSwap(nil, { plugin_bundle = { revision = string.rep("a", 65) } }) == false)
	end)
	test("shouldSwap: revizia diferită cere metamorfoză, cea curentă sau cea eșuată nu", function()
		local status = { ok = true, plugin_bundle = { version = "1.0.0", revision = "rev-2", source = "app" } }
		local swap, revision = Loader.shouldSwap(nil, status)
		assert(swap == true and revision == "rev-2")
		swap, revision = Loader.shouldSwap("rev-1", status)
		assert(swap == true and revision == "rev-2")
		assert(Loader.shouldSwap("rev-2", status) == false)
		assert(Loader.shouldSwap("rev-1", status, "rev-2") == false)
		assert(Loader.shouldSwap("rev-1", status, "rev-9") == true)
	end)
	test("validateBundle acceptă bundle-ul contractului și numește intrarea", function()
		local ok, entry = Loader.validateBundle(bundle())
		assert(ok == true and entry == "Main")
		ok, entry = Loader.validateBundle({ ok = true, revision = "r1", modules = { Main = "return {}" } })
		assert(ok == true and entry == "Main")
		ok, entry = Loader.validateBundle(bundle({ entry = "App", modules = { App = "return {}" } }))
		assert(ok == true and entry == "App")
	end)
	test("validateBundle respinge răspunsuri lipsă, revizii, nume sau surse invalide", function()
		assert(Loader.validateBundle(nil) == false and Loader.validateBundle("x") == false)
		assert(Loader.validateBundle(bundle({ revision = "" })) == false and Loader.validateBundle(bundle({ revision = "a/b" })) == false)
		assert(Loader.validateBundle({ ok = true, revision = "r1" }) == false and Loader.validateBundle(bundle({ modules = {} })) == false)
		assert(Loader.validateBundle(bundle({ modules = "text" })) == false)
		assert(Loader.validateBundle(bundle({ entry = "Lipsă" })) == false and Loader.validateBundle(bundle({ entry = 3 })) == false)
		assert(Loader.validateBundle(bundle({ modules = { Main = "return {}", ["../Evil"] = "x" } })) == false)
		assert(Loader.validateBundle(bundle({ modules = { Main = "return {}", ["Cu spațiu"] = "x" } })) == false)
		assert(Loader.validateBundle(bundle({ modules = { Main = "return {}", Theme = 7 } })) == false)
		assert(Loader.validateBundle(bundle({ modules = { Main = "   \n" } })) == false)
		assert(Loader.validateBundle(bundle({ modules = { Main = string.rep("x", Loader.MAX_SOURCE + 1) } })) == false)
		local many = { Main = "return {}" }
		for index = 1, Loader.MAX_MODULES do many["M" .. index] = "return " .. index end
		assert(Loader.validateBundle(bundle({ modules = many })) == false)
		local _, reason = Loader.validateBundle(bundle({ modules = { Theme = "return 1" } }))
		assert(string.find(reason, "Main", 1, true))
	end)
	test("buildFolder: folder App-<revizie> cu ModuleScript-uri în ordine, Source și părinte", function()
		local parent = fake("Folder")
		local folder = Loader.buildFolder(bundle(), parent, create)
		assert(folder.ClassName == "Folder" and folder.Name == "App-abc123" and folder.Parent == parent and parent.children[1] == folder)
		assert(#folder.children == 2 and folder.children[1].Name == "Main" and folder.children[2].Name == "Theme")
		assert(folder.children[1].ClassName == "ModuleScript" and folder.children[1].Source == "return {}" and folder.children[2].Source == "return 1")
		assert(folder:FindFirstChild("Main") == folder.children[1] and folder.children[1].Parent == folder)
	end)
	test("buildFolder: refuzul injecției de scripturi eșuează înainte de a atinge părintele", function()
		local parent = fake("Folder")
		local function refusing(className) return fake(className, { refuseSource = true }) end
		local ok, message = pcall(Loader.buildFolder, bundle(), parent, refusing)
		assert(not ok and string.find(tostring(message), "injection", 1, true) and #parent.children == 0)
	end)
	test("sameSources: același conținut (și cu CRLF) adoptă revizia; diferențe, module în plus sau lipsă nu", function()
		local folder = fake("Folder")
		module("Main", "return {}").Parent = folder
		module("Theme", "return 1").Parent = folder
		assert(Loader.sameSources(bundle(), folder) == true)
		assert(Loader.sameSources(bundle({ modules = { Main = "return {}\r\n", Theme = "return 1\n" } }), folder) == false)
		assert(Loader.sameSources(bundle({ modules = { Main = "return {}", Theme = "return 1", Extra = "return 2" } }), folder) == false)
		assert(Loader.sameSources(bundle({ modules = { Main = "return {}" } }), folder) == false)
		assert(Loader.sameSources(bundle({ modules = { Main = "return nil", Theme = "return 1" } }), folder) == false)
		local crlf = fake("Folder")
		module("Main", "return {}\r\n").Parent = crlf
		module("Theme", "return 1\r\n").Parent = crlf
		assert(Loader.sameSources(bundle({ modules = { Main = "return {}\n", Theme = "return 1\n" } }), crlf) == true)
		local locked = setmetatable({}, { __index = function() error("Source inaccesibil") end })
		assert(Loader.sameSources(bundle(), locked) == false)
		local note = fake("Folder")
		module("Main", "return {}").Parent = note
		module("Theme", "return 1").Parent = note
		fake("StringValue").Parent = note
		assert(Loader.sameSources(bundle(), note) == true)
	end)
	test("retryHandoff: repornirea pe bundle-ul anterior duce motivul eșecului în handoff", function()
		local snapshot = { controller = { token = TOKEN, source = "loader" }, store = { windows = 2 } }
		local retry = Loader.retryHandoff(snapshot, "rev-9", "modulul Main lipsește din App-rev-9")
		assert(retry.bundleError == "rev-9: modulul Main lipsește din App-rev-9")
		-- Istoricul aplicației trece mai departe, iar snapshotul original rămâne neatins (clonă, nu aceeași masă).
		assert(retry.store == snapshot.store and retry.controller == snapshot.controller and retry ~= snapshot)
		assert(snapshot.bundleError == nil)
		-- Fără snapshot (aplicația nu rula sau snapshotul a eșuat) handoff-ul poartă doar motivul.
		local bare = Loader.retryHandoff(nil, "rev-9", "eroare")
		assert(bare.bundleError == "rev-9: eroare" and next(bare) == "bundleError")
		assert(Loader.retryHandoff("text", "rev-1", "eroare").bundleError == "rev-1: eroare")
	end)
	test("normalize unifică terminatorii de linie", function()
		assert(Loader.normalize("a\r\nb\rc\n") == "a\nb\nc\n" and Loader.normalize(5) == "5")
	end)
	test("Constantele loader-ului urmează contractul", function()
		assert(Loader.VERSION == "1.1.0" and Loader.BASE == "http://127.0.0.1:34871" and Loader.TOKEN_SETTING == "StudioHarnessBridgeToken" and Loader.CHECK_INTERVAL == 5)
		assert(Loader.TOKEN_VALUE == "LocalToken" and Loader.TOKEN_PLACEHOLDER == "STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER")
	end)
	return passed
end
