# JiuwenSwarm 打包 exe 脚本
# 用法: .\scripts\build-exe.ps1  或  pwsh -File scripts\build-exe.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "=== JiuwenSwarm 打包 exe ===" -ForegroundColor Cyan
Write-Host "项目目录: $ProjectRoot`n" -ForegroundColor Gray

# 1. 安装依赖
Write-Host "[1/3] 安装 Python 依赖 (uv sync --extra dev)..." -ForegroundColor Yellow
uv sync --extra dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 2. 构建前端
Write-Host "`n[2/3] 构建前端 (jiuwenclaw/web)..." -ForegroundColor Yellow
Push-Location (Join-Path $ProjectRoot "jiuwenclaw\web")
$WebDist = Join-Path $ProjectRoot "jiuwenclaw\web\dist"
if (Test-Path $WebDist) { Remove-Item $WebDist -Recurse -Force }
npm install
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

# 3. 执行 PyInstaller 打包
Write-Host "`n[3/3] 执行 PyInstaller 打包..." -ForegroundColor Yellow
uv run pyinstaller scripts\jiuwenclaw.spec --noconfirm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 4. 打包 zip
Write-Host "`n[4/4] 打包 zip..." -ForegroundColor Yellow
$ZipPath = "$ProjectRoot\dist\jiuwenswarm.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path "$ProjectRoot\dist\jiuwenswarm\*" -DestinationPath $ZipPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== 打包完成 ===" -ForegroundColor Green
Write-Host "桌面版目录: $ProjectRoot\dist\jiuwenswarm" -ForegroundColor Green
Write-Host "主程序: $ProjectRoot\dist\jiuwenswarm\jiuwenswarm.exe" -ForegroundColor Green
Write-Host "发布包: $ZipPath" -ForegroundColor Green
