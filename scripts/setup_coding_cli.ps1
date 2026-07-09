<#
.SYNOPSIS
    setup_coding_cli.ps1 - 检测并安装数字分身使用的编码引擎 CLI（Windows）

.DESCRIPTION
    由 jiuwenavatar 运行时在分身选择 claude-code / codex 且 CLI 缺失时自动调用，也可手动运行。
    国内环境默认使用 npm + 淘宝镜像。

.PARAMETER Target
    claude-code | codex | all | jiuwen-coding

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup_coding_cli.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\setup_coding_cli.ps1 claude-code
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = "claude-code"
)

$ErrorActionPreference = "Stop"

$NpmMirror = "https://registry.npmmirror.com"

function Write-Info { param([string]$Message) Write-Host "[INFO]  $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "[OK]    $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[WARN]  $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "[FAIL]  $Message" -ForegroundColor Red; exit 1 }

function Test-ChinaNetwork {
    if ($env:LANG -match 'zh' -or $env:LC_ALL -match 'zh') { return $true }
    try {
        $culture = [System.Globalization.CultureInfo]::CurrentUICulture.Name
        if ($culture -match '^zh') { return $true }
    } catch {
        # ignore
    }
    try {
        $resp = Invoke-WebRequest -Uri "https://claude.ai/install.sh" -Method Head -TimeoutSec 8 -UseBasicParsing
        $ct = $resp.Headers["Content-Type"]
        if ($ct -and $ct -match 'text/html') { return $true }
    } catch {
        return $true
    }
    return $false
}

function Update-NpmGlobalPath {
    $extra = @(
        (Join-Path $env:APPDATA "npm"),
        (Join-Path $env:LOCALAPPDATA "npm"),
        (Join-Path ${env:ProgramFiles} "nodejs")
    )
    try {
        $prefix = (npm config get prefix 2>$null).Trim()
        if ($prefix) {
            $extra += $prefix
            $extra += (Join-Path $prefix "bin")
        }
    } catch {
        # ignore
    }
    foreach ($dir in $extra) {
        if ($dir -and (Test-Path -LiteralPath $dir) -and ($env:PATH -notlike "*$dir*")) {
            $env:PATH = "$dir;$env:PATH"
        }
    }
}

function Get-CliCommand {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command "$Name.cmd" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Install-ClaudeCode {
    $existing = Get-CliCommand "claude"
    if ($existing) {
        $ver = & claude --version 2>$null
        Write-Ok "Claude Code 已安装: $($ver -join ' ')"
        return
    }

    Write-Warn "未检测到 Claude Code，开始安装..."
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Fail "需要 npm (Node.js 18+)，请先安装 Node.js: https://nodejs.org"
    }

    if (Test-ChinaNetwork) {
        Write-Warn "国内网络环境，使用 npm + 淘宝镜像"
        npm install -g @anthropic-ai/claude-code --registry=$NpmMirror
    } else {
        Write-Info "使用 npm 全局安装..."
        npm install -g @anthropic-ai/claude-code
    }

    Update-NpmGlobalPath
    $existing = Get-CliCommand "claude"
    if ($existing) {
        $ver = & claude --version 2>$null
        Write-Ok "Claude Code 安装成功: $($ver -join ' ')"
    } else {
        Write-Fail "安装后仍找不到 claude；可手动执行: npm install -g @anthropic-ai/claude-code --registry=$NpmMirror"
    }
}

function Install-Codex {
    $existing = Get-CliCommand "codex"
    if ($existing) {
        $ver = & codex --version 2>$null
        Write-Ok "Codex 已安装: $($ver -join ' ')"
        return
    }

    Write-Warn "未检测到 Codex，开始安装..."
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Fail "安装 Codex 需要 npm (Node.js 18+)，请先安装 Node.js: https://nodejs.org"
    }

    if (Test-ChinaNetwork) {
        Write-Warn "国内网络环境，使用淘宝镜像"
        npm install -g @openai/codex --registry=$NpmMirror
    } else {
        npm install -g @openai/codex
    }

    Update-NpmGlobalPath
    $existing = Get-CliCommand "codex"
    if ($existing) {
        $ver = & codex --version 2>$null
        Write-Ok "Codex 安装成功: $($ver -join ' ')"
    } else {
        Write-Fail "安装后仍找不到 codex；可手动执行: npm install -g @openai/codex"
    }
}

Write-Host "============================================================"
Write-Host "  setup_coding_cli.ps1 - 目标: $Target"
Write-Host "============================================================"

switch ($Target) {
    { $_ -in @("claude-code", "claude") } { Install-ClaudeCode }
    "codex" { Install-Codex }
    "all" {
        Install-ClaudeCode
        Install-Codex
    }
    "jiuwen-coding" {
        Write-Ok "jiuwen-coding 为原生后端，无需安装外部 CLI"
    }
    default {
        Write-Fail "未知目标: $Target (支持: claude-code | codex | all)"
    }
}

Write-Ok "完成"
