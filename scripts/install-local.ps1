param()

$ErrorActionPreference = 'Stop'
$pluginRoot = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $pluginRoot
$skillsDirectory = Join-Path $workspace '.claude\skills'
$destination = Join-Path $skillsDirectory 'roblox-studio-harness'

if (-not (Test-Path -LiteralPath (Join-Path $pluginRoot '.claude-plugin\plugin.json'))) {
    throw 'Manifestul pluginului lipsește.'
}
if (Test-Path -LiteralPath $destination) {
    $existing = Get-Item -LiteralPath $destination -Force
    if ($existing.LinkType -ne 'Junction' -or @($existing.Target)[0] -ne $pluginRoot) {
        throw 'Destinația există și nu este legătura acestui plugin. Nu a fost suprascrisă.'
    }
} else {
    New-Item -ItemType Directory -Path $skillsDirectory -Force | Out-Null
    New-Item -ItemType Junction -Path $destination -Target $pluginRoot | Out-Null
}
[pscustomobject]@{
    Plugin = 'roblox-studio-harness@skills-dir'
    InstalledFor = 'Folderul curent, nu global'
    Source = $pluginRoot
    DiscoveryDirectory = $destination
    NextStep = 'Folosește /reload-plugins; dacă pluginul nou nu apare, reia sesiunea în același folder. Verifică /mcp.'
    PermissionsChanged = $false
}
