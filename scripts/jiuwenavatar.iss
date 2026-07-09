#define MyAppName "JiuwenClaw"
// MyAppVersion must be passed via /DMyAppVersion="X.Y.Z" at compile time.
// Manual compilation (without /D) will fail — use build-exe.bat instead.
#ifndef MyAppVersion
#error "MyAppVersion not defined. Use build-exe.bat or pass /DMyAppVersion=..."
#endif
#define MyAppPublisher "JiuwenClaw"
#define MyAppURL "https://github.com/"
#define MyAppExeName "jiuwenavatar.exe"
#define MyDistDir "..\dist\jiuwenavatar"
#define MyAppId "6DDF1C96-B2CE-4A2F-A7E7-A2E8627AE0A2"

[Setup]
AppId={{6DDF1C96-B2CE-4A2F-A7E7-A2E8627AE0A2}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
SetupLogging=yes
OutputDir=..\dist\installer
OutputBaseFilename=jiuwenavatar-setup-{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}

; 如果后续补了 ico，可以取消下面两行注释并指向同一图标文件
; SetupIconFile=..\assets\jiuwenavatar.ico
; WizardSmallImageFile=..\assets\jiuwenavatar.bmp

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
InstallDirMismatch=检测到已安装的 {#MyAppName} 位于：%n%1%n%n重新安装或升级必须使用原安装路径，不能安装到：%n%2

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--sync-builtin-skills"; StatusMsg: "正在同步内置技能..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 默认不删除用户数据目录，避免误删配置和日志。
; 如果你需要在卸载时清理缓存，可按需增加删除规则。

[Code]
const
  UninstallRegSubKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}}_is1';
  UninstallRegSubKeyCompat = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';

function TryQueryInstallPath(RootKey: Integer; var InstallPath: String): Boolean;
begin
  Result := RegQueryStringValue(RootKey, UninstallRegSubKey, 'InstallLocation', InstallPath);
end;

function GetExistingInstallPath(): String;
begin
  Result := '';
  if not TryQueryInstallPath(HKCU64, Result) then
    if not RegQueryStringValue(HKCU64, UninstallRegSubKeyCompat, 'InstallLocation', Result) then
      if not TryQueryInstallPath(HKCU32, Result) then
        if not RegQueryStringValue(HKCU32, UninstallRegSubKeyCompat, 'InstallLocation', Result) then
          if not TryQueryInstallPath(HKLM64, Result) then
            if not RegQueryStringValue(HKLM64, UninstallRegSubKeyCompat, 'InstallLocation', Result) then
              if not TryQueryInstallPath(HKLM32, Result) then
                RegQueryStringValue(HKLM32, UninstallRegSubKeyCompat, 'InstallLocation', Result);
  Result := RemoveBackslash(Result);
  if (Result <> '') and not FileExists(Result + '\{#MyAppExeName}') then
    Result := '';
end;

function SameInstallPath(const Left, Right: String): Boolean;
begin
  Result := CompareText(RemoveBackslash(ExpandConstant(Left)), RemoveBackslash(ExpandConstant(Right))) = 0;
end;

function CheckLockedInstallPath(): Boolean;
var
  ExistingPath, SelectedPath: String;
begin
  Result := True;
  ExistingPath := GetExistingInstallPath();
  if ExistingPath = '' then
    Exit;
  SelectedPath := RemoveBackslash(WizardForm.DirEdit.Text);
  if not SameInstallPath(SelectedPath, ExistingPath) then
  begin
    MsgBox(FmtMessage(CustomMessage('InstallDirMismatch'), [
      ExistingPath,
      SelectedPath
    ]), mbError, MB_OK);
    WizardForm.DirEdit.Text := ExistingPath;
    Result := False;
  end;
end;

procedure InitializeWizard();
var
  ExistingPath: String;
begin
  ExistingPath := GetExistingInstallPath();
  if ExistingPath <> '' then
    WizardForm.DirEdit.Text := ExistingPath;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
    Result := CheckLockedInstallPath();
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExistingPath, TargetPath: String;
begin
  Result := '';
  ExistingPath := GetExistingInstallPath();
  if ExistingPath = '' then
    Exit;
  TargetPath := RemoveBackslash(ExpandConstant('{app}'));
  if not SameInstallPath(TargetPath, ExistingPath) then
    Result := FmtMessage(CustomMessage('InstallDirMismatch'), [
      ExistingPath,
      TargetPath
    ]);
end;

function UserWorkspaceDir(): string;
begin
  Result := ExpandConstant('{userappdata}') + '\..\.jiuwenavatar';
end;
