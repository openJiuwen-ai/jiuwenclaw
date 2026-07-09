# Start the brainstorm server and output connection info
# Usage: .\start-server.ps1 [-ProjectDir <path>] [-BindHost <host>] [-UrlHost <host>] [-Foreground] [-Background]
#
# PowerShell equivalent of start-server.sh for Windows default shells.

[CmdletBinding()]
param(
    [string]$ProjectDir = "",
    [string]$BindHost = "127.0.0.1",
    [string]$UrlHost = "",
    [switch]$Foreground,
    [switch]$Background
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $UrlHost) {
    if ($BindHost -in @("127.0.0.1", "localhost")) {
        $UrlHost = "localhost"
    } else {
        $UrlHost = $BindHost
    }
}

$RunForeground = $Foreground.IsPresent
if (-not $RunForeground -and -not $Background.IsPresent -and $env:CODEX_CI) {
    $RunForeground = $true
}

$SessionId = "$PID-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"

if ($ProjectDir) {
    $BrainstormRoot = Join-Path $ProjectDir ".brainstorm"
    New-Item -ItemType Directory -Force -Path $BrainstormRoot | Out-Null
    $SessionDir = Join-Path $BrainstormRoot $SessionId
    $ActiveFile = Join-Path $BrainstormRoot "active.json"
} else {
    $SessionDir = Join-Path $env:TEMP "brainstorm-$SessionId"
    $ActiveFile = Join-Path $env:TEMP "brainstorm-active.json"
}

$StateDir = Join-Path $SessionDir "state"
$ContentDir = Join-Path $SessionDir "content"
$PidFile = Join-Path $StateDir "server.pid"
$LogFile = Join-Path $StateDir "server.log"

New-Item -ItemType Directory -Force -Path $ContentDir, $StateDir | Out-Null

function Read-ActiveSessionDir {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return "" }
    try {
        $data = Get-Content $Path -Raw | ConvertFrom-Json
        return [string]$data.session_dir
    } catch {
        return ""
    }
}

function Stop-ActiveSession {
    param([string]$Path)
    $oldSession = Read-ActiveSessionDir -Path $Path
    if ($oldSession -and (Test-Path $oldSession)) {
        & (Join-Path $ScriptDir "stop-server.ps1") -SessionDir $oldSession | Out-Null
    }
}

function New-AuthToken {
    if (Get-Command node -ErrorAction SilentlyContinue) {
        return (& node -e "console.log(require('crypto').randomBytes(16).toString('hex'))").Trim()
    }
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

$AuthToken = New-AuthToken
Stop-ActiveSession -Path $ActiveFile

$OwnerPid = $PID
try {
    $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop).ParentProcessId
    if ($parent) { $OwnerPid = $parent }
} catch {}

$env:BRAINSTORM_DIR = $SessionDir
$env:BRAINSTORM_HOST = $BindHost
$env:BRAINSTORM_URL_HOST = $UrlHost
$env:BRAINSTORM_OWNER_PID = "$OwnerPid"
$env:BRAINSTORM_TOKEN = $AuthToken
$env:BRAINSTORM_ACTIVE_FILE = $ActiveFile

Push-Location $ScriptDir
try {
    if ($RunForeground) {
        & node server.cjs
        exit $LASTEXITCODE
    }

    $proc = Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "node server.cjs >> `"$LogFile`" 2>&1" `
        -WorkingDirectory $ScriptDir `
        -PassThru `
        -WindowStyle Hidden

    for ($i = 0; $i -lt 50; $i++) {
        Start-Sleep -Milliseconds 100
        if ((Test-Path $PidFile) -and (Test-Path $LogFile) -and (Select-String -Path $LogFile -Pattern "server-started" -Quiet)) {
            $serverPid = Get-Content $PidFile -Raw
            $alive = $true
            for ($j = 0; $j -lt 20; $j++) {
                if (-not (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
                    $alive = $false
                    break
                }
                Start-Sleep -Milliseconds 100
            }
            if (-not $alive) {
                $retry = ".\start-server.ps1"
                if ($ProjectDir) { $retry += " -ProjectDir `"$ProjectDir`"" }
                $retry += " -BindHost $BindHost -UrlHost $UrlHost -Foreground"
                Write-Output "{`"error`": `"Server started but was killed. Retry with: $retry`"}"
                exit 1
            }
            Get-Content $LogFile | Select-String "server-started" | Select-Object -First 1 | ForEach-Object { $_.Line }
            exit 0
        }
    }

    Write-Output '{"error": "Server failed to start within 5 seconds"}'
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    exit 1
} finally {
    Pop-Location
}
