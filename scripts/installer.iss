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
; Per-user install: LocalAppData is user-writable, so the installer no longer
; needs administrator privileges. The previous admin lineage installed into
; {autopf} (Program Files); a [Code] hook migrates those installs away.
DefaultDirName={localappdata}\Programs\{#MyAppName}
; Do not inherit the old admin install directory for the same AppId. If an
; uninstall was interrupted, that directory is still under Program Files and
; cannot be safely written by this per-user installer.
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename={#BuildSetupBaseName}
SetupIconFile=..\jiuwenswarm\channels\web\frontend\public\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
; lowest = no UAC for a fresh per-user install. The [Code] section elevates
; (runas) only when an old admin install must be removed during migration.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Keep the legacy mutexes as a stable upgrade protocol for the existing AppId.
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

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
; Remove only application-owned paths that may gain runtime-generated files.
; User data lives outside {app}, under ~/.jiuwenswarm, and is intentionally kept.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\runtime"
Type: files; Name: "{app}\{#MyAppExeName}"
Type: dirifempty; Name: "{app}"

[Run]
; 通过 Explorer 代启动程序，使安装完成后的启动上下文更接近桌面快捷方式启动
; postinstall 在安装向导最后一页显示运行应用复选框，由用户决定是否启动
Filename: "{win}\explorer.exe"; Parameters: """{app}\{#MyAppExeName}"""; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall

[Code]
function HasDescriptionStemInTree(const Directory, Stem: String): Boolean;
var
  FindRec: TFindRec;
  ChildDirectory: String;
begin
  Result := False;
  if not FindFirst(AddBackslash(Directory) + '*', FindRec) then
    exit;

  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        continue;

      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        if CompareText(FindRec.Name, 'fragments') <> 0 then
        begin
          ChildDirectory := AddBackslash(Directory) + FindRec.Name;
          if HasDescriptionStemInTree(ChildDirectory, Stem) then
          begin
            Result := True;
            exit;
          end;
        end;
      end
      else if (CompareText(ExtractFileExt(FindRec.Name), '.md') = 0) and
              (CompareText(ChangeFileExt(FindRec.Name, ''), Stem) = 0) then
      begin
        Result := True;
        exit;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function HasNestedDescriptionReplacement(const LanguageDirectory, Stem: String): Boolean;
var
  FindRec: TFindRec;
  ChildDirectory: String;
begin
  Result := False;
  if not FindFirst(AddBackslash(LanguageDirectory) + '*', FindRec) then
    exit;

  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        continue;

      if ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
         (CompareText(FindRec.Name, 'fragments') <> 0) then
      begin
        ChildDirectory := AddBackslash(LanguageDirectory) + FindRec.Name;
        if HasDescriptionStemInTree(ChildDirectory, Stem) then
        begin
          Result := True;
          exit;
        end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure CleanupStaleOpenJiuwenDescriptions();
var
  DescsDirectory: String;
  LanguageDirectory: String;
  StalePath: String;
  Stem: String;
  LanguageRec: TFindRec;
  FlatRec: TFindRec;
begin
  DescsDirectory := ExpandConstant(
    '{app}\_internal\openjiuwen\agent_teams\tools\locales\descs'
  );
  if not DirExists(DescsDirectory) then
    exit;

  if not FindFirst(AddBackslash(DescsDirectory) + '*', LanguageRec) then
    exit;

  try
    repeat
      if (LanguageRec.Name = '.') or (LanguageRec.Name = '..') or
         ((LanguageRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0) then
        continue;

      LanguageDirectory := AddBackslash(DescsDirectory) + LanguageRec.Name;
      if not FindFirst(AddBackslash(LanguageDirectory) + '*.md', FlatRec) then
        continue;

      try
        repeat
          Stem := ChangeFileExt(FlatRec.Name, '');
          if not HasNestedDescriptionReplacement(LanguageDirectory, Stem) then
            continue;

          StalePath := AddBackslash(LanguageDirectory) + FlatRec.Name;
          if DeleteFile(StalePath) then
            Log('Removed stale OpenJiuwen description: ' + StalePath)
          else
            RaiseException(
              'Unable to remove stale OpenJiuwen description: ' + StalePath
            );
        until not FindNext(FlatRec);
      finally
        FindClose(FlatRec);
      end;
    until not FindNext(LanguageRec);
  finally
    FindClose(LanguageRec);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    exit;

  { Setup is elevated, unlike the installed application's ordinary user. }
  CleanupStaleOpenJiuwenDescriptions();
end;

// Migration: remove a previous admin-lineage install (AppId
// 6DC96977-C194-44FE-812D-D4F0B576BD905) that lives in Program Files + HKLM.
// A per-user (lowest) installer cannot delete those paths itself, so it drives
// the old uninstaller via runas. This triggers one UAC during migration only;
// fresh per-user installs run without any elevation.

const
  // Inno writes the uninstall entry as "<AppId>_is1" under HKLM. The old admin
  // install registers in the 64-bit view; WOW6432Node is checked as a fallback.
  LegacyUninstallNativeSubkey = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{6DC96977-C194-44FE-812D-D4F0B576BD905}_is1';
  LegacyUninstallWowSubkey = 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{6DC96977-C194-44FE-812D-D4F0B576BD905}_is1';

function StripQuotes(const S: String): String;
var
  T: String;
begin
  T := Trim(S);
  if (Length(T) >= 2) and (Copy(T, 1, 1) = '"') and (Copy(T, Length(T), 1) = '"') then
    T := Copy(T, 2, Length(T) - 2);
  Result := Trim(T);
end;

function TryRegUninstaller(const Subkey: String; var ExePath: String): Boolean;
var
  Raw: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, Subkey, 'UninstallString', Raw) then
  begin
    ExePath := StripQuotes(Raw);
    Result := (ExePath <> '') and FileExists(ExePath);
  end;
end;

function TryFallbackUninstaller(const ProgramFilesRoot: String; const FolderName: String; var ExePath: String): Boolean;
var
  Candidate: String;
begin
  Candidate := ProgramFilesRoot + '\' + FolderName + '\unins000.exe';
  Result := FileExists(Candidate);
  if Result then
    ExePath := Candidate;
end;

function GetLegacyAdminUninstaller(var ExePath: String): Boolean;
var
  Pf64: String;
  Pf32: String;
begin
  Result := False;
  // 1) Registry first: covers any custom install folder name used by the old setup.
  if TryRegUninstaller(LegacyUninstallNativeSubkey, ExePath) then
    Result := True
  else if TryRegUninstaller(LegacyUninstallWowSubkey, ExePath) then
    Result := True
  else
  begin
    // 2) Fallback: well-known Program Files locations + current/former product names.
    Pf64 := GetEnv('ProgramFiles');
    if Pf64 = '' then Pf64 := 'C:\Program Files';
    Pf32 := GetEnv('ProgramFiles(x86)');
    if Pf32 = '' then Pf32 := GetEnv('ProgramFiles');
    if Pf32 = '' then Pf32 := 'C:\Program Files (x86)';
    if TryFallbackUninstaller(Pf64, '{#MyAppName}', ExePath) then
      Result := True
    else if TryFallbackUninstaller(Pf32, '{#MyAppName}', ExePath) then
      Result := True
    else if TryFallbackUninstaller(Pf64, 'JiuwenSwarm', ExePath) then
      Result := True
    else if TryFallbackUninstaller(Pf32, 'JiuwenSwarm', ExePath) then
      Result := True;
  end;
end;

function InitializeSetup: Boolean;
var
  LegacyUninstaller: String;
  ErrorCode: Integer;
begin
  Result := True;
  // No old admin install: clean per-user install, no UAC at all.
  if not GetLegacyAdminUninstaller(LegacyUninstaller) then
    Exit;
  // Old admin install detected. Removing Program Files + HKLM requires admin,
  // so we drive the old uninstaller with runas (one UAC during migration).
  if MsgBox('检测到旧版本（管理员安装，位于 Program Files）。' + #13#10 +
            '安装新版前将卸载旧版本，期间会弹出权限确认，请点击“是”。' + #13#10 +
            '若应用正在运行，请先关闭后再继续。' + #13#10 + #13#10 +
            '是否现在卸载旧版本？',
            mbConfirmation, MB_YESNO) <> IDYES then
  begin
    Result := False;
    Exit;
  end;
  // The old unins000.exe carries an admin manifest; runas lets it remove its
  // own Program Files tree + HKLM entry. /VERYSILENT keeps it headless.
  if not ShellExec('runas', LegacyUninstaller,
                   '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', '',
                   SW_HIDE, ewWaitUntilTerminated, ErrorCode) then
  begin
    MsgBox('启动旧版本卸载程序失败（错误码：' + IntToStr(ErrorCode) + '）。' + #13#10 +
           '请手动卸载旧版本后再运行本安装程序。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  // Inno's uninstaller copies itself to TEMP and finishes asynchronously, so a
  // completion check here would false-positive: the registry entry is removed
  // near the end of the temp copy, after this call returns. The new per-user
  // install targets {localappdata}\Programs and is independent of the old
  // Program Files tree, so installation proceeds regardless. If the app was
  // still running the new setup's own AppMutex guard will have blocked startup.
end;
