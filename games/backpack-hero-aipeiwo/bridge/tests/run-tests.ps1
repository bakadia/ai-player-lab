# Run bridge unit tests. No .NET SDK needed: uses the bundled .NET Framework csc.
#
# These tests compile against the SYSTEM .NET Framework (MiniJson.cs only uses BCL),
# which is intentional: the parser must not depend on the game assemblies.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run-tests.ps1
#
# ASCII-only on purpose (Windows PowerShell 5.1 decodes BOM-less scripts by code page).

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "csc not found: $csc" }

$outDir = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$src = @(Get-ChildItem $root -Filter *.cs | ForEach-Object { $_.FullName })
$bridgeSrc = Join-Path $root '..\src'

$failed = 0
foreach ($t in $src) {
    $exe = Join-Path $outDir ([IO.Path]::GetFileNameWithoutExtension($t) + '.exe')
    Write-Host "=== $([IO.Path]::GetFileName($t))"

    # MiniJson.cs is a dependency of the test; keep this list explicit so a test
    # never silently picks up the whole bridge.
    $deps = @()
    if ((Get-Content $t -Raw) -match 'MiniJson') { $deps += (Join-Path $bridgeSrc 'MiniJson.cs') }

    $cscArgs = @('/nologo', '/target:exe', '/codepage:65001', "/out:$exe", $t) + $deps
    & $csc @cscArgs
    if ($LASTEXITCODE -ne 0) { throw "compile failed: $t" }

    & $exe
    if ($LASTEXITCODE -ne 0) { $failed++ }
}

if ($failed -gt 0) { throw "$failed test file(s) failed" }
Write-Host "all tests passed"
