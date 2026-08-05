# JiuwenSwarm 打包 exe 脚本
# 用法: .\scripts\build-exe.ps1  或  pwsh -File scripts\build-exe.ps1

param(
    [string]$NodeDir = "",
    [string]$WebView2RuntimeDir = ""
)

$ErrorActionPreference = "Stop"

# 控制台 UTF-8，避免中文 echo 乱码（PowerShell 5.1 默认编码易乱码）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 项目根 = 脚本所在目录的上一层，基于脚本自身位置推导，换路径不坏
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$BundleNode = if ($env:BUNDLE_NODE) { $env:BUNDLE_NODE } else { "1" }
$NodeVersion = if ($env:NODE_VERSION) { $env:NODE_VERSION } else { "v22.11.0" }
$NodeSource = $null

function Test-Truthy {
    param([string]$Value)

    $normalized = $Value.Trim().ToLowerInvariant()
    return $normalized -in @("1", "true", "yes", "on")
}

function Test-WebView2RuntimeDir {
    param([string]$RuntimeDir)

    if (-not (Test-Path -LiteralPath $RuntimeDir -PathType Container)) {
        return $false
    }
    foreach ($requiredFile in @("msedgewebview2.exe", "msedge.dll")) {
        if (-not (Test-Path -LiteralPath (Join-Path $RuntimeDir $requiredFile) -PathType Leaf)) {
            return $false
        }
    }
    return $true
}

function Find-InstalledWebView2Runtime {
    $programFilesX86 = ${env:ProgramFiles(x86)}
    $roots = @()
    if ($programFilesX86) {
        $roots += Join-Path $programFilesX86 "Microsoft\EdgeWebView\Application"
    }
    if ($env:ProgramFiles) {
        $roots += Join-Path $env:ProgramFiles "Microsoft\EdgeWebView\Application"
    }
    if ($env:LOCALAPPDATA) {
        $roots += Join-Path $env:LOCALAPPDATA "Microsoft\EdgeWebView\Application"
    }
    $roots = $roots | Where-Object { Test-Path -LiteralPath $_ -PathType Container }

    foreach ($root in $roots) {
        $versions = Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Sort-Object -Property Name -Descending
        foreach ($versionDir in $versions) {
            if (Test-WebView2RuntimeDir -RuntimeDir $versionDir.FullName) {
                return $versionDir.FullName
            }
        }
    }
    return $null
}

function Get-NodeArch {
    $arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
    if ($arch -eq [System.Runtime.InteropServices.Architecture]::Arm64) {
        return "arm64"
    }
    return "x64"
}

function Download-NodeRuntime {
    param(
        [string]$ProjectRoot,
        [string]$NodeVersion
    )

    $arch = Get-NodeArch
    $nodeName = "node-$NodeVersion-win-$arch"
    $nodeUrl = "https://nodejs.org/dist/$NodeVersion/$nodeName.zip"
    $vendorRoot = Join-Path $ProjectRoot "vendor"
    $target = Join-Path $vendorRoot "node"
    $downloadDir = Join-Path $ProjectRoot ".build\node-download"
    $zipPath = Join-Path $downloadDir "$nodeName.zip"

    Write-Host "[runtime] Downloading Node.js $NodeVersion ($arch)..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $vendorRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
    Invoke-WebRequest -Uri $nodeUrl -OutFile $zipPath -UseBasicParsing

    $extractRoot = Join-Path $downloadDir "extract"
    if (Test-Path -LiteralPath $extractRoot) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force

    $extracted = Join-Path $extractRoot $nodeName
    if (-not (Test-Path -LiteralPath (Join-Path $extracted "node.exe"))) {
        throw "Downloaded Node archive does not contain node.exe: $nodeUrl"
    }

    if (Test-Path -LiteralPath $target) {
        $projectResolved = (Resolve-Path -LiteralPath $ProjectRoot).Path
        $targetResolved = (Resolve-Path -LiteralPath $target).Path
        if (-not $targetResolved.StartsWith($projectResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove Node cache outside project: $targetResolved"
        }
        Remove-Item -LiteralPath $targetResolved -Recurse -Force
    }
    Move-Item -LiteralPath $extracted -Destination $target
    return (Resolve-Path -LiteralPath $target).Path
}

function Resolve-NodeRuntimeDir {
    param(
        [string]$ProjectRoot,
        [string]$ExplicitNodeDir,
        [string]$NodeVersion
    )

    if ($ExplicitNodeDir) {
        $resolved = (Resolve-Path -LiteralPath $ExplicitNodeDir -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath (Join-Path $resolved "node.exe"))) {
            throw "NodeDir must contain node.exe: $resolved"
        }
        return $resolved
    }

    if ($env:NODE_DIR) {
        $resolved = (Resolve-Path -LiteralPath $env:NODE_DIR -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath (Join-Path $resolved "node.exe"))) {
            throw "NODE_DIR must contain node.exe: $resolved"
        }
        return $resolved
    }

    $vendorNode = Join-Path $ProjectRoot "vendor\node"
    if (Test-Path -LiteralPath (Join-Path $vendorNode "node.exe")) {
        return (Resolve-Path -LiteralPath $vendorNode).Path
    }

    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        return Split-Path -Parent $nodeCommand.Source
    }

    return Download-NodeRuntime -ProjectRoot $ProjectRoot -NodeVersion $NodeVersion
}

function Use-NodeRuntime {
    param([string]$SourceDir)

    if (-not $SourceDir) {
        return
    }
    $env:PATH = "$SourceDir$([System.IO.Path]::PathSeparator)$env:PATH"
}

function Resolve-WebView2RuntimeDir {
    param([string]$ExplicitPath)

    $defaultPath = Join-Path $ProjectRoot "vendor\webview2-fixed"
    if ($ExplicitPath) {
        $candidate = $ExplicitPath
    } elseif ($env:WEBVIEW2_RUNTIME_DIR) {
        $candidate = $env:WEBVIEW2_RUNTIME_DIR
    } else {
        $candidate = Find-InstalledWebView2Runtime
        if ($candidate) {
            Write-Host "[runtime] Found installed WebView2 Runtime: $candidate" -ForegroundColor Green
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        $candidate = $defaultPath
    }

    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
        throw "WebView2 Runtime not found. Install WebView2 on the build machine, or pass -WebView2RuntimeDir."
    }
    $resolved = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
    if (-not (Test-WebView2RuntimeDir -RuntimeDir $resolved)) {
        throw "WebView2 Runtime directory is incomplete: $resolved"
    }

    return $resolved
}

function Copy-WebView2Runtime {
    param(
        [string]$SourceDir,
        [string]$DistDir
    )

    $distResolved = (Resolve-Path -LiteralPath $DistDir -ErrorAction Stop).Path
    $runtimeDir = Join-Path $distResolved "runtime"
    $target = Join-Path $runtimeDir "webview2"

    if (Test-Path -LiteralPath $target) {
        $targetResolved = (Resolve-Path -LiteralPath $target).Path
        if (-not $targetResolved.StartsWith($distResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove WebView2 runtime outside dist: $targetResolved"
        }
        Remove-Item -LiteralPath $targetResolved -Recurse -Force
    }

    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Get-ChildItem -LiteralPath $SourceDir -Force | Copy-Item -Destination $target -Recurse -Force

    foreach ($requiredFile in @("msedgewebview2.exe", "msedge.dll")) {
        if (-not (Test-Path -LiteralPath (Join-Path $target $requiredFile) -PathType Leaf)) {
            throw "Copied WebView2 Runtime is missing ${requiredFile}: $target"
        }
    }

    $sizeMb = [math]::Round(
        ((Get-ChildItem -LiteralPath $target -Recurse -File | Measure-Object -Property Length -Sum).Sum) / 1MB,
        1
    )
    Write-Host "[runtime] Fixed WebView2 Runtime copied to $target ($sizeMb MB)" -ForegroundColor Green
}

function Copy-NodeRuntime {
    param(
        [string]$SourceDir,
        [string]$DistDir
    )

    if (-not $SourceDir) {
        return
    }

    $distResolved = (Resolve-Path -LiteralPath $DistDir -ErrorAction Stop).Path
    $runtimeDir = Join-Path $distResolved "runtime"
    $target = Join-Path $runtimeDir "node-runtime"
    if (Test-Path -LiteralPath $target) {
        $targetResolved = (Resolve-Path -LiteralPath $target).Path
        if (-not $targetResolved.StartsWith($distResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove Node runtime outside dist: $targetResolved"
        }
        Remove-Item -LiteralPath $targetResolved -Recurse -Force
    }
    New-Item -ItemType Directory -Path $target -Force | Out-Null

    $files = @("node.exe", "npm.cmd", "npx.cmd", "corepack.cmd", "nodevars.bat")
    foreach ($file in $files) {
        $source = Join-Path $SourceDir $file
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $target -Force
        }
    }

    $npmModules = Join-Path $SourceDir "node_modules\npm"
    if (Test-Path -LiteralPath $npmModules) {
        $modulesTarget = Join-Path $target "node_modules"
        New-Item -ItemType Directory -Path $modulesTarget -Force | Out-Null
        Copy-Item -LiteralPath $npmModules -Destination $modulesTarget -Recurse -Force
    }

    if (-not (Test-Path -LiteralPath (Join-Path $target "npx.cmd"))) {
        throw "Bundled Node runtime is missing npx.cmd: $target"
    }

    $nodeVersion = & (Join-Path $target "node.exe") --version
    Write-Host "[runtime] Bundled Node $nodeVersion into $target" -ForegroundColor Green
}

$WebView2RuntimeSource = Resolve-WebView2RuntimeDir -ExplicitPath $WebView2RuntimeDir

if (Test-Truthy $BundleNode) {
    $NodeSource = Resolve-NodeRuntimeDir `
        -ProjectRoot $ProjectRoot `
        -ExplicitNodeDir $NodeDir `
        -NodeVersion $NodeVersion
    Use-NodeRuntime -SourceDir $NodeSource
}

Write-Host "=== JiuwenSwarm Build Exe ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot`n" -ForegroundColor Gray

# 1. Install dependencies
Write-Host "[1/5] Installing Python dependencies (uv sync --extra dev)..." -ForegroundColor Yellow
uv sync --extra dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 2. Build frontend
Write-Host "`n[2/5] Building frontend (jiuwenswarm/channels/web/frontend)..." -ForegroundColor Yellow
Push-Location (Join-Path $ProjectRoot "jiuwenswarm\channels\web\frontend")
$WebDist = Join-Path $ProjectRoot "jiuwenswarm\channels\web\dist"
if (Test-Path $WebDist) { Remove-Item $WebDist -Recurse -Force }
if (Test-Path "node_modules") {
    Write-Host "[build] node_modules exists, skip npm install" -ForegroundColor Gray
} else {
    Write-Host "[build] node_modules missing, running npm install..." -ForegroundColor Gray
    npm install
    if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
}
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

# 3. Run PyInstaller
Write-Host "`n[3/5] Running PyInstaller..." -ForegroundColor Yellow
uv run pyinstaller scripts\jiuwenswarm.spec --noconfirm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 3.5 Bundle Node.js runtime for browser tools
if (Test-Truthy $BundleNode) {
    Write-Host "`n[3.5/5] Bundling Node.js runtime..." -ForegroundColor Yellow
    $DistDir = Join-Path $ProjectRoot "dist\jiuwenswarm"
    Copy-NodeRuntime -SourceDir $NodeSource -DistDir $DistDir
} else {
    Write-Host "`n[3.5/5] Skipping bundled Node.js runtime (BUNDLE_NODE=$BundleNode)" -ForegroundColor Yellow
}

# 4. Bundle Fixed Version WebView2 Runtime
Write-Host "`n[4/5] Bundling Fixed Version WebView2 Runtime..." -ForegroundColor Yellow
$DistDir = Join-Path $ProjectRoot "dist\jiuwenswarm"
Copy-WebView2Runtime -SourceDir $WebView2RuntimeSource -DistDir $DistDir

# Verify the actual frozen runtime, including bundled WebView2 resources.
$FrozenExe = Join-Path $ProjectRoot "dist\jiuwenswarm\jiuwenswarm.exe"
$A2UIVerifier = Join-Path $ProjectRoot "scripts\verify_a2ui_bundle.py"
$VerifyProcess = Start-Process `
    -FilePath $FrozenExe `
    -ArgumentList @($A2UIVerifier) `
    -Wait `
    -PassThru `
    -NoNewWindow
if ($VerifyProcess.ExitCode -ne 0) {
    throw "Frozen A2UI bundle verification failed. See ~/.jiuwenswarm/logs/jiuwenswarm_exe_error.log"
}

# 5. Build installer (Inno Setup)
Write-Host "`n[5/5] Building installer (Inno Setup)..." -ForegroundColor Yellow
$IsccPaths = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
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
    Start-Process `
        -FilePath $InnoExe `
        -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-" `
        -Wait `
        -NoNewWindow
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
