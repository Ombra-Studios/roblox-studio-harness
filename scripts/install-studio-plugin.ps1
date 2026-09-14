param()

$ErrorActionPreference = 'Stop'
# Rezumatul JSON iese ca UTF-8 și în consolă, și în pipe (Install-Studio-Plugin.cmd, teste).
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'dist\StudioHarness.rbxmx'
$modulesSource = Join-Path $root 'studio-plugin\modules'
$pluginDirectory = Join-Path $env:LOCALAPPDATA 'Roblox\Plugins'
$target = Join-Path $pluginDirectory 'StudioHarness.rbxmx'
# 0.8: aplicația (modulele Luau) stă în Roblox\Plugins\StudioHarness\app; loader-ul din Studio o încarcă fără repornire.
$appDirectory = Join-Path $pluginDirectory 'StudioHarness\app'
# Directorul de stare al daemon-ului (local_state.state_dir): STUDIO_HARNESS_STATE_DIR sau %LOCALAPPDATA%\StudioHarness.
$stateDirectory = $env:STUDIO_HARNESS_STATE_DIR
if (-not $stateDirectory) {
    $stateDirectory = Join-Path $env:LOCALAPPDATA 'StudioHarness'
}
$backupDirectory = Join-Path $stateDirectory 'plugin-backups'
# 1.0: tokenul UI plugin↔daemon (`local-token`) se injectează în fișierul instalat în locul placeholder-ului din dist/.
$tokenFile = Join-Path $stateDirectory 'local-token'
$tokenPlaceholder = 'STUDIO_HARNESS_LOCAL_TOKEN_PLACEHOLDER'
$tokenPattern = '^[A-Za-z0-9_\-]{16,512}$'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
$utf8NoBom = New-Object Text.UTF8Encoding $false

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw 'Construiește mai întâi StudioHarness.rbxmx cu build_studio_plugin.py.'
}
if (-not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Roblox') -PathType Container)) {
    throw 'Directorul Roblox nu există. Instalează Roblox Studio mai întâi.'
}
$moduleFiles = @()
if (Test-Path -LiteralPath $modulesSource -PathType Container) {
    $moduleFiles = @(Get-ChildItem -LiteralPath $modulesSource -Filter '*.luau' -File)
}
if ($moduleFiles.Count -eq 0) {
    throw 'studio-plugin\modules nu conține module .luau; aplicația din Studio nu poate fi instalată.'
}
if (-not ($moduleFiles | Where-Object { $_.Name -eq 'Main.luau' })) {
    throw 'studio-plugin\modules\Main.luau lipsește: loader-ul din Studio pornește aplicația din Main.'
}

# Tokenul local: citit dacă există și este valid, altfel creat o singură dată (același format ca local_state.ensure_local_token:
# 32 de octeți aleatori în base64 URL-safe, fără `=`). Nu se afișează niciodată.
$token = $null
$tokenCreated = $false
if (Test-Path -LiteralPath $tokenFile -PathType Leaf) {
    $existingToken = ([IO.File]::ReadAllText($tokenFile, [Text.Encoding]::UTF8)).Trim()
    if ($existingToken -match $tokenPattern) {
        $token = $existingToken
    }
}
if (-not $token) {
    $randomBytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($randomBytes)
    } finally {
        $generator.Dispose()
    }
    $token = [Convert]::ToBase64String($randomBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    [IO.File]::WriteAllText($tokenFile, $token, $utf8NoBom)
    $tokenCreated = $true
}

# Conținutul instalat: pachetul din dist/ (doar placeholder) cu tokenul injectat; dist/ rămâne neatins.
$packaged = [IO.File]::ReadAllText($source, [Text.Encoding]::UTF8)
$tokenInjected = $packaged.Contains($tokenPlaceholder)
$installed = $packaged.Replace($tokenPlaceholder, $token)
$installedBytes = $utf8NoBom.GetBytes($installed)
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $expectedHash = ([BitConverter]::ToString($sha256.ComputeHash($installedBytes))).Replace('-', '')
} finally {
    $sha256.Dispose()
}

$backup = $null
if (Test-Path -LiteralPath $target) {
    $existing = Get-Item -LiteralPath $target -Force
    if ($existing.PSIsContainer -or $existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Destinația existentă nu este un fișier obișnuit; nu o suprascriem.'
    }
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $backup = Join-Path $backupDirectory ('StudioHarness-' + $stamp + '.rbxmx')
    Copy-Item -LiteralPath $target -Destination $backup
}
New-Item -ItemType Directory -Path $pluginDirectory -Force | Out-Null
[IO.File]::WriteAllBytes($target, $installedBytes)
$installedHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
if ($expectedHash -ne $installedHash) {
    throw 'Verificarea fișierului instalat a eșuat.'
}

$appBackup = $null
if (Test-Path -LiteralPath $appDirectory) {
    $existingApp = Get-Item -LiteralPath $appDirectory -Force
    if (-not $existingApp.PSIsContainer -or ($existingApp.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Folderul app existent nu este un folder obișnuit; nu îl înlocuim.'
    }
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $appBackup = Join-Path $backupDirectory ('app-' + $stamp)
    Copy-Item -LiteralPath $appDirectory -Destination $appBackup -Recurse
    Remove-Item -LiteralPath $appDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $appDirectory -Force | Out-Null
foreach ($file in $moduleFiles) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $appDirectory $file.Name) -Force
}
$installedModules = @(Get-ChildItem -LiteralPath $appDirectory -Filter '*.luau' -File)
if ($installedModules.Count -ne $moduleFiles.Count) {
    throw 'Verificarea modulelor instalate a eșuat.'
}

if ($tokenInjected) {
    $nextStep = 'Salvează lucrul din Studio, apoi repornește Studio o dată pentru loader. Pluginul se conectează singur la daemon; de acum aplicația se actualizează singură din folderul app.'
} else {
    $nextStep = 'Salvează lucrul din Studio, apoi repornește Studio o dată pentru loader. Pachetul nu are placeholder-ul LocalToken (loader vechi): introdu codul de asociere din Avansat.'
}

# JSON-ul se scrie direct în consolă: formatorul PowerShell 5.1 ar rupe liniile lungi la lățimea ferestrei. Fără token în rezumat.
$summary = [pscustomobject]@{
    Plugin = 'StudioHarness.rbxmx'
    Installed = $true
    HashVerified = $true
    Backup = $backup
    AppDirectory = $appDirectory
    AppModules = $installedModules.Count
    AppBackup = $appBackup
    LocalTokenFile = $tokenFile
    LocalTokenCreated = $tokenCreated
    LocalTokenInjected = $tokenInjected
    StudioRestartRequired = $true
    NextStep = $nextStep
    PlayerChanged = $false
}
[Console]::Out.WriteLine(($summary | ConvertTo-Json))
