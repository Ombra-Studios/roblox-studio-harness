-- Inventarul DataModel-ului pentru harta proiectului (faza 4) și identitatea locului deschis (1.0).
-- Pur: fără servicii Roblox, în afară de `game` pentru rădăcinile implicite și pentru `Scanner.place()`.
local Scanner = {}
Scanner.ROOTS = { "Workspace", "ReplicatedStorage", "ReplicatedFirst", "ServerScriptService", "ServerStorage", "StarterGui", "StarterPack",
	"StarterPlayer", "Lighting", "SoundService", "MaterialService", "Teams", "TextChatService", "Chat" }
Scanner.MAX_NODES = 20000
Scanner.MAX_NAME = 200
Scanner.MAX_PLACE_NAME = 200
Scanner.MAX_USER_NAME = 64
Scanner.CHUNK_BYTES = 700 * 1024 - 2048 -- rezervă pentru antetul cererii (snapshot_id, place_name, creator, studio_id…)
Scanner.FLAG_SOURCE, Scanner.FLAG_ATTRIBUTES, Scanner.FLAG_CLIENT, Scanner.FLAG_SERVER = 1, 2, 4, 8
-- `creator_type` acceptat de daemon (contract §4.4): User, Group sau nil (necunoscut).
Scanner.CREATOR_TYPES = { User = "User", Group = "Group" }

local function clip(text, limit)
	if #text <= limit then return text end
	local stop = limit
	while stop > 0 do
		local byte = string.byte(text, stop + 1)
		if not byte or byte < 128 or byte >= 192 then break end
		stop -= 1
	end
	return string.sub(text, 1, stop)
end

-- Întreg ≥ 0 pentru id-urile Roblox; orice altceva (nil, text, NaN, negativ) devine 0.
local function identifier(value)
	local number = tonumber(value)
	if number == nil or number ~= number or math.abs(number) == math.huge or number < 0 then return 0 end
	return math.floor(number)
end

-- Identitatea locului deschis (contract §2.2, §4.4, §8.5): `game.PlaceId`, `game.GameId`, `game.Name`, `game.CreatorId`, `game.CreatorType`.
-- `dataModel` este `game` în Studio sau un tabel echivalent în teste; proprietățile ilizibile sau lipsă devin 0 / "" / nil.
function Scanner.place(dataModel)
	dataModel = dataModel or game
	local function read(property)
		local ok, value = pcall(function() return dataModel[property] end)
		return if ok then value else nil
	end
	-- `CreatorType` este un EnumItem în Studio (`.Name`), un text în teste; altceva înseamnă creator necunoscut.
	local creatorType = read("CreatorType")
	local okName, enumName = pcall(function() return creatorType.Name end)
	local resolved = if okName and type(enumName) == "string" then enumName else creatorType
	local name = read("Name")
	return { place_id = identifier(read("PlaceId")), game_id = identifier(read("GameId")),
		place_name = clip(if type(name) == "string" then name else "", Scanner.MAX_PLACE_NAME),
		creator_id = identifier(read("CreatorId")), creator_type = Scanner.CREATOR_TYPES[resolved] }
end

-- Corpul `POST /v1/identity` (contract §4.4): contul Roblox plus locul deschis; `userId` 0 = cont necunoscut (numele rămâne gol).
function Scanner.identityPayload(place, userId, userName)
	place = place or Scanner.place()
	local id = identifier(userId)
	local name = if id > 0 and type(userName) == "string" then clip(userName, Scanner.MAX_USER_NAME) else ""
	return { user_id = id, name = name, place_id = place.place_id, game_id = place.game_id, place_name = place.place_name,
		creator_id = place.creator_id, creator_type = place.creator_type }
end

-- Corpul unui chunk pentru `POST /v1/project/chunks` (contract §8.5): `project = { id, chunks, index (1-based), truncated }`, indexul trimis este 0-based.
function Scanner.chunkPayload(project, place, studioId)
	place = place or Scanner.place()
	return { snapshot_id = project.id, index = project.index - 1, total = #project.chunks,
		place_id = place.place_id, game_id = place.game_id, place_name = place.place_name, creator_id = place.creator_id, creator_type = place.creator_type,
		studio_id = studioId, truncated = project.truncated == true, nodes = project.chunks[project.index] }
end

function Scanner.defaultRoots()
	local roots = {}
	for _, name in ipairs(Scanner.ROOTS) do
		-- FindFirstChildOfClass nu creează serviciul lipsă (GetService ar modifica DataModel-ul).
		local ok, service = pcall(function() return game:FindFirstChildOfClass(name) end)
		if ok and service then table.insert(roots, service) end
	end
	return roots
end

function Scanner.flags(instance)
	local flags = 0
	local okSource, source = pcall(function() return instance:IsA("LuaSourceContainer") end)
	if okSource and source == true then flags += Scanner.FLAG_SOURCE end
	local okAttributes, attributes = pcall(function() return next(instance:GetAttributes()) ~= nil end)
	if okAttributes and attributes == true then flags += Scanner.FLAG_ATTRIBUTES end
	-- EnumItem.Name (Enum.RunContext.Client/Server) evită referința la globalul Enum, absent în afara Studio.
	local okContext, context = pcall(function() return instance.RunContext.Name end)
	if okContext then
		if context == "Client" then flags += Scanner.FLAG_CLIENT elseif context == "Server" then flags += Scanner.FLAG_SERVER end
	end
	return flags
end

-- Parcurgere în preordine: părintele primește întotdeauna un id mai mic decât copiii; instanțele ilizibile sunt sărite cu tot subarborele.
function Scanner.scan(roots)
	roots = roots or Scanner.defaultRoots()
	local nodes, hash, truncated = {}, 0, false
	local stackInstances, stackParents = {}, {}
	for index = #roots, 1, -1 do
		table.insert(stackInstances, roots[index]); table.insert(stackParents, -1)
	end
	while #stackInstances > 0 do
		local instance, parent = table.remove(stackInstances), table.remove(stackParents)
		local ok, name, className = pcall(function() return instance.Name, instance.ClassName end)
		if ok and type(name) == "string" and type(className) == "string" and className ~= "" then
			if #nodes >= Scanner.MAX_NODES then truncated = true; break end
			local id = #nodes
			table.insert(nodes, { id, parent, clip(className, Scanner.MAX_NAME), clip(name, Scanner.MAX_NAME), Scanner.flags(instance) })
			-- Sumă simplă a numelor (lungime, primul și ultimul octet) și a claselor, sensibilă la ordine.
			hash = (hash * 31 + #name * 131 + (string.byte(name) or 0) * 7 + (string.byte(name, -1) or 0) + #className) % 2147483647
			local okChildren, children = pcall(function() return instance:GetChildren() end)
			if okChildren and type(children) == "table" then
				for childIndex = #children, 1, -1 do
					table.insert(stackInstances, children[childIndex]); table.insert(stackParents, id)
				end
			end
		end
	end
	return { nodes = nodes, truncated = truncated, count = #nodes, digest = #nodes .. ":" .. hash }
end

local function textBytes(text)
	local _, escapes = string.gsub(text, '[%c"\\]', "")
	local _, high = string.gsub(text, "[\128-\255]", "")
	-- Estimare acoperitoare: caracterele escapate ca \uXXXX și octeții non-ASCII codificați de HttpService.
	return 2 + #text + 5 * escapes + 2 * high
end

function Scanner.nodeBytes(node)
	return 7 + #tostring(node[1]) + #tostring(node[2]) + #tostring(node[5]) + textBytes(node[3]) + textBytes(node[4])
end

-- Împarte nodurile în chunk-uri al căror JSON rămâne sub maxBytes; `encode` (opțional) confirmă cu codificatorul real.
function Scanner.chunks(nodes, maxBytes, encode)
	maxBytes = maxBytes or Scanner.CHUNK_BYTES
	local pending, current, size = {}, {}, 2
	for _, node in ipairs(nodes) do
		local bytes = Scanner.nodeBytes(node)
		if #current > 0 and size + bytes > maxBytes then
			table.insert(pending, current); current, size = {}, 2
		end
		table.insert(current, node); size += bytes
	end
	table.insert(pending, current)
	if type(encode) ~= "function" then return pending end
	-- Un chunk care depășește limita la codificarea reală se înjumătățește; ordinea nodurilor rămâne.
	local result, index = {}, 1
	while index <= #pending do
		local chunk = pending[index]
		local ok, text = pcall(encode, chunk)
		if ok and type(text) == "string" and #text > maxBytes and #chunk > 1 then
			local half = math.ceil(#chunk / 2)
			pending[index] = table.move(chunk, 1, half, 1, {})
			table.insert(pending, index + 1, table.move(chunk, half + 1, #chunk, 1, {}))
		else
			table.insert(result, chunk); index += 1
		end
	end
	return result
end

return Scanner
