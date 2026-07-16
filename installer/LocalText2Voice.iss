#ifndef MyAppVersion
  #define MyAppVersion "1.1.1"
#endif

#define MyAppName "LocalText2Voice"
#define MyAppPublisher "Esteban / AndromedaNova.com"
#define MyAppURL "https://andromedanova.com"
#define MyAppUpdatesURL "https://github.com/estebanstifli/LocalText2Voice/releases/latest"
#ifndef SourceDir
  #define SourceDir "..\dist\LocalText2Voice"
#endif
#define OutputDir "..\.util_instalador_y_firmas\output"

[Setup]
AppId={{B05DB7EE-3D2B-4E5D-9F4B-A0A7DE187C4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppUpdatesURL}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=LocalText2Voice-Setup
SetupIconFile=..\assets\LocalText2Voice.ico
UninstallDisplayIcon={app}\LocalText2Voice.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=LocalText2Voice installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[CustomMessages]
english.CpuLightType=CPU light - Piper only
english.GpuPowerType=Powerful GPU - prepare OmniVoice and Faster Whisper
english.CoreComponent=LocalText2Voice portable application
english.GpuComponent=Download OmniVoice and Faster Whisper on first launch
english.DesktopIcon=Create a desktop shortcut
english.LaunchProgram=Launch LocalText2Voice

spanish.CpuLightType=Equipo CPU ligero - solo Piper
spanish.GpuPowerType=GPU potente - preparar OmniVoice y Faster Whisper
spanish.CoreComponent=Aplicación portable LocalText2Voice
spanish.GpuComponent=Descargar OmniVoice y Faster Whisper en el primer arranque
spanish.DesktopIcon=Crear acceso directo en el escritorio
spanish.LaunchProgram=Abrir LocalText2Voice

[Types]
Name: "cpu"; Description: "{cm:CpuLightType}"
Name: "gpu"; Description: "{cm:GpuPowerType}"

[Components]
Name: "core"; Description: "{cm:CoreComponent}"; Types: cpu gpu; Flags: fixed
Name: "gpu"; Description: "{cm:GpuComponent}"; Types: gpu

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "runtimes\python311\engine-deps\*, output\*, logs\*, __pycache__\*, *.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\LocalText2Voice.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\LocalText2Voice.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LocalText2Voice.exe"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent

[Code]
function SelectedUiLanguageCode(): String;
var
  Lang: String;
begin
  Lang := ActiveLanguage();
  if Lang = 'spanish' then
    Result := 'es'
  else if Lang = 'french' then
    Result := 'fr'
  else if Lang = 'german' then
    Result := 'de'
  else if Lang = 'italian' then
    Result := 'it'
  else if Lang = 'portuguese' then
    Result := 'pt'
  else if Lang = 'japanese' then
    Result := 'ja'
  else if Lang = 'arabic' then
    Result := 'ar'
  else
    Result := 'en';
end;

function InitialConfigJson(): String;
var
  UiLang: String;
  Profile: String;
  TtsEngine: String;
  PendingInstalls: String;
  Completed: String;
  ReviewEnabled: String;
begin
  UiLang := SelectedUiLanguageCode();
  if WizardIsComponentSelected('gpu') then
  begin
    Profile := 'gpu';
    TtsEngine := 'omnivoice';
    PendingInstalls := '["omnivoice", "faster_whisper"]';
    Completed := 'false';
    ReviewEnabled := 'true';
  end
  else
  begin
    Profile := 'cpu';
    TtsEngine := 'piper';
    PendingInstalls := '[]';
    Completed := 'true';
    ReviewEnabled := 'false';
  end;

  Result :=
    '{' + #13#10 +
    '  "settings_schema_version": 11,' + #13#10 +
    '  "ui_language": "' + UiLang + '",' + #13#10 +
    '  "output_dir": "output",' + #13#10 +
    '  "tts_engine": "' + TtsEngine + '",' + #13#10 +
    '  "review": {' + #13#10 +
    '    "enabled": ' + ReviewEnabled + ',' + #13#10 +
    '    "auto_verify_after_generation": ' + ReviewEnabled + #13#10 +
    '  },' + #13#10 +
    '  "installer_setup": {' + #13#10 +
    '    "profile": "' + Profile + '",' + #13#10 +
    '    "pending_installs": ' + PendingInstalls + ',' + #13#10 +
    '    "completed": ' + Completed + ',' + #13#10 +
    '    "completed_at": ""' + #13#10 +
    '  }' + #13#10 +
    '}';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    ConfigPath := ExpandConstant('{app}\config.json');
    if not FileExists(ConfigPath) then
      SaveStringToFile(ConfigPath, InitialConfigJson(), False);
  end;
end;
