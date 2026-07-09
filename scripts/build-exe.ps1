# JiuwenAvatar 打包 exe 脚本
# 用法: .\scripts\build-exe.ps1  或  pwsh -File scripts\build-exe.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "=== JiuwenAvatar Build Exe (Windows Desktop) ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot`n" -ForegroundColor Gray

# 1. Install dependencies (dev extra includes pyinstaller + pywebview + pystray)
Write-Host "[1/5] Installing Python dependencies..." -ForegroundColor Yellow
uv sync --extra dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 2. Build frontend
Write-Host "`n[2/5] Building frontend..." -ForegroundColor Yellow
Push-Location (Join-Path $ProjectRoot "jiuwenavatar\channels\web\frontend")
$PublicDir = Join-Path $ProjectRoot "jiuwenavatar\channels\web\frontend\public"
$WebDist = Join-Path $ProjectRoot "jiuwenavatar\channels\web\frontend\dist"

if (-not (Test-Path (Join-Path $PublicDir "jiuwen_avatar.png"))) {
    Write-Host "ERROR: Missing public/jiuwen_avatar.png (brand logo)." -ForegroundColor Red
    Pop-Location
    exit 1
}

Write-Host "  Regenerating transparent brand PNG + logo.ico..." -ForegroundColor Gray
uv run python (Join-Path $ProjectRoot "scripts\regenerate_brand_icons.py")
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }

if (-not (Test-Path (Join-Path $PublicDir "logo.ico"))) {
    Write-Host "ERROR: Missing public/logo.ico. Generate it from jiuwen_avatar.png first." -ForegroundColor Red
    Pop-Location
    exit 1
}

if (Test-Path $WebDist) { Remove-Item $WebDist -Recurse -Force }
npm install
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }

# Ensure brand logos are present in dist (tray / floating widget / favicon)
foreach ($logoFile in @("jiuwen_avatar.png", "logo.ico")) {
    $src = Join-Path $PublicDir $logoFile
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $WebDist $logoFile) -Force
        Write-Host "  Synced brand asset: $logoFile" -ForegroundColor Gray
    }
}
Pop-Location

# 3. Clean previous dist
Write-Host "`n[3/5] Cleaning previous build..." -ForegroundColor Yellow
$DistDir = Join-Path $ProjectRoot "dist\jiuwenavatar"
if (Test-Path $DistDir) { Remove-Item $DistDir -Recurse -Force }

# 4. Run PyInstaller
Write-Host "`n[4/5] Running PyInstaller (--clean --noconfirm)..." -ForegroundColor Yellow
uv run pyinstaller scripts\jiuwenavatar.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Verify output
$ExePath = Join-Path $ProjectRoot "dist\jiuwenavatar\jiuwenavatar.exe"
if (-not (Test-Path $ExePath)) {
    Write-Host "ERROR: jiuwenavatar.exe not found at: $ExePath" -ForegroundColor Red
    exit 1
}
$ExeSize = (Get-Item $ExePath).Length
Write-Host "  jiuwenavatar.exe: $([math]::Round($ExeSize / 1MB, 1)) MB" -ForegroundColor Gray

# 5. Build installer (Inno Setup)
Write-Host "`n[5/5] Building installer (Inno Setup)..." -ForegroundColor Yellow
$IsccPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
)
$Iscc = $IsccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    $Iscc = Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
}
$InnoDownloadFailed = $false
if (-not $Iscc) {
    Write-Host "  Inno Setup 6 not found. Downloading..." -ForegroundColor Yellow
    $InnoUrl = "https://jrsoftware.org/download.php/is.exe"
    $InnoExe = "$env:TEMP\innosetup-6.exe"
    try {
        Invoke-WebRequest -Uri $InnoUrl -OutFile $InnoExe -UseBasicParsing -ErrorAction Stop
        Write-Host "  Installing Inno Setup 6 (silent)..." -ForegroundColor Yellow
        Start-Process -FilePath $InnoExe -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-" -Wait -NoNewWindow
        $Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        if (-not (Test-Path $Iscc)) {
            Write-Host "  Trying alternative path..." -ForegroundColor Yellow
            $Iscc = "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
        }
    } catch {
        $InnoDownloadFailed = $true
        Write-Host "  WARNING: Failed to install Inno Setup. Installer not built." -ForegroundColor Yellow
        Write-Host "  Download manually from: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    }
}
if ($Iscc -and (Test-Path $Iscc)) {
    # Read version from pyproject.toml
    $Version = uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Iscc "/DMyAppVersion=$Version" "$ProjectRoot\scripts\installer.iss"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Iscc "/DMyAppVersion=$Version" "$ProjectRoot\scripts\jiuwenavatar.iss"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} elseif (-not $InnoDownloadFailed) {
    Write-Host "  WARNING: ISCC.exe not found. Skipping installer build." -ForegroundColor Yellow
}

$InstallerPath = Get-ChildItem "$ProjectRoot\dist\JiuwenAvatar-setup-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
$PortableDir = Join-Path $ProjectRoot "dist\jiuwenavatar"

Write-Host "`n=== Build complete ===" -ForegroundColor Green
if ($InstallerPath) {
    Write-Host "Installer: $($InstallerPath.FullName)" -ForegroundColor Green
    Write-Host "Size: $([math]::Round($InstallerPath.Length / 1MB, 1)) MB" -ForegroundColor Green
}
Write-Host "Portable: $PortableDir" -ForegroundColor Green
Write-Host "  jiuwenavatar.exe size: $([math]::Round($ExeSize / 1MB, 1)) MB" -ForegroundColor Gray