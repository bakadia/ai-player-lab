# dnmap 构建 + 运行。
#
# 不依赖 .NET SDK / MSBuild：用 Windows 自带的 .NET Framework csc (C# 5)
# 编译，再引用 MelonLoader 安装包里自带的 Mono.Cecil 读元数据。
#
#   .\build.ps1                # 编译并 dump 默认的 Assembly-CSharp.dll
#   .\build.ps1 -NoRun         # 只编译
#   .\build.ps1 -Assembly <path> -OutDir <dir>
#
# 本机执行策略是 Restricted，直接 .\build.ps1 会被拒；用：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1

[CmdletBinding()]
param(
    [string]$Assembly,
    [string]$OutDir,
    [string]$CecilDir,
    [switch]$NoRun
)

$ErrorActionPreference = 'Stop'
# csc 的中文报错在 GBK 控制台会乱码
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# 注意：param 默认值里 $PSScriptRoot 在部分 Windows PowerShell 下为空，
# 所以路径一律在脚本体里算。
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

if (-not $Assembly) { $Assembly = 'E:\steam\steamapps\common\Backpack Hero\Backpack Hero_Data\Managed\Assembly-CSharp.dll' }
if (-not $OutDir)   { $OutDir   = Join-Path $root '..\..\games\backpack-hero-aipeiwo\runtime\inspect' }
if (-not $CecilDir) { $CecilDir = Join-Path $root '..\..\games\backpack-hero-aipeiwo\.tools\melonloader-0.7.3\MelonLoader\net472' }

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "csc not found: $csc" }

$cecil = Join-Path $CecilDir 'Mono.Cecil.dll'
if (-not (Test-Path $cecil)) {
    throw "Mono.Cecil.dll not found: $cecil (put MelonLoader in games/backpack-hero-aipeiwo/.tools first)"
}

$bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$exe = Join-Path $bin 'Dump.exe'

$fw = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'

Write-Host "compiling -> $exe"
& $csc /nologo /target:exe /platform:anycpu /optimize+ /langversion:5 `
    "/out:$exe" `
    "/r:$cecil" `
    "/r:$(Join-Path $fw 'System.dll')" `
    "/r:$(Join-Path $fw 'System.Core.dll')" `
    (Join-Path $root 'Dump.cs')
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }

# Xref.exe：字段/方法的交叉引用（谁读、谁写、谁调）
$xref = Join-Path $bin 'Xref.exe'
Write-Host "compiling -> $xref"
& $csc /nologo /target:exe /platform:anycpu /optimize+ /langversion:5 `
    "/out:$xref" `
    "/r:$cecil" `
    "/r:$(Join-Path $fw 'System.dll')" `
    "/r:$(Join-Path $fw 'System.Core.dll')" `
    (Join-Path $root 'Xref.cs')
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }

Copy-Item $cecil $bin -Force

if ($NoRun) { return }

# Dump.exe 只依赖 Mono.Cecil.dll，运行时放在同目录
Copy-Item $cecil $bin -Force

$OutDir = [IO.Path]::GetFullPath($OutDir)
Write-Host "dumping  : $Assembly"
Write-Host "into     : $OutDir"
& $exe $Assembly $OutDir
if ($LASTEXITCODE -ne 0) { throw "Dump.exe failed with exit code $LASTEXITCODE" }
