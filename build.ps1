# JiuwenClaw 打包脚本
# 1. 编译前端 (jiuwenclaw/web)
# 2. 构建 wheel 包（包含前端 dist）

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Write-Host "[build] 项目根目录: $ProjectRoot" -ForegroundColor Cyan

# 1. 编译前端
$WebDir = Join-Path (Join-Path $ProjectRoot "jiuwenclaw") "web"
if (-not (Test-Path $WebDir)) {
    Write-Error "前端目录不存在: $WebDir"
}

Write-Host "[build] 正在编译前端..." -ForegroundColor Yellow
Push-Location $WebDir
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "[build] 安装 npm 依赖..." -ForegroundColor Gray
        npm install
    }
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "前端编译失败"
    }
} finally {
    Pop-Location
}

$DistDir = Join-Path $WebDir "dist"
if (-not (Test-Path $DistDir)) {
    Write-Error "前端编译输出不存在: $DistDir"
}
Write-Host "[build] 前端编译完成: $DistDir" -ForegroundColor Green

# 临时移走 node_modules，避免被打包进 wheel
$NodeModules = Join-Path $WebDir "node_modules"
$NodeModulesBak = Join-Path $WebDir "node_modules.bak"
$NodeModulesMoved = $false
if (Test-Path $NodeModules) {
    Write-Host "[build] 临时移走 node_modules 以减小 wheel 体积..." -ForegroundColor Gray
    Move-Item $NodeModules $NodeModulesBak -Force
    $NodeModulesMoved = $true
}

# 创建符号链接，让 workspace 和入口脚本在 jiuwenclaw 包内
$SymlinksRemoved = @()
$JiuwenclawDir = Join-Path $ProjectRoot "jiuwenclaw"

$WorkspaceLink = Join-Path $JiuwenclawDir "workspace"
if (-not (Test-Path $WorkspaceLink)) {
    Write-Host "[build] 创建 workspace 符号链接..." -ForegroundColor Gray
    $WorkspaceSource = Join-Path $ProjectRoot "workspace"
    New-Item -ItemType SymbolicLink -Path $WorkspaceLink -Target $WorkspaceSource | Out-Null
    $SymlinksRemoved += $WorkspaceLink
}

# 创建入口脚本的符号链接
$AppLink = Join-Path $JiuwenclawDir "app.py"
if (-not (Test-Path $AppLink)) {
    Write-Host "[build] 创建 app.py 符号链接..." -ForegroundColor Gray
    $AppSource = Join-Path $ProjectRoot "app.py"
    New-Item -ItemType SymbolicLink -Path $AppLink -Target $AppSource | Out-Null
    $SymlinksRemoved += $AppLink
}

$AppWebLink = Join-Path $JiuwenclawDir "app_web.py"
if (-not (Test-Path $AppWebLink)) {
    Write-Host "[build] 创建 app_web.py 符号链接..." -ForegroundColor Gray
    $AppWebSource = Join-Path $ProjectRoot "app_web.py"
    New-Item -ItemType SymbolicLink -Path $AppWebLink -Target $AppWebSource | Out-Null
    $SymlinksRemoved += $AppWebLink
}

$StartServicesLink = Join-Path $JiuwenclawDir "start_services.py"
if (-not (Test-Path $StartServicesLink)) {
    Write-Host "[build] 创建 start_services.py 符号链接..." -ForegroundColor Gray
    $StartServicesSource = Join-Path $ProjectRoot "start_services.py"
    New-Item -ItemType SymbolicLink -Path $StartServicesLink -Target $StartServicesSource | Out-Null
    $SymlinksRemoved += $StartServicesLink
}

try {
# 2. 构建 wheel
Write-Host "[build] 正在构建 wheel 包..." -ForegroundColor Yellow
Push-Location $ProjectRoot
try {
    python -m pip install --upgrade build wheel 2>$null
    python -m build --wheel
    if ($LASTEXITCODE -ne 0) {
        throw "wheel 构建失败"
    }
} finally {
    Pop-Location
}

# 确保 dist 目录存在
$DistOutput = Join-Path $ProjectRoot "dist"
if (-not (Test-Path $DistOutput)) {
    New-Item -ItemType Directory -Path $DistOutput -Force | Out-Null
    Write-Host "[build] 创建 dist 目录: $DistOutput" -ForegroundColor Gray
}
Write-Host "[build] 完成! wheel 包位于: $DistOutput" -ForegroundColor Green
Get-ChildItem $DistOutput -Filter "*.whl" | ForEach-Object { Write-Host "  - $($_.Name)" }
} finally {
    # 清理符号链接
    foreach ($link in $SymlinksRemoved) {
        if (Test-Path $link -PathType Any) {
            Remove-Item $link -Force -Recurse
            Write-Host "[build] 已删除符号链接: $link" -ForegroundColor Gray
        }
    }

    # 恢复 node_modules
    if ($NodeModulesMoved -and (Test-Path $NodeModulesBak)) {
        Move-Item $NodeModulesBak $NodeModules -Force
        Write-Host "[build] 已恢复 node_modules" -ForegroundColor Gray
    }
}
