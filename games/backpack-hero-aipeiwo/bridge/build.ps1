# 构建 BphBridge.dll —— 不依赖 .NET SDK / MSBuild。
#
# 本机现状（2026-10-25 实测）：
#   dotnet build  -> 无 .NET SDK（C:\Program Files\dotnet\sdk 不存在，只有 runtime）
#   msbuild       -> 有 VS2022 MSBuild，但没装 Roslyn 组件
#                    (MSBuild\Current\Bin\Roslyn\Microsoft.CSharp.Core.targets 缺失)
#   csc.exe       -> Windows 自带 .NET Framework csc 可用，语言版本上限 C# 5
#
# 所以统一用 csc 直接编译；BphBridge.csproj 保留做参照/IDE 用，
# 它引用的程序集和这里一一对应。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
#   powershell ... -File .\build.ps1 -Deploy      # 顺便拷进游戏的 Mods/

[CmdletBinding()]
param(
    [string]$GameDir,
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

if (-not $GameDir) { $GameDir = 'E:\steam\steamapps\common\Backpack Hero' }
if (-not (Test-Path $GameDir)) { throw "game dir not found: $GameDir" }

$managed = Join-Path $GameDir 'Backpack Hero_Data\Managed'
$melon   = Join-Path $GameDir 'MelonLoader\net472'
$csc     = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$fw      = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'

foreach ($p in $csc, $managed, $melon) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$outDir = Join-Path $root 'bin\Release'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$out = Join-Path $outDir 'BphBridge.dll'

$sources = @(Get-ChildItem (Join-Path $root 'src') -Filter *.cs -Recurse | ForEach-Object { $_.FullName })
if ($sources.Count -eq 0) { throw "no .cs found under $root\src" }

$refs = @(
    (Join-Path $melon   'MelonLoader.dll')
    (Join-Path $managed 'UnityEngine.dll')
    (Join-Path $managed 'UnityEngine.CoreModule.dll')
    (Join-Path $managed 'Assembly-CSharp.dll')
    (Join-Path $managed 'Newtonsoft.Json.dll')
    (Join-Path $fw      'System.dll')
    (Join-Path $fw      'System.Core.dll')
)

$cscArgs = @('/nologo', '/target:library', '/platform:anycpu', '/optimize+', '/langversion:5', "/out:$out")
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
