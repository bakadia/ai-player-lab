# BPH game lifecycle helper.
#
#   bph.cmd status    show process / dll / state
#   bph.cmd stop      gracefully close the game (then kill if it does not exit)
#   bph.cmd start     deploy newest BphBridge.dll (if any), launch, wait for state.json
#   bph.cmd restart   stop + start
#   bph.cmd deploy    copy the built dll only (game must be closed)
#
# Why this exists: replacing Mods\BphBridge.dll needs the game to be CLOSED
# (the assembly is memory-mapped, see docs/PITFALLS.md E2). Doing that by hand
# is two steps plus a wait; this script does stop -> deploy -> start in one go.
#
# Admin: writing into the Steam folder usually needs an elevated shell.
# From an elevated PowerShell:   .\bph.ps1 restart
# Without admin, -Elevate relaunches this script through UAC.
#
# ASCII-only on purpose: Windows PowerShell 5.1 decodes BOM-less files by the
# local code page, and non-ASCII comments can swallow the next line (PITFALLS E6).

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'stop', 'start', 'restart', 'deploy', 'help')]
    [string]$Action = 'status',

    [string]$GameDir,
    [string]$Runtime,
    [switch]$NoDeploy,
    [switch]$Elevate,
    [switch]$Quiet,
    [int]$WaitSec = 60,
    [switch]$KeepWindow,
    [ValidateSet('auto', 'primary', 'secondary')]
    [string]$Display = 'auto'
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- paths

$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

if (-not $GameDir) { $GameDir = $env:BPH_GAME_DIR }
if (-not $GameDir) { $GameDir = 'E:\steam\steamapps\common\Backpack Hero' }

if (-not $Runtime) { $Runtime = Join-Path $root 'runtime' }

$exe      = Join-Path $GameDir 'Backpack Hero.exe'
$mods     = Join-Path $GameDir 'Mods'
$dllSrc   = Join-Path $root 'bridge\bin\Release\BphBridge.dll'
$dllDst   = Join-Path $mods 'BphBridge.dll'
$state    = Join-Path $Runtime 'state.json'
$haltFile = Join-Path $Runtime 'halt'

function Say([string]$msg) {
    if (-not $Quiet) { Write-Host $msg }
}

function Fail([string]$msg) {
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- admin

function Test-Admin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($id)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

if ($Elevate -and -not (Test-Admin)) {
    $argList = @(
        '-NoProfile'
        '-ExecutionPolicy', 'Bypass'
        '-File', ('"' + $PSCommandPath + '"')
        $Action
    )
    if ($GameDir) { $argList += @('-GameDir', ('"' + $GameDir + '"')) }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs
    Say 'relaunched in an elevated window; watch that window for the result'
    exit 0
}

# ---------------------------------------------------------------- processes

function Get-BphPids {
    $pids = New-Object System.Collections.ArrayList

    try {
        $ps = @(Get-Process -Name 'Backpack Hero' -ErrorAction SilentlyContinue)
        foreach ($p in $ps) { if ($p) { [void]$pids.Add([int]$p.Id) } }
    } catch { }

    if ($pids.Count -eq 0) {
        try {
            $cim = @(Get-CimInstance Win32_Process -Filter "Name='Backpack Hero.exe'" -ErrorAction SilentlyContinue)
            foreach ($c in $cim) { if ($c) { [void]$pids.Add([int]$c.ProcessId) } }
        } catch { }
    }

    if ($pids.Count -eq 0) {
        try {
            $wmi = @(Get-WmiObject Win32_Process -Filter "Name='Backpack Hero.exe'" -ErrorAction SilentlyContinue)
            foreach ($c in $wmi) { if ($c) { [void]$pids.Add([int]$c.ProcessId) } }
        } catch { }
    }

    return @($pids | Sort-Object -Unique)
}

# ---------------------------------------------------------------- window fit

function Fit-BphWindow {
    if ($KeepWindow) { return }

    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        if (-not ('BphWin32' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public struct BphRect { public int Left; public int Top; public int Right; public int Bottom; }
public static class BphWin32 {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out BphRect r);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hh, uint flags);
}
'@
        }
    } catch {
        return
    }

    try {
        # 选哪块屏：默认放到「非主屏」（远程桌面/副屏常见需求），没有副屏才用主屏。
        $target = $null
        $screens = [System.Windows.Forms.Screen]::AllScreens
        if ($Display -eq 'primary') {
            $target = $screens | Where-Object { $_.Primary } | Select-Object -First 1
        } elseif ($Display -eq 'secondary') {
            $target = $screens | Where-Object { -not $_.Primary } | Select-Object -First 1
        } else {
            $target = $screens | Where-Object { -not $_.Primary } | Select-Object -First 1
            if (-not $target) { $target = $screens | Where-Object { $_.Primary } | Select-Object -First 1 }
        }
        if (-not $target) { $target = [System.Windows.Forms.Screen]::PrimaryScreen }
        $scr = $target.Bounds

        $proc = $null
        foreach ($p in @(Get-Process -Name 'Backpack Hero' -ErrorAction SilentlyContinue)) {
            if ($p -and $p.MainWindowHandle -ne 0) { $proc = $p; break }
        }
        if (-not $proc) { return }

        $h = $proc.MainWindowHandle
        $r = New-Object BphRect
        if (-not [BphWin32]::GetWindowRect($h, [ref]$r)) { return }

        $inside = ($r.Left -ge $scr.Left -and $r.Top -ge $scr.Top -and
                   $r.Right -le $scr.Right -and $r.Bottom -le $scr.Bottom)
        if ($inside) { return }

        [void][BphWin32]::SetWindowPos($h, [IntPtr]::Zero, $scr.Left, $scr.Top,
                                       $scr.Width, $scr.Height, 0x0040)
        Say ("fitted window to {0} display {1}x{2} at {3},{4}" -f $Display, $scr.Width, $scr.Height, $scr.Left, $scr.Top)
    } catch { }
}

# ---------------------------------------------------------------- dll

# The game keeps the mod assembly memory-mapped (PITFALLS E2), so the file lock
# is the only reliable "is it running" probe when process enumeration is blocked.
# BUT: Open(ReadWrite, None) also fails with UnauthorizedAccessException when the
# shell simply lacks write rights on the Steam folder (PITFALLS E20). A denied
# write is NOT a lock -- otherwise "bph status" claims the game is running when
# it is not, and "bph start" refuses to deploy.
function Test-DllLocked {
    if (-not (Test-Path $dllDst)) { return $false }
    try {
        $fs = [System.IO.File]::Open($dllDst, 'Open', 'ReadWrite', 'None')
        $fs.Close()
        return $false
    } catch [System.UnauthorizedAccessException] {
        # No write permission: cannot prove a lock either way. Deploy will fail
        # loudly if the file really is mapped; do not block start.
        return $false
    } catch {
        return $true
    }
}

function Get-DllInfo {
    if (-not (Test-Path $dllDst)) { return 'not deployed' }
    $f = Get-Item $dllDst
    return ("{0} bytes, {1:yyyy-MM-dd HH:mm:ss}" -f $f.Length, $f.LastWriteTime)
}

function Deploy-Dll {
    if (-not (Test-Path $dllSrc)) {
        Fail ("built dll not found: " + $dllSrc + " (run bridge\build.ps1 first)")
    }
    if (Test-DllLocked) {
        Fail 'dll is locked (game is still running); run "bph.cmd stop" first'
    }

    if (-not (Test-Path $mods)) { New-Item -ItemType Directory -Path $mods -Force | Out-Null }
    if (Test-Path $dllDst) { Copy-Item $dllDst ($dllDst + '.previous') -Force }

    Copy-Item $dllSrc $dllDst -Force
    Say ("deployed " + (Get-DllInfo))
}

function Test-DeployNeeded {
    if (-not (Test-Path $dllSrc)) { return $false }
    if (-not (Test-Path $dllDst)) { return $true }
    return ((Get-Item $dllSrc).LastWriteTimeUtc -gt (Get-Item $dllDst).LastWriteTimeUtc)
}

# ---------------------------------------------------------------- state

function Read-StateSummary {
    if (-not (Test-Path $state)) { return 'no state.json yet' }
    try {
        $j = Get-Content $state -Raw -Encoding UTF8 | ConvertFrom-Json
        $floor = $j.run.floor
        return ("seq={0} mode={1} floor={2}" -f $j.seq, $j.mode, $floor)
    } catch {
        return 'state.json not readable yet'
    }
}

function Wait-State([datetime]$afterUtc, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-Path $state) {
            if ((Get-Item $state).LastWriteTimeUtc -gt $afterUtc) {
                return $true
            }
        }
    }
    return $false
}

# ---------------------------------------------------------------- actions

function Do-Status {
    Say ("game dir : " + $GameDir)
    Say ("runtime  : " + $Runtime)

    $pids = Get-BphPids
    if ($pids.Count -gt 0) {
        Say ("process  : running (pid " + ($pids -join ', ') + ")")
    } else {
        Say 'process  : not running'
    }

    Say ("built dll: " + $(if (Test-Path $dllSrc) { "{0} bytes, {1:yyyy-MM-dd HH:mm:ss}" -f (Get-Item $dllSrc).Length, (Get-Item $dllSrc).LastWriteTime } else { 'missing' }))
    Say ("mods dll : " + (Get-DllInfo))
    Say ("dll lock : " + $(if (Test-DllLocked) { 'LOCKED (game running)' } else { 'free' }))
    Say ("state    : " + (Read-StateSummary))
    if (Test-Path $haltFile) { Say 'halt file: EXISTS (actions are blocked; start clears it)' }
}

function Do-Stop {
    $pids = Get-BphPids
    $locked = Test-DllLocked

    if ($pids.Count -eq 0 -and -not $locked) {
        Say 'game is not running'
        return
    }
    if ($pids.Count -eq 0 -and $locked) {
        Say 'process not visible from this shell, but the mod dll is locked -> still running'
        Say 'trying taskkill by image name ...'
        try { & taskkill.exe /IM "Backpack Hero.exe" /F 2>&1 | Out-Null } catch { }
        Start-Sleep -Seconds 3
        if (-not (Test-DllLocked)) {
            Say 'game stopped'
            return
        }
        Fail 'cannot stop the game from this shell (session/integrity). Close it manually.'
    }

    Say ("closing game (pid " + ($pids -join ', ') + ") ...")

    foreach ($p in $pids) {
        try {
            $proc = Get-Process -Id $p -ErrorAction Stop
            [void]$proc.CloseMainWindow()
        } catch { }
    }

    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if ((Get-BphPids).Count -eq 0 -and -not (Test-DllLocked)) { break }
        Start-Sleep -Milliseconds 400
    }

    $left = Get-BphPids
    if ($left.Count -gt 0 -or (Test-DllLocked)) {
        Say 'still alive, killing (taskkill /T /F)'
        foreach ($p in $left) {
            try { & taskkill.exe /PID $p /T /F 2>&1 | Out-Null } catch { }
        }
        if ($left.Count -eq 0) {
            try { & taskkill.exe /IM "Backpack Hero.exe" /F 2>&1 | Out-Null } catch { }
        }
        Start-Sleep -Seconds 3
    }

    if ((Test-DllLocked)) { Fail 'could not stop the game (dll still locked)' }
    Say 'game stopped'
}

function Do-Start {
    if (-not (Test-Path $exe)) { Fail ("game exe not found: " + $exe) }

    if (Test-DllLocked) {
        Fail 'game is already running (dll locked); run "bph.cmd stop" first'
    }

    if (-not $NoDeploy) {
        if (Test-DeployNeeded) {
            Deploy-Dll
        } else {
            Say 'dll up to date, skip deploy'
        }
    }

    if (Test-Path $haltFile) {
        try {
            Remove-Item $haltFile -Force
            Say 'cleared runtime\halt'
        } catch { }
    }

    $beforeUtc = [datetime]::MinValue
    if (Test-Path $state) { $beforeUtc = (Get-Item $state).LastWriteTimeUtc }

    Say 'launching game ...'
    $p = Start-Process -FilePath $exe -WorkingDirectory $GameDir -PassThru
    Say ("launched pid " + $p.Id)

    if (Wait-State $beforeUtc $WaitSec) {
        Say ("state    : " + (Read-StateSummary))
        Say 'bridge is writing state.json -- mod loaded'
    } else {
        Say ("WARNING: state.json did not change within " + $WaitSec + "s")
        Say 'check MelonLoader\Latest.log: mod may not have loaded'
    }

    Fit-BphWindow
}

function Do-Deploy {
    Deploy-Dll
}

# ---------------------------------------------------------------- main

switch ($Action) {
    'status'  { Do-Status }
    'stop'    { Do-Stop }
    'start'   { Do-Start }
    'restart' { Do-Stop; Do-Start }
    'deploy'  { Do-Deploy }
    'help'    {
        Say 'usage: bph.cmd [status|stop|start|restart|deploy] [-GameDir PATH] [-Elevate]'
        Say ''
        Say '  status   process / dll / state.json overview'
        Say '  stop     close the game (graceful, then kill)'
        Say '  start    deploy newest dll + launch + wait for state.json'
        Say '  restart  stop then start'
        Say '  deploy   copy the built dll only'
        Say ''
        Say '  -NoDeploy   start without touching the dll'
        Say '  -Elevate    relaunch through UAC (needed to write the Steam folder)'
        Say '  -KeepWindow do not reposition the game window'
    }
}
exit 0
