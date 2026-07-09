# Stop the brainstorm server and clean up
# Usage: .\stop-server.ps1 -SessionDir <session_dir>
#
# PowerShell equivalent of stop-server.sh.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionDir
)

$StateDir = Join-Path $SessionDir "state"
$PidFile = Join-Path $StateDir "server.pid"

function Resolve-ActiveFile {
    param([string]$Path)
    if ($Path -match '\\\.brainstorm\\|^/.+\/\.brainstorm\/') {
        $root = $Path -replace '[\\/]\.brainstorm[\\/].*$', ''
        return Join-Path $root ".brainstorm\active.json"
    }
    if ($Path -like "$($env:TEMP)\brainstorm-*" -or $Path -like "/tmp/brainstorm-*") {
        if ($IsLinux -or $IsMacOS) {
            return "/tmp/brainstorm-active.json"
        }
        return Join-Path $env:TEMP "brainstorm-active.json"
    }
    return $null
}

function Clear-ActiveFile {
    param([string]$Path)
    $activeFile = Resolve-ActiveFile -Path $Path
    if (-not $activeFile -or -not (Test-Path $activeFile)) { return }
    try {
        $current = Get-Content $activeFile -Raw | ConvertFrom-Json
        if ($current.session_dir -eq $Path) {
            Remove-Item $activeFile -Force
        }
    } catch {
        Remove-Item $activeFile -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $PidFile)) {
    Write-Output '{"status": "not_running"}'
    exit 0
}

$pidValue = (Get-Content $PidFile -Raw).Trim()

try {
    Stop-Process -Id $pidValue -ErrorAction Stop
} catch {
    # process may already be gone
}

for ($i = 0; $i -lt 20; $i++) {
    if (-not (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 100
}

if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
    Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 100
}

if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
    Write-Output '{"status": "failed", "error": "process still running"}'
    exit 1
}

Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
$logFile = Join-Path $StateDir "server.log"
Remove-Item $logFile -Force -ErrorAction SilentlyContinue
Clear-ActiveFile -Path $SessionDir

$isEphemeral = ($SessionDir -like "$($env:TEMP)\brainstorm-*") -or ($SessionDir -like "/tmp/brainstorm-*")
if ($isEphemeral) {
    Remove-Item $SessionDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output '{"status": "stopped"}'
