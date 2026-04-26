; ============================================================
; TranslateX — Inno Setup installer script
; Requires Inno Setup 6+ from https://jrsoftware.org/isinfo.php
;
; Usage:
;   ISCC.exe installer\setup.iss
; Output: installer\Output\TranslateX_Setup_1.0.0.exe
; ============================================================

#define MyAppName        "TranslateX"
#define MyAppPublisher   "TranslateX"
; Path to PyInstaller output (relative to this .iss file).
; Note: this is also referenced below by [Files] section.
#define BuildDir "..\dist\TranslateX"
;
; Версия читается ДИНАМИЧЕСКИ из ресурса скомпилированного TranslateX.exe
; (который в свою очередь генерируется PyInstaller'ом из version_info.txt,
;  а тот — из core/__version__.py через scripts/sync_version.py).
; Это даёт SSOT: версию правишь в одном месте — core/__version__.py.
#define MyAppExeFull AddBackslash(SourcePath) + BuildDir + "\TranslateX.exe"
#if FileExists(MyAppExeFull)
  #define MyAppVersion GetFileVersion(MyAppExeFull)
#else
  #pragma warning "\nWARNING: " + MyAppExeFull + " not found — falling back to 0.0.0.\nRun pyinstaller build.spec first."
  #define MyAppVersion "0.0.0"
#endif
#define MyAppURL         "https://github.com/your-repo/translatex"
#define MyAppExeName     "TranslateX.exe"
#define MyAppDescription "Real-time screen translation overlay"

[Setup]
AppId={{F3A7B2C1-8D4E-4F9A-B1C2-D3E4F5A6B7C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
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
WizardResizable=no

; Compression (LZMA is best for Python apps)
Compression=lzma2/ultra64
SolidCompression=yes
InternalCompressLevel=ultra64

; Privileges
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Minimum Windows version: Windows 10
MinVersion=10.0

; Allow silent install via /SILENT or /VERYSILENT
AllowNoIcons=yes

; Uninstall
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
CreateUninstallRegKey=yes

; Don't close applications
CloseApplications=no

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

[UninstallRun]
; Kill the process before uninstalling if it's running
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden skipifdoesntexist

[Code]
// ──────────────────────────────────────────────────────────
// Check for existing installation and offer to close the app
// ──────────────────────────────────────────────────────────
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// Show a friendly welcome note about first-run OCR download
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    MsgBox(
      'TranslateX has been installed!' + #13#10 + #13#10 +
      'On first launch, the OCR engine will download language models (~100 MB).' + #13#10 +
      'Please ensure you have an internet connection for the first run.' + #13#10 + #13#10 +
      'Hotkeys (configurable in Settings):' + #13#10 +
      '  Ctrl+Shift+T — Select screen region' + #13#10 +
      '  Ctrl+Shift+P — Toggle translation on/off',
      mbInformation,
      MB_OK
    );
  end;
end;
