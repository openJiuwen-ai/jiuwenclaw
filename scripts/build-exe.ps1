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
Write-Host "`n[2/4] Building frontend (jiuwenswarm/channels/web/frontend)..." -ForegroundColor Yellow
Push-Location (Join-Path $ProjectRoot "jiuwenswarm\channels\web\frontend")
$WebDist = Join-Path $ProjectRoot "jiuwenswarm\channels\web\dist"
if (Test-Path $WebDist) { Remove-Item $WebDist -Recurse -Force }
npm install
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

# 3. Run PyInstaller
Write-Host "`n[3/4] Running PyInstaller..." -ForegroundColor Yellow
uv run pyinstaller scripts\jiuwenswarm.spec --noconfirm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 4. Build installer (Inno Setup)
Write-Host "`n[4/4] Building installer (Inno Setup)..." -ForegroundColor Yellow
$IsccPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$Iscc = $IsccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    $Iscc = Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
}
if (-not $Iscc) {
    Write-Host "Downloading Inno Setup 6..." -ForegroundColor Yellow
    $InnoUrl = "https://jrsoftware.org/download.php/is.exe"
    $InnoExe = "$env:TEMP\innosetup-6.7.1.exe"
    Invoke-WebRequest -Uri $InnoUrl -OutFile $InnoExe -UseBasicParsing
    Write-Host "Installing Inno Setup 6 (silent)..." -ForegroundColor Yellow
    Start-Process -FilePath $InnoExe -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-" -Wait -NoNewWindow
    $Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $Iscc)) {
        Write-Host "ERROR: Inno Setup installation failed" -ForegroundColor Red
        exit 1
    }
}
& $Iscc "$ProjectRoot\scripts\installer.iss"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$InstallerPath = (Get-ChildItem "$ProjectRoot\dist\JiuwenSwarm-setup-*.exe" | Select-Object -First 1).FullName

Write-Host "`n=== Build complete ===" -ForegroundColor Green
Write-Host "Installer: $InstallerPath" -ForegroundColor Green
Write-Host "Size: $([math]::Round((Get-Item $InstallerPath).Length / 1MB, 1)) MB" -ForegroundColor Green