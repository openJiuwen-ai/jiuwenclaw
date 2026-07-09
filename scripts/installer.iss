; JiuwenAvatar Inno Setup Installer Script
; Windows 桌面版一键安装包
; 用法: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" scripts\installer.iss

#define MyAppName "JiuwenAvatar"
// MyAppVersion must be passed via /DMyAppVersion="X.Y.Z" at compile time.
// Manual compilation (without /D) will fail — use build-exe.bat instead.
#ifndef MyAppVersion
#error "MyAppVersion not defined. Use build-exe.bat or pass /DMyAppVersion=..."
#endif
#define MyAppPublisher "openJiuwen"
#define MyAppExeName "jiuwenavatar.exe"
#define MyAppURL "https://openjiuwen.com"
#define MyAppId "E7A4C291-5F3B-4D8E-A6C2-1B9E0F4D7A3E"

[Setup]
AppId={{E7A4C291-5F3B-4D8E-A6C2-1B9E0F4D7A3E}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppContact=support@openjiuwen.com
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 始终显示「选择安装位置」页（admin 模式下默认 auto 会在升级时跳过该页）
DisableDirPage=no
; 不用 Inno 按 AppId 记忆的上次目录（旧 AppId 与 JiuwenSwarm 冲突时会填错路径）
UsePreviousAppDir=no
AlwaysShowDirOnReadyPage=yes
OutputDir=..\dist
OutputBaseFilename=JiuwenAvatar-setup-{#MyAppVersion}
SetupIconFile=..\jiuwenavatar\channels\web\frontend\public\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
UsedUserAreasWarning=no
[Languages]
; 默认 Inno Setup 安装包不含简体中文，使用项目内置语言文件
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
; 升级安装确认（%1=应用名 %2=已装版本 %3=安装路径 %4=新版本）
chinesesimplified.UpgradeConfirm=检测到您的计算机上已安装 %1 %2。%n%n当前安装路径：%3%n%n安装程序将把程序升级至版本 %4。%n为避免多实例和快捷方式混乱，升级/重装必须安装到原路径；附加任务（桌面快捷方式、开机启动）可重新勾选。%n%n是否继续？
english.UpgradeConfirm=Setup detected %1 %2 on this computer.%n%nCurrent location:%n%3%n%nThe installer will upgrade to version %4.%nTo avoid duplicate installs and shortcut conflicts, reinstall/upgrade must use the existing install folder. Optional tasks (desktop shortcut, startup) can be re-selected.%n%nContinue?
chinesesimplified.InstallDirMismatch=检测到已安装的 JiuwenAvatar 位于：%n%1%n%n重新安装或升级必须使用原安装路径，不能安装到：%n%2
english.InstallDirMismatch=An existing JiuwenAvatar installation was detected at:%n%1%n%nReinstall or upgrade must use the existing install folder, not:%n%2
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkablealone
Name: "startupicon"; Description: "开机自动启动 JiuwenAvatar"; GroupDescription: "启动选项："; Flags: checkablealone unchecked

[Files]
Source: "..\dist\jiuwenavatar\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; WebView2 运行时安装程序（如用户尚未安装）
; Source: "..\scripts\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not IsWebView2Installed

[Dirs]
Name: "{userappdata}\.jiuwenavatar"; Flags: uninsalwaysuninstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: startupicon

[Run]
; 安装/升级后同步内置 skill 到用户目录（仅合并已安装的内置 skill，保留用户运行时数据）
Filename: "{app}\{#MyAppExeName}"; Parameters: "--sync-builtin-skills"; StatusMsg: "正在同步内置技能..."; Flags: runhidden waituntilterminated
; shellexec 让程序通过 ShellExecute 启动，正确处理 UAC 权限请求
; postinstall 在安装向导最后一页显示"运行 JiuwenAvatar"复选框，由用户决定是否启动
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--uninstall-silent"; Flags: runhidden; RunOnceId: "SilentUninstall"

[Code]
const
  // Inno writes AppId={{GUID}} uninstall keys as "{GUID}}_is1" (note the
  // doubled closing brace). Keep a fallback for hand-written/older keys.
  UninstallRegSubKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}}_is1';
  UninstallRegSubKeyCompat = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';
  // 与父项目 JiuwenSwarm 曾共用的旧 AppId，仅用于升级检测（须配合 exe 校验）
  LegacyUninstallRegSubKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B8F3A2D1-7E4C-4A9B-8D6F-1C2E3F4A5B6C}_is1';
  // 旧 jiuwenavatar.iss / JiuwenClaw 安装包 AppId，用户可能从该安装包升级到主安装包。
  JiuwenClawUninstallRegSubKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6DDF1C96-B2CE-4A2F-A7E7-A2E8627AE0A2}}_is1';
  JiuwenClawUninstallRegSubKeyCompat = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6DDF1C96-B2CE-4A2F-A7E7-A2E8627AE0A2}_is1';

// 检测 WebView2 是否已安装
function IsWebView2Installed: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00FB3A3A6E4E}') or
            RegKeyExists(HKCU32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00FB3A3A6E4E}') or
            RegKeyExists(HKCU64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00FB3A3A6E4E}');
end;

function QueryUninstallStringValue(RootKey: Integer; Name: String; var Value: String): Boolean;
begin
  Result := RegQueryStringValue(RootKey, UninstallRegSubKey, Name, Value);
end;

function QueryUninstallStringValueAt(RootKey: Integer; const SubKey: String; Name: String; var Value: String): Boolean;
begin
  Result := RegQueryStringValue(RootKey, SubKey, Name, Value);
end;

function FileDirFromQuotedCommand(const Command: String): String;
var
  S, ExePath: String;
  EndQuote: Integer;
begin
  Result := '';
  S := Trim(Command);
  if S = '' then
    Exit;

  if Copy(S, 1, 1) = '"' then
  begin
    Delete(S, 1, 1);
    EndQuote := Pos('"', S);
    if EndQuote > 0 then
      ExePath := Copy(S, 1, EndQuote - 1)
    else
      Exit;
  end
  else
  begin
    EndQuote := Pos('.exe', Lowercase(S));
    if EndQuote > 0 then
      ExePath := Copy(S, 1, EndQuote + 3)
    else
      Exit;
  end;

  Result := RemoveBackslash(ExtractFileDir(ExePath));
end;

function ResolveInstallLocationFromUninstallKey(RootKey: Integer; const SubKey: String): String;
var
  Value: String;
begin
  Result := '';
  if QueryUninstallStringValueAt(RootKey, SubKey, 'InstallLocation', Value) then
    Result := RemoveBackslash(Value);
  if Result <> '' then
    Exit;

  if QueryUninstallStringValueAt(RootKey, SubKey, 'DisplayIcon', Value) then
    Result := RemoveBackslash(ExtractFileDir(Value));
  if Result <> '' then
    Exit;

  if QueryUninstallStringValueAt(RootKey, SubKey, 'UninstallString', Value) then
    Result := FileDirFromQuotedCommand(Value);
end;

// 仅当卸载项对应目录下存在 jiuwenavatar.exe 时才视为本产品的已装实例，
// 避免与父项目 JiuwenSwarm（可能共用旧 AppId 或同名注册项）误判为升级。
function IsJiuwenAvatarInstallAt(RootKey: Integer; const SubKey: String): Boolean;
var
  InstallLoc: String;
begin
  Result := False;
  if not RegKeyExists(RootKey, SubKey) then
    Exit;
  InstallLoc := ResolveInstallLocationFromUninstallKey(RootKey, SubKey);
  if InstallLoc = '' then
    Exit;
  Result := FileExists(InstallLoc + '\{#MyAppExeName}');
end;

function IsJiuwenAvatarInstall(RootKey: Integer): Boolean;
begin
  Result := IsJiuwenAvatarInstallAt(RootKey, UninstallRegSubKey) or
            IsJiuwenAvatarInstallAt(RootKey, UninstallRegSubKeyCompat) or
            IsJiuwenAvatarInstallAt(RootKey, LegacyUninstallRegSubKey);
end;

var
  DetectedUninstallRegSubKey: String;

function LooksLikeJiuwenAvatarUninstallEntry(RootKey: Integer; const SubKey: String): Boolean;
var
  DisplayName, InstallLoc: String;
begin
  Result := False;
  DisplayName := '';
  QueryUninstallStringValueAt(RootKey, SubKey, 'DisplayName', DisplayName);
  if Pos('jiuwenavatar', Lowercase(DisplayName)) = 0 then
    Exit;

  InstallLoc := ResolveInstallLocationFromUninstallKey(RootKey, SubKey);
  if InstallLoc = '' then
    Exit;
  Result := FileExists(InstallLoc + '\{#MyAppExeName}');
end;

function TryExistingInstallRootByScan(Rk: Integer; var OutRootKey: Integer): Boolean;
var
  Names: TArrayOfString;
  I: Integer;
  SubKey: String;
begin
  Result := False;
  if not RegGetSubkeyNames(Rk, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Names) then
    Exit;

  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    SubKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Names[I];
    if LooksLikeJiuwenAvatarUninstallEntry(Rk, SubKey) then
    begin
      OutRootKey := Rk;
      DetectedUninstallRegSubKey := SubKey;
      Result := True;
      Exit;
    end;
  end;
end;

function TryExistingInstallRoot(Rk: Integer; var OutRootKey: Integer): Boolean;
begin
  Result := False;
  if IsJiuwenAvatarInstallAt(Rk, UninstallRegSubKey) then
  begin
    OutRootKey := Rk;
    DetectedUninstallRegSubKey := UninstallRegSubKey;
    Result := True;
    Exit;
  end;
  if IsJiuwenAvatarInstallAt(Rk, UninstallRegSubKeyCompat) then
  begin
    OutRootKey := Rk;
    DetectedUninstallRegSubKey := UninstallRegSubKeyCompat;
    Result := True;
    Exit;
  end;
  if IsJiuwenAvatarInstallAt(Rk, LegacyUninstallRegSubKey) then
  begin
    OutRootKey := Rk;
    DetectedUninstallRegSubKey := LegacyUninstallRegSubKey;
    Result := True;
    Exit;
  end;
  if IsJiuwenAvatarInstallAt(Rk, JiuwenClawUninstallRegSubKey) then
  begin
    OutRootKey := Rk;
    DetectedUninstallRegSubKey := JiuwenClawUninstallRegSubKey;
    Result := True;
    Exit;
  end;
  if IsJiuwenAvatarInstallAt(Rk, JiuwenClawUninstallRegSubKeyCompat) then
  begin
    OutRootKey := Rk;
    DetectedUninstallRegSubKey := JiuwenClawUninstallRegSubKeyCompat;
    Result := True;
  end;
end;

function GetExistingInstallRoot(var RootKey: Integer): Boolean;
begin
  Result := False;
  DetectedUninstallRegSubKey := UninstallRegSubKey;
  if TryExistingInstallRoot(HKLM64, RootKey) then
    Result := True
  else if TryExistingInstallRoot(HKLM32, RootKey) then
    Result := True
  else if TryExistingInstallRoot(HKCU64, RootKey) then
    Result := True
  else if TryExistingInstallRoot(HKCU32, RootKey) then
    Result := True
  else if TryExistingInstallRootByScan(HKLM64, RootKey) then
    Result := True
  else if TryExistingInstallRootByScan(HKLM32, RootKey) then
    Result := True
  else if TryExistingInstallRootByScan(HKCU64, RootKey) then
    Result := True
  else if TryExistingInstallRootByScan(HKCU32, RootKey) then
    Result := True;
end;

function GetExistingInstallPath(): String;
var
  RootKey: Integer;
begin
  Result := '';
  if GetExistingInstallRoot(RootKey) then
    Result := ResolveInstallLocationFromUninstallKey(RootKey, DetectedUninstallRegSubKey);
end;

function GetExistingVersion(): String;
var
  RootKey: Integer;
begin
  Result := '';
  if GetExistingInstallRoot(RootKey) then
    QueryUninstallStringValueAt(RootKey, DetectedUninstallRegSubKey, 'DisplayVersion', Result);
end;

function GetExistingDisplayName(): String;
var
  RootKey: Integer;
begin
  Result := ExpandConstant('{#MyAppName}');
  if GetExistingInstallRoot(RootKey) then
    if not QueryUninstallStringValueAt(RootKey, DetectedUninstallRegSubKey, 'DisplayName', Result) then
      Result := ExpandConstant('{#MyAppName}');
end;

function GetPreferredInstallDir(): String;
var
  Existing: String;
begin
  Existing := RemoveBackslash(GetExistingInstallPath());
  if (Existing <> '') and FileExists(Existing + '\{#MyAppExeName}') then
    Result := Existing
  else
    Result := ExpandConstant('{autopf}\{#MyAppName}');
end;

function ShouldUseInstallPath(const Path: String): Boolean;
var
  P: String;
begin
  P := RemoveBackslash(Path);
  if P = '' then
  begin
    Result := False;
    Exit;
  end;
  Result := FileExists(P + '\{#MyAppExeName}');
end;

function GetLockedExistingInstallPath(): String;
var
  Existing: String;
begin
  Existing := RemoveBackslash(GetExistingInstallPath());
  if (Existing <> '') and FileExists(Existing + '\{#MyAppExeName}') then
    Result := Existing
  else
    Result := '';
end;

function SameInstallPath(const Left, Right: String): Boolean;
begin
  Result := CompareText(RemoveBackslash(ExpandConstant(Left)), RemoveBackslash(ExpandConstant(Right))) = 0;
end;

function CheckLockedInstallPath(): Boolean;
var
  LockedPath, SelectedPath: String;
begin
  Result := True;
  LockedPath := GetLockedExistingInstallPath();
  if LockedPath = '' then
    Exit;

  SelectedPath := RemoveBackslash(WizardForm.DirEdit.Text);
  if not SameInstallPath(SelectedPath, LockedPath) then
  begin
    MsgBox(FmtMessage(CustomMessage('InstallDirMismatch'), [
      LockedPath,
      SelectedPath
    ]), mbError, MB_OK);
    WizardForm.DirEdit.Text := LockedPath;
    Result := False;
  end;
end;

procedure InitializeWizard();
begin
  WizardForm.DirEdit.Text := GetPreferredInstallDir();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectDir then
  begin
    if not ShouldUseInstallPath(WizardForm.DirEdit.Text) then
      WizardForm.DirEdit.Text := GetPreferredInstallDir();
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
    Result := CheckLockedInstallPath();
end;

function InitializeSetup(): Boolean;
var
  PrevVer, PrevPath, PrevName, Msg: String;
begin
  Result := True;
  if WizardSilent() then
    Exit;

  PrevVer := GetExistingVersion();
  if PrevVer <> '' then
  begin
    PrevName := GetExistingDisplayName();
    PrevPath := GetExistingInstallPath();
    if PrevPath = '' then
      PrevPath := ExpandConstant('{autopf}\{#MyAppName}');
    Msg := FmtMessage(CustomMessage('UpgradeConfirm'), [
      PrevName,
      PrevVer,
      PrevPath,
      ExpandConstant('{#MyAppVersion}')
    ]);
    if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  LockedPath, TargetPath: String;
begin
  Result := '';
  LockedPath := GetLockedExistingInstallPath();
  if LockedPath = '' then
    Exit;

  TargetPath := RemoveBackslash(ExpandConstant('{app}'));
  if not SameInstallPath(TargetPath, LockedPath) then
    Result := FmtMessage(CustomMessage('InstallDirMismatch'), [
      LockedPath,
      TargetPath
    ]);
end;

// WebView2 会把前端 JS/CSS 缓存在用户 profile 下，重装 exe 不会自动失效。
procedure ClearWebViewHttpCache();
var
  BasePath: String;
  CacheDirs: array[0..6] of String;
  I: Integer;
begin
  BasePath := ExpandConstant('{%USERPROFILE}\.jiuwenavatar\webview');
  CacheDirs[0] := BasePath + '\EBWebView\Default\Cache';
  CacheDirs[1] := BasePath + '\EBWebView\Default\Code Cache';
  CacheDirs[2] := BasePath + '\EBWebView\Default\GPUCache';
  CacheDirs[3] := BasePath + '\EBWebView\Default\Service Worker\CacheStorage';
  CacheDirs[4] := BasePath + '\EBWebView\ShaderCache';
  CacheDirs[5] := BasePath + '\EBWebView\GrShaderCache';
  CacheDirs[6] := BasePath + '\EBWebView\GPUPersistentCache';
  for I := 0 to High(CacheDirs) do
  begin
    if DirExists(CacheDirs[I]) then
      DelTree(CacheDirs[I], True, True, True);
  end;
end;

// 安装后确保用户目录存在，并清理 WebView 前端 HTTP 缓存（升级后避免旧 UI）
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not DirExists(ExpandConstant('{%USERPROFILE}\.jiuwenavatar')) then
      CreateDir(ExpandConstant('{%USERPROFILE}\.jiuwenavatar'));
    ClearWebViewHttpCache();
  end;
end;