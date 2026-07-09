# ---------------------------------------------------------------
# 按 tag 拉取 openJiuwen agent-runtime 快照到 assets/<tag>
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\fetch.ps1 -Tag v0.1.0
#   powershell -ExecutionPolicy Bypass -File scripts\fetch.ps1 -Tag auto
#   powershell -ExecutionPolicy Bypass -File scripts\fetch.ps1   # 交互输入 tag / auto
#
# 参数:
#   -Tag    tag 名（如 v0.1.0），或 auto（扫描 references/v*.md 批量拉取）
#
# auto 模式:
#   - 从 references/ 读取 v*.md（版本索引），文件名（去 .md）即 tag
#   - 忽略 references/ 下非 v*.md 的补充文档（如 runtime-sdk-notes.md）
#   -Force  单 tag 模式：目标目录非空时仍尝试克隆
# ---------------------------------------------------------------

param(
    [string]$Tag,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://gitcode.com/openJiuwen/agent-runtime.git"

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

function Test-TagSafe {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return $Value -notmatch '[\\/]|\.\.'
}

function Normalize-Tag {
    param([string]$Value)
    $t = $Value.Trim()
    if ($t -match '^(?<ver>\d+\.\d+\.\d+(?:[.-][0-9A-Za-z.+-]*)?)$') {
        return "v$($Matches['ver'])"
    }
    return $t
}

function Get-ReferenceTags {
    if (-not (Test-Path -LiteralPath $ReferencesRoot)) {
        throw "references 目录不存在: $ReferencesRoot"
    }
    $tags = Get-ChildItem -LiteralPath $ReferencesRoot -File -Filter "v*.md" |
        ForEach-Object { $_.BaseName } |
        Where-Object { Test-TagSafe -Value $_ } |
        Sort-Object { [version]($_.TrimStart('v')) }
    return ,$tags
}

function Invoke-FetchTag {
    param(
        [string]$TagName,
        [switch]$SkipIfExists,
        [switch]$AllowForce
    )

    $targetDir = Join-Path $AssetsRoot $TagName

    Write-Info "标签: $TagName"
    Write-Info "目标: $targetDir"

    if (Test-Path -LiteralPath $targetDir) {
        if ($SkipIfExists) {
            Write-Skip "$TagName — assets 已存在，跳过"
            return @{ Status = "skipped" }
        }
        if (-not (Test-DirEmpty -Path $targetDir)) {
            if (-not $AllowForce -or -not $Force) {
                throw "目标目录已存在且非空: $targetDir。请删除后重试，或使用 -Force。"
            }
            Write-Info "目标目录非空，已指定 -Force，继续尝试克隆..."
        }
    }

    Write-Info "正在浅克隆 tag $TagName ..."
    if (Test-Path -LiteralPath $targetDir) {
        Push-Location $targetDir
        try {
            & git clone --branch $TagName --depth 1 --single-branch $RepoUrl .
        } finally {
            Pop-Location
        }
    } else {
        & git clone --branch $TagName --depth 1 --single-branch $RepoUrl $targetDir
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

    Write-Ok "已拉取 $TagName 到 $targetDir"
    Write-Ok "HEAD: $head  $describe"
    return @{ Status = "fetched"; Head = $head }
}

function Invoke-AutoFetch {
    $tags = @(Get-ReferenceTags)
    if ($tags.Count -eq 0) {
        Write-Err "references 中未找到 v*.md 版本索引，无法执行 auto"
        exit 1
    }

    Write-Info "auto 模式：自 references/ 发现 $($tags.Count) 个 tag"
    Write-Info "仓库: $RepoUrl"
    Write-Info "tags: $($tags -join ', ')"

    $fetched = 0
    $skipped = 0
    $failed = 0

    foreach ($t in $tags) {
        Write-Host ""
        Write-Info "---- $t ----"
        try {
            $result = Invoke-FetchTag -TagName $t -SkipIfExists
            switch ($result.Status) {
                "skipped" { $skipped++ }
                "fetched" { $fetched++ }
            }
        } catch {
            $failed++
            Write-Err "$t — $($_.Exception.Message)"
        }
    }

    Write-Host ""
    Write-Info "auto 完成：拉取 $fetched，跳过 $skipped，失败 $failed"
    if ($failed -gt 0) { exit 1 }
}

if ([string]::IsNullOrWhiteSpace($Tag)) {
    $Tag = Read-Host "请输入 tag (例如 v0.1.0) 或 auto"
}
$Tag = Normalize-Tag $Tag

Test-GitAvailable

if (-not (Test-Path -LiteralPath $AssetsRoot)) {
    New-Item -ItemType Directory -Path $AssetsRoot -Force | Out-Null
}

if ($Tag -ieq "auto") {
    Invoke-AutoFetch
    exit 0
}

if (-not (Test-TagSafe -Value $Tag)) {
    Write-Err "无效的 tag: $Tag（不可包含路径分隔符或 ..）"
    exit 1
}

Write-Info "仓库: $RepoUrl"
try {
    Invoke-FetchTag -TagName $Tag -AllowForce | Out-Null
} catch {
    Write-Err $_.Exception.Message
    exit 1
}
