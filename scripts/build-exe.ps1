# JiuwenSwarm 打包 exe 脚本
# 用法: .\scripts\build-exe.ps1  或  pwsh -File scripts\build-exe.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "=== JiuwenSwarm Build Exe ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot`n" -ForegroundColor Gray

# 1. Install dependencies
Write-Host "[1/4] Installing Python dependencies (uv sync --extra dev)..." -ForegroundColor Yellow
uv sync --extra dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 2. Build frontend
Write-Host "`n[2/4] Building frontend (jiuwenclaw/web)..." -ForegroundColor Yellow
Push-Location (Join-Path $ProjectRoot "jiuwenclaw\web")
$WebDist = Join-Path $ProjectRoot "jiuwenclaw\web\dist"
if (Test-Path $WebDist) { Remove-Item $WebDist -Recurse -Force }
npm install
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

# 3. Run PyInstaller
Write-Host "`n[3/4] Running PyInstaller..." -ForegroundColor Yellow
uv run pyinstaller scripts\jiuwenclaw.spec --noconfirm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 4. Pack archive
Write-Host "`n[4/4] Packing archive..." -ForegroundColor Yellow
$ArchivePath = "$ProjectRoot\dist\jiuwenswarm.tar.gz"
if (Test-Path $ArchivePath) { Remove-Item $ArchivePath -Force }
tar -czf $ArchivePath -C "$ProjectRoot\dist\jiuwenswarm" .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Build complete ===" -ForegroundColor Green
Write-Host "Output dir: $ProjectRoot\dist\jiuwenswarm" -ForegroundColor Green
Write-Host "Executable: $ProjectRoot\dist\jiuwenswarm\jiuwenswarm.exe" -ForegroundColor Green
Write-Host "Archive: $ArchivePath" -ForegroundColor Green
