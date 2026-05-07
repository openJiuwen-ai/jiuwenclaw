#Requires -Version 5.1
<#
.SYNOPSIS
  将 jiuwenclaw 运行日志 (.logs) 与可选 session 目录打成 tar.gz（与 collect-jiuwenclaw-logs.sh 语义一致）。

.DESCRIPTION
  设计见 docs/design/jiuwenclaw日志采集脚本设计文档.md
  依赖: PowerShell 5.1+、Windows 10+ 自带 tar.exe（或 PATH 中的 tar）

.EXAMPLE
  cd scripts\collect_logs
  .\collect-jiuwenclaw-logs.ps1
  .\collect-jiuwenclaw-logs.ps1 -Base "$env:USERPROFILE\.office-claw\.jiuwenclaw\service_default" -Sessions 1-3 -Output C:\Temp
#>
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $RemainingArgs = @()
)

$ErrorActionPreference = 'Stop'

$ScriptVersion = '1.0.0'

$OptBase = ''
$OptService = 'service_default'
$OptAgent = 'agent_default'
$OptSessions = '1'
$OptOutput = '.'
$OptPrefix = 'jiuwenclaw-logs'
$script:OptDryRun = $false
$script:OptQuiet = $false

function Show-Help {
  @'
用法:
  collect-jiuwenclaw-logs.ps1 [选项]

将 {base}\.logs 下符合白名单的文件复制到包内 runtime_logs\；每个 session 仅复制根目录下 history.json、metadata.json。

选项（支持 -Name 与 --name）:
  -Base / --base DIR     基目录。未指定时: %USERPROFILE%\.office-claw\.jiuwenclaw\<service>
  -Service / --service   服务名，默认 service_default
  -Agent / --agent       agent 目录名，默认 agent_default
  -Sessions / --sessions  会话选择，默认 1（仅最新一个）
                         正整数 N、闭区间 N-M（须 N<=M，禁止 3-1）、all
  -Output / --output DIR 输出目录，默认当前目录
  -Prefix / --prefix STR 文件名前缀，默认 jiuwenclaw-logs
  -DryRun / --dry-run     仅列出将纳入的路径
  -Quiet / -q / --quiet  少输出
  -Help / -h / --help     帮助

说明:
  - Session 目录按 CreationTime（创建时间）新→旧编号，1 为最新。
  - 隐私白名单：.logs 仅 *.log；session 仅 history.json、metadata.json。
  - 无可拷贝的 session 文件时仍成功（只要 .logs 目录存在），包内含 SESSIONS_NOTE.txt。
'@ | Write-Output
}

function Write-Info([string]$Msg) {
  if (-not $script:OptQuiet) {
    Write-Host $Msg
  }
}

function Write-Warn([string]$Msg) {
  Write-Warning $Msg
}

function Write-Die([string]$Msg) {
  Write-Error $Msg -ErrorAction Continue
  exit 1
}

function Resolve-BasePath {
  if ($OptBase) {
    if (-not (Test-Path -LiteralPath $OptBase)) {
      Write-Die "基目录不存在: $OptBase"
    }
    return (Resolve-Path -LiteralPath $OptBase).Path
  }
  $userProfileRoot = [Environment]::GetFolderPath('UserProfile')
  $p = Join-Path $userProfileRoot ".office-claw\.jiuwenclaw\$OptService"
  if (-not (Test-Path -LiteralPath $p)) {
    Write-Die "默认基目录不存在: $p（请使用 -Base / --base 指定）"
  }
  return (Resolve-Path -LiteralPath $p).Path
}

function Get-SessionsSorted {
  param([string]$SessionsRoot)
  if (-not (Test-Path -LiteralPath $SessionsRoot -PathType Container)) {
    return @()
  }
  $dirs = @(Get-ChildItem -LiteralPath $SessionsRoot -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like 'officeclaw_*' })
  if ($dirs.Count -eq 0) { return @() }
  return @(
    $dirs | Sort-Object -Property CreationTime -Descending | ForEach-Object { $_.FullName }
  )
}

function Parse-SessionsSpec {
  param([string]$Spec)
  $s = $Spec.Trim()
  $lc = $s.ToLowerInvariant()
  if ($lc -eq 'all') {
    return @('ALL')
  }
  if ($s -match '^([0-9]+)-([0-9]+)$') {
    $a = [int]$Matches[1]
    $b = [int]$Matches[2]
    if ($a -gt $b) {
      Write-Die "非法区间 `"$Spec`"：序号须从小到大（新→旧编号），禁止逆序如 3-1"
    }
    $out = @()
    for ($i = $a; $i -le $b; $i++) { $out += $i }
    return @($out)
  }
  if ($s -match '^[0-9]+$') {
    return @([int]$s)
  }
  Write-Die "无法解析 --sessions `"$Spec`"（支持: 正整数、N-M、all）"
}

function Get-TarExecutable {
  $t = Get-Command tar.exe -ErrorAction SilentlyContinue
  if ($t) { return $t.Source }
  $t2 = Get-Command tar -ErrorAction SilentlyContinue
  if ($t2) { return $t2.Source }
  Write-Die "未找到 tar 可执行文件。请安装 Windows 10+ 自带 tar 或将其加入 PATH。"
}

function Normalize-ArgKey([string] $Raw) {
  $x = $Raw.Trim()
  while ($x.Length -gt 0 -and ($x[0] -eq '-' -or $x[0] -eq '/')) {
    $x = $x.Substring(1)
  }
  return $x.ToLowerInvariant()
}

# --- 参数解析（支持 -Name / --name / /name）---
$argv = @($RemainingArgs)
if ($argv.Count -eq 0 -and $args.Count -gt 0) {
  $argv = @($args)
}

$i = 0
while ($i -lt $argv.Count) {
  $a = $argv[$i]
  $key = Normalize-ArgKey -Raw $a
  if ($key -eq 'h' -or $key -eq 'help') {
    Show-Help
    exit 0
  }
  elseif ($key -eq 'q' -or $key -eq 'quiet') {
    $script:OptQuiet = $true
    $i++
  }
  elseif ($key -eq 'dry-run' -or $key -eq 'dryrun') {
    $script:OptDryRun = $true
    $i++
  }
  elseif ($key -eq 'base') {
    if ($i + 1 -ge $argv.Count) { Write-Die "--base 需要参数" }
    $script:OptBase = $argv[$i + 1]
    $i += 2
  }
  elseif ($key -eq 'service') {
    if ($i + 1 -ge $argv.Count) { Write-Die "--service 需要参数" }
    $script:OptService = $argv[$i + 1]
    $i += 2
  }
  elseif ($key -eq 'agent') {
    if ($i + 1 -ge $argv.Count) { Write-Die "--agent 需要参数" }
    $script:OptAgent = $argv[$i + 1]
    $i += 2
  }
  elseif ($key -eq 'sessions') {
    if ($i + 1 -ge $argv.Count) { Write-Die "--sessions 需要参数" }
    $script:OptSessions = $argv[$i + 1]
    $i += 2
  }
  elseif ($key -eq 'output') {
    if ($i + 1 -ge $argv.Count) { Write-Die "--output 需要参数" }
    $script:OptOutput = $argv[$i + 1]
    $i += 2
  }
  elseif ($key -eq 'prefix') {
    if ($i + 1 -ge $argv.Count) { Write-Die "--prefix 需要参数" }
    $script:OptPrefix = $argv[$i + 1]
    $i += 2
  }
  else {
    Write-Die "未知参数: $a（使用 -Help）"
  }
}

$Base = Resolve-BasePath
$LogsDir = Join-Path $Base '.logs'
$SessionsDir = Join-Path $Base (Join-Path $OptAgent (Join-Path 'agent' 'sessions'))

if (-not (Test-Path -LiteralPath $LogsDir -PathType Container)) {
  Write-Die "运行日志目录不存在: $LogsDir（请检查 -Base / -Service）"
}

$SessionList = @(Get-SessionsSorted -SessionsRoot $SessionsDir)
$TotalSessions = $SessionList.Count

$SessionAbsentReason = ''
if (-not (Test-Path -LiteralPath $SessionsDir -PathType Container)) {
  $SessionAbsentReason = "sessions 目录不存在: $SessionsDir"
}
elseif ($TotalSessions -eq 0) {
  $SessionAbsentReason = "sessions 目录下无 officeclaw_* 子目录: $SessionsDir"
}

$parsed = Parse-SessionsSpec -Spec $OptSessions
$SelectedSessionPaths = New-Object System.Collections.Generic.List[string]
# Parse-SessionsSpec 对 all 可能返回单元素数组或标量；统一用 @($parsed)[0] 判断
$firstTok = @($parsed)[0]
if ($firstTok -is [string] -and $firstTok -eq 'ALL') {
  foreach ($p in $SessionList) { [void]$SelectedSessionPaths.Add($p) }
}
else {
  $wanted = @($parsed | Sort-Object -Unique)
  $maxWanted = 0
  foreach ($n in $wanted) {
    if ($n -gt $maxWanted) { $maxWanted = $n }
  }
  if ($maxWanted -gt 0 -and $TotalSessions -gt 0 -and $maxWanted -gt $TotalSessions) {
    Write-Warn "请求的 session 序号最大为 $maxWanted，当前仅有 $TotalSessions 个；将只打包存在的序号。"
  }
  foreach ($idx in $wanted) {
    if ($idx -ge 1) {
      $arrIdx = $idx - 1
      if ($arrIdx -lt $TotalSessions) {
        [void]$SelectedSessionPaths.Add($SessionList[$arrIdx])
      }
    }
  }
}

if ($script:OptDryRun) {
  Write-Info "BASE=$Base"
  Write-Info "LOGS_DIR=$LogsDir"
  Write-Info '--- runtime_logs（仅 *.log，最多 200 条）---'
  Get-ChildItem -LiteralPath $LogsDir -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -ieq '.log' } |
    Select-Object -ExpandProperty FullName -First 200 |
    ForEach-Object { Write-Info $_ }
  $fc = @(Get-ChildItem -LiteralPath $LogsDir -Recurse -File -Force -ErrorAction SilentlyContinue |
      Where-Object { $_.Extension -ieq '.log' }).Count
  Write-Info "(符合白名单的 .log 文件数: $fc)"
  Write-Info '--- sessions（选中目录；打包时仅 history.json / metadata.json）---'
  if ($SelectedSessionPaths.Count -eq 0) {
    Write-Info '(无)'
  }
  else {
    $di = 1
    foreach ($pp in $SelectedSessionPaths) {
      Write-Info "$di $pp"
      $di++
    }
  }
  exit 0
}

$StageDir = Join-Path $env:TEMP ("jiuwenclaw-collect-" + [Guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

try {
  $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
  $bundleName = "${OptPrefix}_${ts}"
  $BundleRoot = Join-Path $StageDir $bundleName
  New-Item -ItemType Directory -Path $BundleRoot -Force | Out-Null

  $RuntimeDest = Join-Path $BundleRoot 'runtime_logs'
  New-Item -ItemType Directory -Path $RuntimeDest -Force | Out-Null

  try {
    $logsItem = Get-Item -LiteralPath $LogsDir
    $logsRootFull = $logsItem.FullName.TrimEnd('\', '/')
    Get-ChildItem -LiteralPath $LogsDir -Recurse -File -Force -ErrorAction Stop |
      Where-Object { $_.Extension -ieq '.log' } |
      ForEach-Object {
        $rel = $_.FullName.Substring($logsRootFull.Length).TrimStart('\', '/')
        $destFile = Join-Path $RuntimeDest $rel
        $destDir = Split-Path -Parent $destFile
        if (-not (Test-Path -LiteralPath $destDir)) {
          New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $_.FullName -Destination $destFile -Force
      }
  }
  catch {
    Write-Die "复制运行日志失败: $LogsDir — $($_.Exception.Message)"
  }

  $IncludeSessionCount = 0
  $SessionLinesForManifest = New-Object System.Text.StringBuilder

  if ($SelectedSessionPaths.Count -gt 0) {
    foreach ($sessPath in $SelectedSessionPaths) {
      if (-not (Test-Path -LiteralPath $sessPath -PathType Container)) { continue }
      $bn = Split-Path -Leaf $sessPath
      $sessOut = Join-Path (Join-Path $BundleRoot 'sessions') $bn
      $sessCopied = $false
      foreach ($jf in @('history.json', 'metadata.json')) {
        $srcJ = Join-Path $sessPath $jf
        if (Test-Path -LiteralPath $srcJ -PathType Leaf) {
          if (-not (Test-Path -LiteralPath $sessOut)) {
            New-Item -ItemType Directory -Path $sessOut -Force | Out-Null
          }
          Copy-Item -LiteralPath $srcJ -Destination (Join-Path $sessOut $jf) -Force
          $sessCopied = $true
        }
      }
      if ($sessCopied) {
        $IncludeSessionCount++
        [void]$SessionLinesForManifest.AppendLine("  - $sessPath")
      }
    }
  }

  $NotePath = Join-Path $BundleRoot 'SESSIONS_NOTE.txt'
  if ($IncludeSessionCount -eq 0) {
    $noteSb = New-Object System.Text.StringBuilder
    [void]$noteSb.AppendLine("本次归档未包含任何 session 目录数据。")
    [void]$noteSb.AppendLine()
    if ($SessionAbsentReason) {
      [void]$noteSb.AppendLine("原因: $SessionAbsentReason")
    }
    else {
      [void]$noteSb.AppendLine("原因: 按 --sessions=$OptSessions 筛选后没有可用的 session（可能序号超出当前数量）。")
      [void]$noteSb.AppendLine("当前按创建时间新→旧共检测到 $TotalSessions 个 officeclaw_* 目录。")
    }
    [void]$noteSb.AppendLine()
    [void]$noteSb.AppendLine("运行日志仍已按设计打包在 runtime_logs/ 下。")
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($NotePath, $noteSb.ToString(), $utf8NoBom)
  }
  else {
    Remove-Item -LiteralPath $NotePath -Force -ErrorAction SilentlyContinue
  }

  $manifestSb = New-Object System.Text.StringBuilder
  [void]$manifestSb.AppendLine('jiuwenclaw 日志采集 MANIFEST')
  [void]$manifestSb.AppendLine("script_version: $ScriptVersion")
  [void]$manifestSb.AppendLine('privacy_logs_whitelist: *.log files only (case-insensitive)')
  [void]$manifestSb.AppendLine('privacy_session_files: history.json, metadata.json at session dir root only')
  [void]$manifestSb.AppendLine('session_sort: CreationTime (fallback: LastWriteTime if unavailable)')
  [void]$manifestSb.AppendLine('collector: PowerShell')
  [void]$manifestSb.AppendLine("created_local: $(Get-Date -Format o)")
  [void]$manifestSb.AppendLine("base: $Base")
  [void]$manifestSb.AppendLine("logs_dir: $LogsDir")
  [void]$manifestSb.AppendLine("sessions_dir: $SessionsDir")
  [void]$manifestSb.AppendLine("sessions_spec: $OptSessions")
  [void]$manifestSb.AppendLine("sessions_detected: $TotalSessions")
  [void]$manifestSb.AppendLine("sessions_included: $IncludeSessionCount")
  [void]$manifestSb.AppendLine()
  [void]$manifestSb.AppendLine('session_dirs_sorted_newest_first:')
  $si = 1
  foreach ($sp in $SessionList) {
    [void]$manifestSb.AppendLine("  $si $sp")
    $si++
  }
  [void]$manifestSb.AppendLine()
  [void]$manifestSb.AppendLine('session_dirs_included:')
  if ($IncludeSessionCount -eq 0) {
    [void]$manifestSb.AppendLine('  (none)')
  }
  else {
    [void]$manifestSb.Append($SessionLinesForManifest.ToString())
  }

  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText((Join-Path $BundleRoot 'MANIFEST.txt'), $manifestSb.ToString(), $utf8)

  $archiveName = "${bundleName}.tar.gz"
  if (-not (Test-Path -LiteralPath $OptOutput)) {
    New-Item -ItemType Directory -Path $OptOutput -Force | Out-Null
  }
  $outDir = (Resolve-Path -LiteralPath $OptOutput).Path
  $archivePath = Join-Path $outDir $archiveName

  $tarExe = Get-TarExecutable
  Write-Info "正在打包: $archivePath"
  Push-Location $StageDir
  try {
    & $tarExe -czf $archivePath $bundleName
    if ($LASTEXITCODE -ne 0) {
      Write-Die "tar 打包失败，退出码: $LASTEXITCODE"
    }
  }
  finally {
    Pop-Location
  }

  Write-Info "完成: $archivePath"
  Write-Info "包含 session 目录数: $IncludeSessionCount"
}
finally {
  if ($StageDir -and (Test-Path -LiteralPath $StageDir)) {
    Remove-Item -LiteralPath $StageDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

exit 0
