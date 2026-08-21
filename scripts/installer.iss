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
Filename: "{win}\explorer.exe"; Parameters: """{app}\{#MyAppExeName}"""; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall; Check: DoctorPassed

[Code]
var
  DoctorSucceeded: Boolean;

function DoctorPassed(): Boolean;
begin
  Result := DoctorSucceeded;
end;

function LoadDoctorSummary(const FileName: String): String;
var
  Lines: TArrayOfString;
  Index: Integer;
begin
  Result := '';
  if LoadStringsFromFile(FileName, Lines) then
  begin
    for Index := 0 to GetArrayLength(Lines) - 1 do
    begin
      if Result <> '' then
        Result := Result + #13#10;
      Result := Result + Lines[Index];
    end;
  end;
end;

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
var
  DoctorOutputPath: String;
  DoctorSummaryPath: String;
  DoctorParams: String;
  Summary: String;
  ResultCode: Integer;
  Executed: Boolean;
begin
  if CurStep <> ssPostInstall then
    exit;

  { Setup is elevated, unlike the installed application's ordinary user. }
  CleanupStaleOpenJiuwenDescriptions();

  DoctorSucceeded := False;
  DoctorOutputPath := ExpandConstant('{tmp}\jiuwenswarm-doctor.json');
  DoctorSummaryPath := ExpandConstant('{tmp}\jiuwenswarm-doctor-summary.txt');
  DoctorParams := '--doctor --doctor-output "' + DoctorOutputPath +
    '" --doctor-summary-output "' + DoctorSummaryPath + '"';

  try
    { Run in the original user's environment, not Setup's elevated context. }
    Executed := ExecAsOriginalUser(
      ExpandConstant('{app}\{#MyAppExeName}'),
      DoctorParams,
      ExpandConstant('{app}'),
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );
  except
    Executed := False;
    ResultCode := -1;
  end;

  if Executed and (ResultCode = 0) then
  begin
    DoctorSucceeded := True;
    exit;
  end;

  Summary := LoadDoctorSummary(DoctorSummaryPath);
  if Summary = '' then
  begin
    if Executed then
      Summary := '{#MyAppName} 安装后自检未通过，退出码：' + IntToStr(ResultCode)
    else if ResultCode >= 0 then
      Summary := '无法启动 {#MyAppName} 自检程序：' + SysErrorMessage(ResultCode)
    else
      Summary := '无法启动 {#MyAppName} 自检程序。';
  end;

  MsgBox(
    Summary + #13#10 + #13#10 +
    '软件已完成安装，但本次不会自动启动。修复环境后可重新运行 {#MyAppName}。',
    mbError,
    MB_OK
  );
end;
