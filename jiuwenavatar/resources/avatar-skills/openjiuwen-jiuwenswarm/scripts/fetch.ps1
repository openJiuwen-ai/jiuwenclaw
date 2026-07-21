# ---------------------------------------------------------------
# 按 reference 名拉取 openJiuwen jiuwenswarm 快照到 assets/<name>
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\fetch.ps1 -Tag 0.2.3
#   powershell -ExecutionPolicy Bypass -File scripts\fetch.ps1 -Tag enterprise_kub
#   powershell -ExecutionPolicy Bypass -File scripts\fetch.ps1 -Tag auto
#
# 参数:
#   -Tag    索引名（如 0.2.3 / enterprise_kub），或 auto
#   -Force  单 name 模式：目标目录非空时仍尝试克隆
#
# Git 源解析:
#   - 优先读取 references/<name>.md 顶部的 <!-- git-ref: <branch-or-tag> -->
#   - 若无注释，则用 <name> 本身作为 git branch/tag
#
# auto 模式:
#   - 扫描 references/[0-9]*.md；若存在 enterprise_kub.md 一并纳入
#   - assets/<name> 已存在则跳过；不存在则拉取
# ---------------------------------------------------------------

param(
    [string]$Tag,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://gitcode.com/openJiuwen/jiuwenswarm.git"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PackageRoot = Split-Path -Parent $ScriptDir
$AssetsRoot = Join-Path $PackageRoot "assets"
$ReferencesRoot = Join-Path $PackageRoot "references"

function Write-Info { param([string]$Message) Write-Host "[INFO]  $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "[OK]    $Message" -ForegroundColor Green }
function Write-Err  { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }
function Write-Skip { param([string]$Message) Write-Host "[SKIP]  $Message" -ForegroundColor DarkYellow }

function Test-GitAvailable {
    $null = Get-Command git -ErrorAction Stop
}

function Test-DirEmpty {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    $items = Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notin ".", ".." }
    return ($null -eq $items -or $items.Count -eq 0)
}

function Test-NameSafe {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if ($Value -match '\.\.|[\\/]') { return $false }
    return $Value -match '^[A-Za-z0-9._-]+$'
}

function Normalize-Name {
    param([string]$Value)
    $t = $Value.Trim()
    if ($t -match '^[vV]([0-9].*)$') {
        return $Matches[1]
    }
    return $t
}

function Resolve-GitRef {
    param([string]$Name)
    $refFile = Join-Path $ReferencesRoot "$Name.md"
    if (Test-Path -LiteralPath $refFile) {
        $line = Get-Content -LiteralPath $refFile -TotalCount 20 |
            Where-Object { $_ -match '^<!--\s*git-ref:\s*(\S+)\s*-->\s*$' } |
            Select-Object -First 1
        if ($line -match '^<!--\s*git-ref:\s*(\S+)\s*-->\s*$') {
            return $Matches[1]
        }
    }
    return $Name
}

function Get-ReferenceNames {
    if (-not (Test-Path -LiteralPath $ReferencesRoot)) {
        throw "references 目录不存在: $ReferencesRoot"
    }
    $names = @(
        Get-ChildItem -LiteralPath $ReferencesRoot -File |
            Where-Object { $_.Name -match '^[0-9].*\.md$' } |
            ForEach-Object { $_.BaseName } |
            Where-Object { Test-NameSafe -Value $_ }
    )
    $enterprise = Join-Path $ReferencesRoot "enterprise_kub.md"
    if (Test-Path -LiteralPath $enterprise) {
        $names += "enterprise_kub"
    }
    # Keep version-like names sorted; append special names at end without breaking version sort
    $versionNames = @($names | Where-Object { $_ -match '^[0-9]' } | Sort-Object {
        try { [version]($_ -replace '\.post(\d+)$', '.$1') } catch { $_ }
    })
    $otherNames = @($names | Where-Object { $_ -notmatch '^[0-9]' } | Sort-Object)
    return @($versionNames + $otherNames | Select-Object -Unique)
}

function Invoke-FetchName {
    param(
        [string]$Name,
        [switch]$SkipIfExists,
        [switch]$AllowForce
    )

    $targetDir = Join-Path $AssetsRoot $Name
    $gitRef = Resolve-GitRef -Name $Name

    Write-Info "索引名: $Name"
    Write-Info "Git 源: $gitRef"
    Write-Info "目标: $targetDir"

    if (Test-Path -LiteralPath $targetDir) {
        if ($SkipIfExists) {
            Write-Skip "$Name — assets 已存在，跳过"
            return @{ Status = "skipped" }
        }
        if (-not (Test-DirEmpty -Path $targetDir)) {
            if (-not $AllowForce -or -not $Force) {
                throw "目标目录已存在且非空: $targetDir。请删除后重试，或使用 -Force。"
            }
            Write-Info "目标目录非空，已指定 -Force，继续尝试克隆..."
        }
    }

    Write-Info "正在浅克隆 $gitRef ..."
    if (Test-Path -LiteralPath $targetDir) {
        Push-Location $targetDir
        try {
            & git clone --branch $gitRef --depth 1 --single-branch $RepoUrl .
        } finally {
            Pop-Location
        }
    } else {
        & git clone --branch $gitRef --depth 1 --single-branch $RepoUrl $targetDir
    }

    if ($LASTEXITCODE -ne 0) {
        throw "git clone 失败 (exit $LASTEXITCODE)"
    }

    Push-Location $targetDir
    try {
        $head = & git rev-parse --short HEAD 2>$null
        $describe = & git describe --tags --exact-match 2>$null
        if (-not $describe) { $describe = "(detached HEAD)" }
    } finally {
        Pop-Location
    }

    Write-Ok "已拉取 $Name ($gitRef) 到 $targetDir"
    Write-Ok "HEAD: $head  $describe"
    return @{ Status = "fetched"; Head = $head }
}

function Invoke-AutoFetch {
    $names = @(Get-ReferenceNames)
    if ($names.Count -eq 0) {
        Write-Err "references 中未找到可拉取索引，无法执行 auto"
        exit 1
    }

    Write-Info "auto 模式：自 references/ 发现 $($names.Count) 个索引"
    Write-Info "仓库: $RepoUrl"
    Write-Info "names: $($names -join ', ')"

    $fetched = 0
    $skipped = 0
    $failed = 0

    foreach ($n in $names) {
        Write-Host ""
        Write-Info "---- $n ----"
        try {
            $result = Invoke-FetchName -Name $n -SkipIfExists
            switch ($result.Status) {
                "skipped" { $skipped++ }
                "fetched" { $fetched++ }
            }
        } catch {
            $failed++
            Write-Err "$n — $($_.Exception.Message)"
        }
    }

    Write-Host ""
    Write-Info "auto 完成：拉取 $fetched，跳过 $skipped，失败 $failed"
    if ($failed -gt 0) { exit 1 }
}

if ([string]::IsNullOrWhiteSpace($Tag)) {
    $Tag = Read-Host "请输入索引名 (例如 0.2.3 / enterprise_kub) 或 auto"
}
$Tag = Normalize-Name $Tag

Test-GitAvailable

if (-not (Test-Path -LiteralPath $AssetsRoot)) {
    New-Item -ItemType Directory -Path $AssetsRoot -Force | Out-Null
}

if ($Tag -ieq "auto") {
    Invoke-AutoFetch
    exit 0
}

if (-not (Test-NameSafe -Value $Tag)) {
    Write-Err "无效的索引名: $Tag（仅允许字母数字 . _ -，不可含路径分隔符）"
    exit 1
}

Write-Info "仓库: $RepoUrl"
try {
    Invoke-FetchName -Name $Tag -AllowForce | Out-Null
} catch {
    Write-Err $_.Exception.Message
    exit 1
}
