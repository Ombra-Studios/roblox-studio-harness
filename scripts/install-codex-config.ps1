# Adaugă serverul MCP studio_hub și notify-ul Studio Harness în configurația Codex a utilizatorului.
$ErrorActionPreference = "Stop"
# Mesajele au diacritice: fără asta consola Windows (cp1252/OEM) le strică, mai ales când scriptul este chemat de instalator.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$shim = (Join-Path $root "scripts\harness_mcp.py") -replace "\\", "\\"
$hook = (Join-Path $root "scripts\harness_hook.py") -replace "\\", "\\"
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$config = Join-Path $codexHome "config.toml"
New-Item -ItemType Directory -Force $codexHome | Out-Null
$existing = if (Test-Path $config) { Get-Content $config -Raw -Encoding UTF8 } else { "" }

if ($existing -match "\[mcp_servers\.studio_hub\]") {
    Write-Host "Codex: serverul studio_hub este deja configurat în $config."
} else {
    if ($existing -and -not $existing.EndsWith("`n")) { $existing += "`n" }
    $block = @"

# Studio Harness Hub (adăugat de Install-Codex-Config.cmd)
[mcp_servers.studio_hub]
command = "python"
args = ["-I", "$shim"]
env = { STUDIO_HARNESS_PROVIDER = "codex" }
"@
    $existing += $block
    Write-Host "Codex: am adăugat serverul MCP studio_hub în $config."
}

if ($existing -match "(?m)^\s*notify\s*=") {
    if ($existing -notmatch "harness_hook\.py") {
        Write-Warning "Codex: config.toml are deja o cheie notify. Adaugă manual harness_hook.py --codex sau înlocuiește notify-ul existent."
    }
} else {
    if (-not $existing.EndsWith("`n")) { $existing += "`n" }
    $existing = "notify = [`"python`", `"-I`", `"$hook`", `"--codex`"]`n" + $existing
    Write-Host "Codex: am adăugat notify pentru feed-ul live din Studio."
}

if (Test-Path $config) { Copy-Item $config ($config + ".studio-harness.bak") -Force }
[System.IO.File]::WriteAllText($config, $existing, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Gata. Repornește Codex ca să încarce configurația."
