-- Teste pure. Runnerul injectează modulul ProjectScanner; DataModel-ul este simulat cu tabele (fără `game`, fără HttpService).
return function(Scanner)
	local passed = 0
	local function test(name, run)
		local ok, message = pcall(run)
		assert(ok, name .. ": " .. tostring(message))
		passed += 1
	end
	local function fake(className, name, children, extra)
		local instance = { ClassName = className, Name = name, children = children or {}, attributes = {} }
		for key, value in pairs(extra or {}) do instance[key] = value end
		function instance:GetChildren() return self.children end
		function instance:GetDescendants()
			local result = {}
			for _, child in ipairs(self.children) do
				table.insert(result, child)
				for _, descendant in ipairs(child:GetDescendants()) do table.insert(result, descendant) end
			end
			return result
		end
		function instance:IsA(class) return class == self.ClassName or (class == "LuaSourceContainer" and self.source == true) end
		function instance:GetAttributes() return self.attributes end
		return instance
	end
	local function script(className, name, context, attributes, children)
		return fake(className, name, children, { source = true, RunContext = if context then { Name = context } else nil, attributes = attributes or {} })
	end
	-- Codificator JSON minimal cu regulile HttpService pentru textele din noduri (ghilimele, backslash, control → \uXXXX, UTF-8 brut).
	local function encodeText(text)
		return '"' .. (string.gsub(text, '[%c"\\]', function(char)
			if char == '"' then return '\\"' elseif char == "\\" then return "\\\\" end
			return string.format("\\u%04x", string.byte(char))
		end)) .. '"'
	end
	local function encode(nodes)
		local parts = {}
		for _, node in ipairs(nodes) do
			table.insert(parts, "[" .. node[1] .. "," .. node[2] .. "," .. encodeText(node[3]) .. "," .. encodeText(node[4]) .. "," .. node[5] .. "]")
		end
		return "[" .. table.concat(parts, ",") .. "]"
	end
	local function tree()
		local workspace = fake("Workspace", "Workspace", {
			fake("Model", "Map", { fake("Part", "Floor"), fake("Part", "Wall", nil, { attributes = { Zone = 3 } }) }),
			fake("Part", "Baseplate"),
		})
		local server = fake("ServerScriptService", "ServerScriptService", {
			script("Script", "Main", "Server"), script("Script", "Legacy", "Legacy"), script("ModuleScript", "Util", nil, { Version = 1 }),
		})
		local gui = fake("StarterGui", "StarterGui", { fake("ScreenGui", "Hud", { script("LocalScript", "HudController"), script("Script", "Bridge", "Client") }) })
		return { workspace, server, gui }
	end
	local function manyNodes(total)
		local nodes = {}
		for index = 1, total do nodes[index] = { index - 1, if index == 1 then -1 else 0, "Part", "Nume" .. index, index % 16 } end
		return nodes
	end

	test("Rădăcinile contractului și scanarea fără DataModel", function()
		assert(#Scanner.ROOTS == 14 and Scanner.ROOTS[1] == "Workspace" and Scanner.ROOTS[14] == "Chat" and Scanner.MAX_NODES == 20000)
		assert(#Scanner.defaultRoots() == 0)
		local result = Scanner.scan()
		assert(result.count == 0 and #result.nodes == 0 and result.truncated == false and result.digest == "0:0")
	end)
	test("Preordine: id consecutiv, părinte -1 pentru rădăcini, părinții înaintea copiilor", function()
		local result = Scanner.scan(tree())
		assert(result.count == 13 and #result.nodes == 13 and result.truncated == false)
		local names = {}
		for index, node in ipairs(result.nodes) do
			assert(#node == 5 and node[1] == index - 1 and node[2] < node[1] and node[2] >= -1)
			names[index] = node[4]
		end
		assert(table.concat(names, ",") == "Workspace,Map,Floor,Wall,Baseplate,ServerScriptService,Main,Legacy,Util,StarterGui,Hud,HudController,Bridge")
		assert(result.nodes[1][2] == -1 and result.nodes[6][2] == -1 and result.nodes[10][2] == -1)
		assert(result.nodes[2][2] == 0 and result.nodes[3][2] == 1 and result.nodes[4][2] == 1 and result.nodes[5][2] == 0 and result.nodes[12][2] == 10 and result.nodes[13][2] == 10)
		assert(result.nodes[1][3] == "Workspace" and result.nodes[3][3] == "Part" and result.nodes[9][3] == "ModuleScript")
		assert(type(result.digest) == "string" and string.sub(result.digest, 1, 3) == "13:")
	end)
	test("Flags: sursă, atribute, RunContext Client/Server", function()
		local flags = {}
		for _, node in ipairs(Scanner.scan(tree()).nodes) do flags[node[4]] = node[5] end
		assert(flags.Workspace == 0 and flags.Floor == 0 and flags.Wall == Scanner.FLAG_ATTRIBUTES)
		assert(flags.Main == Scanner.FLAG_SOURCE + Scanner.FLAG_SERVER and flags.Legacy == Scanner.FLAG_SOURCE)
		assert(flags.Util == Scanner.FLAG_SOURCE + Scanner.FLAG_ATTRIBUTES and flags.HudController == Scanner.FLAG_SOURCE)
		assert(flags.Bridge == Scanner.FLAG_SOURCE + Scanner.FLAG_CLIENT)
		local broken = fake("Part", "Rupt")
		broken.IsA = function() error("IsA indisponibil") end
		broken.GetAttributes = function() error("fără atribute") end
		assert(Scanner.flags(broken) == 0 and Scanner.flags(42) == 0)
	end)
	test("Instanțele ilizibile sunt sărite împreună cu subarborele", function()
		local locked = setmetatable({}, { __index = function() error("Instanță blocată") end })
		local ghost = setmetatable({ Name = "Ghost" }, { __index = function() error("fără ClassName") end })
		local unnamed = fake("Part", 5, { fake("Part", "CopilInvizibil") })
		local root = fake("Workspace", "Workspace", { locked, fake("Part", "Ok", { unnamed }), ghost, fake("", "FărăClasă") })
		local result = Scanner.scan({ root, 42, "text" })
		assert(result.count == 2 and result.nodes[1][4] == "Workspace" and result.nodes[2][4] == "Ok" and result.nodes[2][2] == 0)
		assert(result.truncated == false)
	end)
	test("Plafonul de 20 000 de noduri marchează trunchierea", function()
		local children = {}
		for index = 1, Scanner.MAX_NODES do children[index] = fake("Part", "P" .. index) end
		local root = fake("Workspace", "Workspace", children)
		local result = Scanner.scan({ root })
		assert(result.count == Scanner.MAX_NODES and #result.nodes == Scanner.MAX_NODES and result.truncated == true)
		assert(result.nodes[Scanner.MAX_NODES][4] == "P" .. (Scanner.MAX_NODES - 1) and string.sub(result.digest, 1, 6) == "20000:")
		table.remove(children)
		result = Scanner.scan({ root })
		assert(result.count == Scanner.MAX_NODES and result.truncated == false)
	end)
	test("Digestul se schimbă la redenumire și adăugare, nu la rescanare", function()
		local roots = tree()
		local first = Scanner.scan(roots).digest
		assert(Scanner.scan(roots).digest == first)
		roots[1].children[2].Name = "Baseplate2"
		local longer = Scanner.scan(roots).digest
		assert(longer ~= first)
		roots[1].children[2].Name = "Caseplate"
		local renamed = Scanner.scan(roots).digest
		assert(renamed ~= first and renamed ~= longer)
		table.insert(roots[1].children, fake("Part", "Nou"))
		assert(Scanner.scan(roots).digest ~= renamed)
	end)
	test("Numele lungi sunt tăiate la 200 de octeți fără a rupe UTF-8", function()
		local result = Scanner.scan({ fake("Folder", string.rep("ă", 150)), fake(string.rep("C", 250), "Scurt") })
		assert(#result.nodes[1][4] == 200 and utf8.len(result.nodes[1][4]) == 100 and #result.nodes[2][3] == 200)
	end)
	test("Estimarea per nod acoperă JSON-ul real", function()
		for _, name in ipairs({ "Simplu", 'Cu "ghilimele"', "Back\\slash", "Linie\nnouă\t", "Diacritice ăâîșț", "", string.rep("\1", 50) }) do
			local node = { 12345, -1, "Folder", name, 15 }
			assert(Scanner.nodeBytes(node) >= #encode({ node }) - 1, name)
		end
	end)
	test("Chunking după dimensiunea estimată păstrează ordinea și limita", function()
		local nodes = manyNodes(300)
		local chunks = Scanner.chunks(nodes, 600)
		assert(#chunks > 1)
		local total = 0
		for _, chunk in ipairs(chunks) do
			assert(#chunk > 0 and #encode(chunk) <= 600)
			for _, node in ipairs(chunk) do total += 1; assert(node == nodes[total]) end
		end
		assert(total == 300)
		assert(#Scanner.chunks(nodes, 10 * 1024 * 1024) == 1)
		local empty = Scanner.chunks({}, 600)
		assert(#empty == 1 and #empty[1] == 0)
		local oversized = Scanner.chunks({ { 0, -1, "Folder", string.rep("x", 199), 0 }, { 1, 0, "Part", "Mic", 0 } }, 100)
		assert(#oversized == 2 and #oversized[1] == 1 and #oversized[2] == 1)
	end)
	test("Limita implicită rămâne sub 700 KB pentru 20 000 de noduri", function()
		assert(Scanner.CHUNK_BYTES <= 700 * 1024)
		local nodes = manyNodes(Scanner.MAX_NODES)
		for index, node in ipairs(nodes) do node[4] = "Instanța cu nume lung " .. index end
		local chunks = Scanner.chunks(nodes)
		assert(#chunks >= 2 and #chunks <= 64)
		local total = 0
		for _, chunk in ipairs(chunks) do total += #chunk; assert(#encode(chunk) <= Scanner.CHUNK_BYTES) end
		assert(total == Scanner.MAX_NODES)
	end)
	test("Codificatorul real corectează estimarea; un codificator defect nu blochează", function()
		local nodes = manyNodes(300)
		local function inflated(chunk) return string.rep(encode(chunk), 2) end
		local plain = Scanner.chunks(nodes, 600)
		local checked = Scanner.chunks(nodes, 600, inflated)
		assert(#checked > #plain)
		local total = 0
		for _, chunk in ipairs(checked) do
			assert(#inflated(chunk) <= 600 or #chunk == 1)
			for _, node in ipairs(chunk) do total += 1; assert(node == nodes[total]) end
		end
		assert(total == 300)
		assert(#Scanner.chunks(nodes, 600, function() error("codificare imposibilă") end) == #plain)
		assert(#Scanner.chunks(nodes, 600, function() return nil end) == #plain)
	end)
	test("Identitatea locului: id-uri, nume și tipul creatorului din DataModel (sau tabele echivalente)", function()
		local place = Scanner.place({ PlaceId = 1291603, GameId = 987654, Name = "Ball", CreatorId = 555, CreatorType = { Name = "User" } })
		assert(place.place_id == 1291603 and place.game_id == 987654 and place.place_name == "Ball" and place.creator_id == 555 and place.creator_type == "User")
		assert(Scanner.place({ CreatorType = "Group", CreatorId = 7 }).creator_type == "Group" and Scanner.place({ CreatorType = { Name = "Group" } }).creator_type == "Group")
		assert(Scanner.place({ CreatorType = "Studio" }).creator_type == nil and Scanner.place({ CreatorType = { Name = 5 } }).creator_type == nil)
		assert(Scanner.place({ CreatorType = 3 }).creator_type == nil and Scanner.place({}).creator_type == nil)
		-- Fără `game` (luau.exe) și cu proprietăți ilizibile: valorile implicite, fără eroare.
		for _, model in ipairs({ Scanner.place(), Scanner.place(setmetatable({}, { __index = function() error("indisponibil") end })) }) do
			assert(model.place_id == 0 and model.game_id == 0 and model.place_name == "" and model.creator_id == 0 and model.creator_type == nil)
		end
		local odd = Scanner.place({ PlaceId = -5, GameId = "12", Name = 42, CreatorId = 1 / 0, CreatorType = "User" })
		assert(odd.place_id == 0 and odd.game_id == 12 and odd.place_name == "" and odd.creator_id == 0 and odd.creator_type == "User")
		assert(Scanner.place({ PlaceId = 0 / 0 }).place_id == 0 and Scanner.place({ PlaceId = 3.7 }).place_id == 3 and Scanner.place({ GameId = true }).game_id == 0)
		local long = Scanner.place({ Name = string.rep("ă", 150) })
		assert(#long.place_name == Scanner.MAX_PLACE_NAME and utf8.len(long.place_name) == 100)
	end)
	test("identityPayload și chunkPayload au exact câmpurile contractului", function()
		local place = Scanner.place({ PlaceId = 1291603, GameId = 987654, Name = "Ball", CreatorId = 555, CreatorType = { Name = "User" } })
		local identity = Scanner.identityPayload(place, 12345, "ellob")
		assert(identity.user_id == 12345 and identity.name == "ellob" and identity.place_id == 1291603 and identity.game_id == 987654)
		assert(identity.place_name == "Ball" and identity.creator_id == 555 and identity.creator_type == "User")
		local keys = {}
		for key in pairs(identity) do table.insert(keys, key) end
		table.sort(keys)
		assert(table.concat(keys, ",") == "creator_id,creator_type,game_id,name,place_id,place_name,user_id")
		-- Cont necunoscut (user_id 0): numele nu se trimite; id-urile invalide devin 0; numele lung se taie la 64.
		local unknown = Scanner.identityPayload(place, 0, "Studio")
		assert(unknown.user_id == 0 and unknown.name == "" and unknown.place_id == 1291603)
		assert(Scanner.identityPayload(place, -3, "x").user_id == 0 and Scanner.identityPayload(place, "77", nil).user_id == 77 and Scanner.identityPayload(place, "77", nil).name == "")
		assert(#Scanner.identityPayload(place, 1, string.rep("n", 100)).name == Scanner.MAX_USER_NAME)
		local bareIdentity = Scanner.identityPayload(nil, 5, "eu")
		assert(bareIdentity.user_id == 5 and bareIdentity.name == "eu" and bareIdentity.place_id == 0 and bareIdentity.place_name == "" and bareIdentity.creator_type == nil)
		local nodes = manyNodes(3)
		local project = { id = "snap-1", chunks = { { nodes[1], nodes[2] }, { nodes[3] } }, index = 2, truncated = true }
		local payload = Scanner.chunkPayload(project, place, "studio-A")
		assert(payload.snapshot_id == "snap-1" and payload.index == 1 and payload.total == 2 and payload.nodes == project.chunks[2] and payload.truncated == true and payload.studio_id == "studio-A")
		assert(payload.place_id == 1291603 and payload.game_id == 987654 and payload.place_name == "Ball" and payload.creator_id == 555 and payload.creator_type == "User")
		keys = {}
		for key in pairs(payload) do table.insert(keys, key) end
		table.sort(keys)
		assert(table.concat(keys, ",") == "creator_id,creator_type,game_id,index,nodes,place_id,place_name,snapshot_id,studio_id,total,truncated")
		project.index = 1
		local first = Scanner.chunkPayload(project, place)
		assert(first.index == 0 and first.nodes == project.chunks[1] and first.studio_id == nil and first.truncated == true)
		local bare = Scanner.chunkPayload({ id = "s", chunks = { {} }, index = 1 })
		assert(bare.place_id == 0 and bare.place_name == "" and bare.creator_type == nil and bare.truncated == false and bare.total == 1 and #bare.nodes == 0)
	end)
	test("Rezerva pentru antetul chunk-ului acoperă cele mai lungi valori posibile", function()
		-- Antetul cel mai mare: GUID, id-uri de 16 cifre, place_name de 200 de octeți de control (6 octeți fiecare în JSON), studio_id de 64, creator Group.
		local worst = Scanner.place({ PlaceId = 9007199254740991, GameId = 9007199254740991, CreatorId = 9007199254740991, Name = string.rep("\1", 300), CreatorType = "Group" })
		local payload = Scanner.chunkPayload({ id = string.rep("a", 36), chunks = { {} }, index = 1, truncated = false }, worst, string.rep("s", 64))
		local header = '{"snapshot_id":' .. encodeText(payload.snapshot_id) .. ',"index":' .. payload.index .. ',"total":64,"place_id":' .. payload.place_id
			.. ',"game_id":' .. payload.game_id .. ',"place_name":' .. encodeText(payload.place_name) .. ',"creator_id":' .. payload.creator_id
			.. ',"creator_type":' .. encodeText(payload.creator_type) .. ',"studio_id":' .. encodeText(payload.studio_id) .. ',"truncated":false,"nodes":}'
		assert(#payload.place_name == 200 and #header <= 700 * 1024 - Scanner.CHUNK_BYTES, #header)
	end)
	return passed
end
