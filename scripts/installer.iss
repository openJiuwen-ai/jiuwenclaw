; Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
; Inno Setup installer script
; 仅由 scripts\build-exe.ps1 调用；wrapper 从 pyproject.toml 读取并传入构建配置。

#ifndef BuildDisplayName
  #error BuildDisplayName is required; run scripts\build-exe.ps1
#endif
#ifndef BuildVersion
  #error BuildVersion is required; run scripts\build-exe.ps1
#endif
#ifndef BuildExecutableNameWindows
  #error BuildExecutableNameWindows is required; run scripts\build-exe.ps1
#endif
#ifndef BuildDistDirName
  #error BuildDistDirName is required; run scripts\build-exe.ps1
#endif
#ifndef BuildSetupBaseName
  #error BuildSetupBaseName is required; run scripts\build-exe.ps1
#endif
#ifndef BuildWebView2InstallerPath
  #error BuildWebView2InstallerPath is required; run scripts\build-exe.ps1
#endif
#ifndef BuildWebView2InstallerFileName
  #error BuildWebView2InstallerFileName is required; run scripts\build-exe.ps1
#endif

#define MyAppName BuildDisplayName
#define MyAppVersion BuildVersion
#define MyAppPublisher "openJiuwen"
#define MyAppExeName BuildExecutableNameWindows
#define MyAppURL "https://openjiuwen.com"

[Setup]
AppId={{6DC96977-C194-44FE-812D-D4F0B576BD905}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename={#BuildSetupBaseName}
SetupIconFile=..\jiuwenswarm\channels\web\frontend\public\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Keep the legacy and current mutexes stable across installer AppId transitions.
; New frozen processes also create the product-derived mutexes.
AppMutex=JiuwenSwarm.App,Global\JiuwenSwarm.App,{#MyAppName}.App,Global\{#MyAppName}.App
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#BuildDistDirName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Keep the Microsoft-signed offline prerequisite inside Setup only. It is
; extracted to {tmp} solely when no usable Evergreen Runtime is registered.
; Keep it outside the main solid-compression stream so PrepareToInstall can
; reach the already-compressed installer without decoding the app payload.
Source: "{#BuildWebView2InstallerPath}"; Flags: dontcopy solidbreak nocompression

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--desktop-reset-external-cli-config"; Flags: runhidden waituntilterminated; RunOnceId: "ResetExternalCliConfig"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
; Remove only application-owned paths that may gain runtime-generated files.
; User configuration lives outside {app}; optional Windows runtimes live under {app}\runtime.
; The uninstall command resets external CLI switches before this directory is removed.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\runtime"
Type: files; Name: "{app}\{#MyAppExeName}"
Type: dirifempty; Name: "{app}"

[Run]
; 通过 Explorer 代启动程序，使安装完成后的启动上下文更接近桌面快捷方式启动
; postinstall 在安装向导最后一页显示运行应用复选框，由用户决定是否启动
Filename: "{win}\explorer.exe"; Parameters: """{app}\{#MyAppExeName}"""; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall

[Code]
const
  WebView2RuntimeId = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

var
  WebView2ProgressPage: TOutputMarqueeProgressWizardPage;

procedure InitializeWizard;
begin
  WebView2ProgressPage := CreateOutputMarqueeProgressPage(
    '正在准备 ' + '{#MyAppName}',
    '首次安装 Microsoft Edge WebView2 Runtime 可能需要 1–3 分钟，请勿关闭安装程序。'
  );
end;

function GetWebView2RuntimeVersion(var Version: String): Boolean;
var
  Key: String;
begin
  Key := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2RuntimeId;
  Result := RegQueryStringValue(HKLM32, Key, 'pv', Version) and
    (Trim(Version) <> '') and (CompareText(Trim(Version), '0.0.0.0') <> 0);

  if not Result then
    Result := RegQueryStringValue(HKCU, Key, 'pv', Version) and
      (Trim(Version) <> '') and (CompareText(Trim(Version), '0.0.0.0') <> 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RuntimeVersion: String;
  RuntimeInstaller: String;
  ResultCode: Integer;
  Executed: Boolean;
begin
  Result := '';

  if GetWebView2RuntimeVersion(RuntimeVersion) then
  begin
    Log('Microsoft Edge WebView2 Runtime is already installed: ' + RuntimeVersion);
    exit;
  end;

  Log('Microsoft Edge WebView2 Runtime is missing; running bundled installer.');
  WebView2ProgressPage.Show;
  try
    WebView2ProgressPage.SetText(
      '正在准备 Microsoft Edge WebView2 Runtime…',
      '即将开始安装运行环境。'
    );
    WebView2ProgressPage.Animate;
    ExtractTemporaryFile('{#BuildWebView2InstallerFileName}');
    RuntimeInstaller := ExpandConstant('{tmp}\{#BuildWebView2InstallerFileName}');

    WebView2ProgressPage.SetText(
      '正在安装 Microsoft Edge WebView2 Runtime…',
      '首次安装可能需要 1–3 分钟，请勿关闭安装程序。'
    );
    WebView2ProgressPage.Animate;
    ResultCode := -1;
    Executed := Exec(
      RuntimeInstaller,
      '/silent /install',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );
  finally
    WebView2ProgressPage.Hide;
  end;

  { The Evergreen installer can return a non-zero code even when registration
    succeeded. Verify Microsoft's documented pv value instead of trusting only
    the child exit code. }
  if GetWebView2RuntimeVersion(RuntimeVersion) then
  begin
    Log(
      'Microsoft Edge WebView2 Runtime installation verified: ' +
      RuntimeVersion + ' (installer exit code ' + IntToStr(ResultCode) + ')'
    );
    exit;
  end;

  if Executed then
    Log('WebView2 Runtime installer exit code: ' + IntToStr(ResultCode))
  else
    Log('WebView2 Runtime installer could not be started.');

  Result :=
    'Microsoft Edge WebView2 Runtime 安装失败。' + #13#10 + #13#10 +
    '{#MyAppName} 桌面端需要此运行环境。请检查系统策略或安全软件后重试安装。';
end;
