; ============================================================
; TranslateX — Inno Setup installer script
; Requires Inno Setup 6+ from https://jrsoftware.org/isinfo.php
;
; Usage:
;   ISCC.exe installer\setup.iss
; Output: installer\Output\TranslateX_Setup_<version>.exe
; ============================================================

#define MyAppName        "TranslateX"
#define MyAppPublisher   "TranslateX"
; Path to PyInstaller output (relative to this .iss file).
; Note: this is also referenced below by [Files] section.
#ifndef BuildDir
  #define BuildDir "..\dist\TranslateX"
#endif
;
; Версия берётся из генерируемого version.iss (SSOT — core/__version__.py).
; Генерация: `python scripts/sync_version.py` (вызывается из build.bat).
#include "version.iss"
#define MyAppURL         "https://github.com/spbmolot/TranslateX"
#define MyAppExeName     "TranslateX.exe"
#define MyAppDescription "Real-time screen translation overlay"
#if GetVersionNumbersString(AddBackslash(BuildDir) + MyAppExeName) != MyAppVersion + ".0"
  #error The packaged TranslateX.exe does not match installer/version.iss. Run the build again.
#endif

[Setup]
AppId={{F3A7B2C1-8D4E-4F9A-B1C2-D3E4F5A6B7C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoTextVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}.0
VersionInfoProductTextVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppComments={#MyAppDescription}

; Install to Program Files
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Output
OutputDir=Output
OutputBaseFilename=TranslateX_Setup_{#MyAppVersion}

; Icon and visuals
SetupIconFile=..\assets\icon.ico
WizardStyle=modern

; Compression (LZMA is best for Python apps)
Compression=lzma2/ultra64
SolidCompression=yes
InternalCompressLevel=ultra64
#ifdef BuildBlockThreads
LZMANumBlockThreads={#BuildBlockThreads}
LZMAUseSeparateProcess=yes
#endif

; Privileges
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Minimum Windows version: Windows 10
MinVersion=10.0

; TranslateX — 64-битное приложение (torch). Ставим в «настоящий» Program Files
; и работаем с 64-битным представлением реестра.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Запоминаем каталог прошлой установки — обновление идёт «на место».
UsePreviousAppDir=yes

; Allow silent install via /SILENT or /VERYSILENT
AllowNoIcons=yes

; Uninstall
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
CreateUninstallRegKey=yes

; Restart Manager matches the actual file in {app} and requests a normal exit.
CloseApplications=yes
CloseApplicationsFilter=TranslateX.exe
RestartApplications=no

[Languages]
Name: "english";  MessagesFile: "compiler:Default.isl"
Name: "russian";  MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon";  Description: "{cm:CreateDesktopIcon}";   GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon";  Description: "Start TranslateX with Windows";  GroupDescription: "Startup:"; Flags: unchecked

[Files]
; All PyInstaller output files
Source: "{#BuildDir}\*";  DestDir: "{app}";  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start menu
Name: "{group}\{#MyAppName}";         Filename: "{app}\{#MyAppExeName}";  IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

; Desktop icon (optional)
Name: "{autodesktop}\{#MyAppName}";   Filename: "{app}\{#MyAppExeName}";  IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Auto-start with Windows (optional task)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startupicon

[Run]
; Launch after install (optional, skippable)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// ════════════════════════════════════════════════════════════════════════
//  Версионирование и качественное обновление
//
//  При запуске установщик:
//    1. Считывает версию уже установленного TranslateX из реестра
//       (ключ удаления Inno: <AppId>_is1, поле DisplayVersion).
//    2. Сравнивает её с версией дистрибутива {#MyAppVersion}.
//    3. Показывает осмысленное сообщение:
//         • нет установки   → обычная установка;
//         • старее          → предложение обновить (X → Y);
//         • та же версия    → предложение переустановить (восстановить);
//         • новее           → предупреждение о даунгрейде.
//    4. Перед копированием Restart Manager может закрыть установленную копию.
// ════════════════════════════════════════════════════════════════════════

const
  UninstallKey =
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    '{F3A7B2C1-8D4E-4F9A-B1C2-D3E4F5A6B7C8}_is1';

var
  gInstalledVersion: String;
  gIsUpgrade: Boolean;

// Версия уже установленного TranslateX (из реестра). Проверяем оба
// представления реестра (64/32) + HKCU — на случай прежних сборок.
function GetInstalledVersion(var Version: String): Boolean;
begin
  Version := '';
  Result :=
    RegQueryStringValue(HKLM64, UninstallKey, 'DisplayVersion', Version) or
    RegQueryStringValue(HKLM32, UninstallKey, 'DisplayVersion', Version) or
    RegQueryStringValue(HKCU,   UninstallKey, 'DisplayVersion', Version);
end;

// Сравнение версий вида "a.b.c": -1 если A<B, 0 если равны, 1 если A>B.
function CompareVersions(A, B: String): Integer;
var
  na, nb, pa, pb: Integer;
begin
  Result := 0;
  while ((Length(A) > 0) or (Length(B) > 0)) and (Result = 0) do
  begin
    pa := Pos('.', A);
    if pa > 0 then begin
      na := StrToIntDef(Copy(A, 1, pa - 1), 0);
      Delete(A, 1, pa);
    end else begin
      na := StrToIntDef(A, 0);
      A := '';
    end;

    pb := Pos('.', B);
    if pb > 0 then begin
      nb := StrToIntDef(Copy(B, 1, pb - 1), 0);
      Delete(B, 1, pb);
    end else begin
      nb := StrToIntDef(B, 0);
      B := '';
    end;

    if na < nb then Result := -1
    else if na > nb then Result := 1;
  end;
end;

function InitializeSetup(): Boolean;
var
  cmp: Integer;
begin
  Result := True;
  gIsUpgrade := False;

  if not GetInstalledVersion(gInstalledVersion) then
    Exit;  // свежая установка — без вопросов

  cmp := CompareVersions(gInstalledVersion, '{#MyAppVersion}');

  if cmp < 0 then
  begin
    gIsUpgrade := True;
    if SuppressibleMsgBox('Установлена версия TranslateX ' + gInstalledVersion + '.' + #13#10 +
              'Обновить до версии {#MyAppVersion}?' + #13#10 + #13#10 +
              'Ваши настройки будут сохранены.',
              mbConfirmation, MB_YESNO, IDYES) = IDNO then
      Result := False;
  end
  else if cmp = 0 then
  begin
    gIsUpgrade := True;
    if SuppressibleMsgBox('TranslateX версии {#MyAppVersion} уже установлена.' + #13#10 +
              'Переустановить (восстановить файлы программы)?',
              mbConfirmation, MB_YESNO, IDYES) = IDNO then
      Result := False;
  end
  else
  begin
    gIsUpgrade := True;
    if SuppressibleMsgBox('Внимание: установлена БОЛЕЕ НОВАЯ версия TranslateX ' +
              gInstalledVersion + '.' + #13#10 +
              'Сейчас вы устанавливаете более старую версию {#MyAppVersion} (откат).'
              + #13#10 + #13#10 + 'Продолжить?',
              mbError, MB_YESNO, IDNO) = IDNO then
      Result := False;
  end;
end;

// Restart Manager is an installer feature. On uninstall let the user exit
// normally if this installation's executable is still mapped by Windows.
function InitializeUninstall(): Boolean;
var
  Exe: String;
  Probe: TFileStream;
begin
  Result := True;
  Exe := ExpandConstant('{app}\{#MyAppExeName}');
  while FileExists(Exe) do begin
    try
      Probe := TFileStream.Create(Exe, fmOpenReadWrite or fmShareDenyNone);
      Probe.Free;
      Exit;
    except
      if SuppressibleMsgBox('Файл ' + Exe + ' занят или недоступен.' + #13#10 +
        'Закройте TranslateX через «Выход» в меню значка возле часов и нажмите «Повторить».',
        mbError, MB_RETRYCANCEL, IDCANCEL) <> IDRETRY then begin
        Result := False;
        Exit;
      end;
    end;
  end;
end;

// Дружелюбное сообщение после установки.
procedure CurStepChanged(CurStep: TSetupStep);
var
  head: String;
begin
  if (CurStep = ssPostInstall) and not WizardSilent then
  begin
    if gIsUpgrade then
      head := 'TranslateX обновлён до версии {#MyAppVersion}!'
    else
      head := 'TranslateX {#MyAppVersion} установлен!';

    MsgBox(
      head + #13#10 + #13#10 +
      'Базовые модели распознавания (русский + английский) уже включены в' + #13#10 +
      'комплект — приложение работает сразу, без скачивания.' + #13#10 +
      'Модели для других языков будут загружены при необходимости.' + #13#10 + #13#10 +
      'Посмотреть и изменить горячие клавиши можно в настройках TranslateX.',
      mbInformation,
      MB_OK
    );
  end;
end;
