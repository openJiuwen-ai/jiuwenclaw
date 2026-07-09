<#
.SYNOPSIS
    dev.ps1 - JiuwenAvatar 一键开发环境启动脚本（Windows / PowerShell 版，对标 dev.sh）

.DESCRIPTION
    安装依赖 + 构建前端 + 启动后端(Gateway+AgentServer) + 启动前端(Vite)。
    启动前会清理占用开发端口的残留进程；等待 Gateway 就绪后再拉起前端，
    避免 Vite 代理在后端就绪前刷 ECONNREFUSED。

    注意：本文件使用 UTF-8 BOM 编码，以兼容 Windows PowerShell 5.1。

.PARAMETER SkipInstall   跳过依赖安装（uv sync / npm install）
.PARAMETER SkipBuild     跳过前端构建（npm run build）
.PARAMETER FrontendOnly  只启动前端 Vite dev server
.PARAMETER BackendOnly   只启动后端

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\dev.ps1
    powershell -ExecutionPolicy Bypass -File .\dev.ps1 -SkipInstall -SkipBuild
#>

[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipBuild,
    [switch]$FrontendOnly,
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$FrontendDir = Join-Path $ProjectRoot "jiuwenavatar\channels\web\frontend"

# 开发端口: 28092=AgentServer, 29000=Web Gateway, 29001=ACP/TUI Gateway, 29173=Vite 前端
$DevPorts = @(28092, 29000, 29001, 29173)

function Write-Step($msg)  { Write-Host $msg -ForegroundColor Yellow }
function Write-Info($msg)  { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host $msg -ForegroundColor Green }
function Write-Err($msg)   { Write-Host $msg -ForegroundColor Red }

function Get-PidsOnPort($port) {
    # 优先用 Get-NetTCPConnection，回退到 netstat 解析
    try {
        return @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        $lines = netstat -ano -p TCP 2>$null | Select-String ":$port\s+.*LISTENING"
        $pids = @()
        foreach ($l in $lines) {
            $parts = ($l.ToString() -split "\s+") | Where-Object { $_ -ne "" }
            if ($parts.Count -ge 5) { $pids += [int]$parts[-1] }
        }
        return ($pids | Select-Object -Unique)
    }
}

function Stop-StaleDevServices {
    foreach ($port in $DevPorts) {
        foreach ($procId in (Get-PidsOnPort $port)) {
            if (-not $procId -or $procId -eq 0) { continue }
            try {
                $p = Get-Process -Id $procId -ErrorAction Stop
                Write-Step "停止占用端口 $port 的进程 (pid $procId): $($p.ProcessName)"
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            } catch {
                # 进程可能已退出，忽略
            }
        }
    }
    Start-Sleep -Seconds 1
    # 第二轮强制清理仍未释放的端口
    foreach ($port in $DevPorts) {
        foreach ($procId in (Get-PidsOnPort $port)) {
            if (-not $procId -or $procId -eq 0) { continue }
            Write-Err "端口 $port 仍被占用 (pid $procId)，强制结束..."
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-ForGateway($port = 29000, $timeoutSec = 60) {
    Write-Info "等待 Gateway 在端口 $port 就绪..."
    $waited = 0
    while ($waited -lt $timeoutSec) {
        if ($script:BackendProc -and $script:BackendProc.HasExited) {
            Write-Err "[ERR] 后端进程已退出，启动失败"
            return $false
        }
        if ((Get-PidsOnPort $port).Count -gt 0) {
            Write-Ok "[OK] Gateway 已就绪 (端口 $port)"
            return $true
        }
        Start-Sleep -Seconds 1
        $waited++
    }
    Write-Step "[WARN] 等待 Gateway 超时 (${timeoutSec}s)，仍尝试启动前端"
    return $true
}

Write-Info "=========================================="
Write-Info "  JiuwenAvatar 开发环境一键启动 (Windows)"
Write-Info "=========================================="

# Step 1: 后端依赖
if (-not $SkipInstall) {
    Write-Step "[1/4] 安装后端依赖 (uv sync)..."
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv sync
        Write-Ok "[OK] 后端依赖安装完成"
    } else {
        Write-Err "[ERR] 未找到 uv，请先安装: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    }
} else {
    Write-Step "[1/4] 跳过后端依赖安装"
}

# Step 2: 前端依赖
if (-not $SkipInstall -and -not $BackendOnly) {
    Write-Step "[2/4] 安装前端依赖 (npm install)..."
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location $FrontendDir
        try {
            npm install
        } finally {
            Pop-Location
        }
        Write-Ok "[OK] 前端依赖安装完成"
    } else {
        Write-Err "[ERR] 未找到 npm，请先安装 Node.js (https://nodejs.org/)"
        exit 1
    }
} else {
    Write-Step "[2/4] 跳过前端依赖安装"
}

# Step 3: 构建前端
if (-not $SkipBuild -and -not $BackendOnly) {
    Write-Step "[3/4] 构建前端 (npm run build)..."
    Push-Location $FrontendDir
    try {
        npm run build
    } finally {
        Pop-Location
    }
    Write-Ok "[OK] 前端构建完成"
} else {
    Write-Step "[3/4] 跳过前端构建"
}

# 仅前端模式
if ($FrontendOnly) {
    Write-Info "[4/4] 启动前端开发服务器 (Vite)..."
    Write-Info "  前端地址: http://localhost:29173 (需 Gateway 运行在 29000)"
    Push-Location $FrontendDir
    try {
        npx vite --host
    } finally {
        Pop-Location
    }
    exit 0
}

Write-Info "[4/4] 启动后端服务 (jiuwenavatar.app)..."
Stop-StaleDevServices

# 仅后端模式
if ($BackendOnly) {
    uv run python -m jiuwenavatar.app
    exit 0
}

# 完整模式: 后端(后台) + 前端(前台)
$script:BackendProc = $null
try {
    Write-Info "启动后端..."
    $script:BackendProc = Start-Process -FilePath "uv" `
        -ArgumentList @("run", "python", "-m", "jiuwenavatar.app") `
        -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru

    if (-not (Wait-ForGateway 29000 60)) {
        exit 1
    }

    Write-Info "启动前端开发服务器..."
    Write-Ok  "=========================================="
    Write-Ok  "  JiuwenAvatar 开发环境已启动!"
    Write-Ok  "=========================================="
    Write-Info "  后端 (Gateway):  http://localhost:29000"
    Write-Info "  前端 (Vite HMR): http://localhost:29173"
    Write-Info "  按 Ctrl+C 停止所有服务"

    Push-Location $FrontendDir
    try {
        npx vite --host
    } finally {
        Pop-Location
    }
}
finally {
    Write-Step "正在停止所有服务..."
    if ($script:BackendProc -and -not $script:BackendProc.HasExited) {
        Stop-Process -Id $script:BackendProc.Id -Force -ErrorAction SilentlyContinue
    }
    # 兜底：清理可能残留的开发端口进程
    Stop-StaleDevServices
    Write-Ok "[OK] 所有服务已停止"
}
