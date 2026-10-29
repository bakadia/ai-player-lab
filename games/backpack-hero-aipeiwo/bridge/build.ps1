# Build BphBridge.dll without .NET SDK / MSBuild.
#
# Machine state (verified 2026-10-25):
#   dotnet build  -> no .NET SDK (only runtime installed)
#   msbuild       -> VS2022 MSBuild exists but the Roslyn component is missing
#                    (MSBuild\Current\Bin\Roslyn\Microsoft.CSharp.Core.targets absent)
#   csc.exe       -> Windows .NET Framework csc works, C# 5 max
#
# So we compile with csc directly. BphBridge.csproj is kept for reference/IDE only.
#
# IMPORTANT: references must come from the GAME's own BCL, not the system
# .NET Framework. Assembly-CSharp.dll is built against netstandard 2.1, so its
# public API surface points List<T>/System.Object/System.Enum at netstandard.
# Compiling against the system mscorlib fails with CS0012.
#
# This file is ASCII-only on purpose: Windows PowerShell 5.1 decodes BOM-less
# scripts using the OEM/ANSI code page, and non-ASCII comments can corrupt parsing.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
#   powershell ... -File .\build.ps1 -Deploy

[CmdletBinding()]
param(
    [string]$GameDir,
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

if (-not $GameDir) { $GameDir = 'E:\steam\steamapps\common\Backpack Hero' }
if (-not (Test-Path $GameDir)) { throw "game dir not found: $GameDir" }

$managed = Join-Path $GameDir 'Backpack Hero_Data\Managed'
$melon   = Join-Path $GameDir 'MelonLoader\net472'
$csc     = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'

foreach ($p in $csc, $managed, $melon) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$outDir = Join-Path $root 'bin\Release'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$out = Join-Path $outDir 'BphBridge.dll'

$sources = @(Get-ChildItem (Join-Path $root 'src') -Filter *.cs -Recurse | ForEach-Object { $_.FullName })
if ($sources.Count -eq 0) { throw "no .cs found under $root\src" }

$refs = @(
    (Join-Path $managed 'mscorlib.dll')
    (Join-Path $managed 'netstandard.dll')
    (Join-Path $managed 'System.dll')
    (Join-Path $managed 'System.Core.dll')
    (Join-Path $melon   'MelonLoader.dll')
    (Join-Path $managed 'UnityEngine.dll')
    (Join-Path $managed 'UnityEngine.CoreModule.dll')
    (Join-Path $managed 'UnityEngine.Physics2DModule.dll')
    (Join-Path $managed 'UnityEngine.UI.dll')
    (Join-Path $managed 'Assembly-CSharp.dll')
)

# /noconfig : csc would otherwise read csc.rsp and auto-reference the system
#             System.dll / System.Core.dll -> CS1703 duplicate assembly identity.
# /nostdlib+ : do not implicitly reference the system mscorlib.
# /codepage  : read sources as UTF-8; without it csc uses the local ANSI code page
#             and non-ASCII (Chinese) comments get mangled.
$cscArgs = @('/nologo', '/noconfig', '/target:library', '/platform:anycpu', '/optimize+', '/langversion:5', '/nostdlib+', '/codepage:65001', "/out:$out")
foreach ($r in $refs) {
    if (-not (Test-Path $r)) { throw "reference not found: $r" }
    $cscArgs += "/r:$r"
}
foreach ($s in $sources) { $cscArgs += $s }

Write-Host ("csc {0} source(s) -> {1}" -f $sources.Count, $out)
& $csc @cscArgs
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }

$dll = Get-Item $out
Write-Host ("built: {0} ({1} bytes)" -f $dll.Name, $dll.Length)

if ($Deploy) {
    $mods = Join-Path $GameDir 'Mods'
    New-Item -ItemType Directory -Force -Path $mods | Out-Null
    Copy-Item $out $mods -Force
    Write-Host "deployed -> $(Join-Path $mods 'BphBridge.dll')"
}
