# Compilează installer/StudioHarnessSetup.cs în dist/StudioHarnessSetup.exe cu csc.exe din .NET Framework.
# Nu are nevoie de nimic instalat: csc.exe vine cu Windows, iar executabilul rezultat rulează pe orice Windows 10/11.
param(
    [string]$Output = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$source = Join-Path $root "installer\StudioHarnessSetup.cs"
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Lipsește $source."
}
if (-not $Output) { $Output = Join-Path $root "dist\StudioHarnessSetup.exe" }

$framework = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"
if (-not (Test-Path -LiteralPath $framework -PathType Container)) {
    $framework = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319"
}
$compiler = Join-Path $framework "csc.exe"
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "Nu am găsit csc.exe (.NET Framework 4) în $framework."
}

New-Item -ItemType Directory -Force (Split-Path -Parent $Output) | Out-Null
$references = @("System.dll", "System.Core.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll")
$arguments = @("/nologo", "/target:exe", "/platform:anycpu", "/optimize+", "/utf8output", "/warnaserror-", "/out:$Output")
foreach ($reference in $references) {
    $path = Join-Path $framework $reference
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Lipsește referința $reference din $framework." }
    $arguments += "/reference:$path"
}
$arguments += $source

# Atenție: variabilele PowerShell nu fac diferența între majuscule și minuscule, deci jurnalul nu se poate numi $output (ar rescrie $Output).
$log = & $compiler @arguments 2>&1
if ($LASTEXITCODE -ne 0) {
    $log | ForEach-Object { Write-Host $_ }
    throw "Compilarea a eșuat (cod $LASTEXITCODE)."
}
$built = Get-Item -LiteralPath $Output
Write-Host ("Instalator construit: {0} ({1:N0} octeți)" -f $built.FullName, $built.Length)
