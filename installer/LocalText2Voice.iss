#ifndef MyAppVersion
  #define MyAppVersion "1.2.0"
#endif

#define MyAppName "LocalText2Voice"
#define MyAppPublisher "Esteban / AndromedaNova.com"
#define MyAppURL "https://andromedanova.com"
#define MyAppUpdatesURL "https://github.com/estebanstifli/LocalText2Voice/releases/latest"
#ifndef SourceDir
  #define SourceDir "..\dist\LocalText2Voice"
#endif
#ifndef UserDataDir
  #define UserDataDir "{localappdata}\LocalText2Voice"
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
english.RemoveDownloadedAIDataPrompt=LocalText2Voice found downloaded AI engines, models, runtime dependencies, or voice gallery files. Remove them too? This can free several GB. Audiobook projects, exported audio, and settings will be kept.

spanish.CpuLightType=Equipo CPU ligero - solo Piper
spanish.GpuPowerType=GPU potente - preparar OmniVoice y Faster Whisper
spanish.CoreComponent=Aplicación portable LocalText2Voice
spanish.GpuComponent=Descargar OmniVoice y Faster Whisper en el primer arranque
spanish.DesktopIcon=Crear acceso directo en el escritorio
spanish.LaunchProgram=Abrir LocalText2Voice
spanish.RemoveDownloadedAIDataPrompt=LocalText2Voice ha encontrado motores de IA, modelos, dependencias o archivos de voces descargados. ¿Quieres eliminarlos también? Esto puede liberar varios GB. Los proyectos, audios exportados y ajustes se conservarán.

french.RemoveDownloadedAIDataPrompt=LocalText2Voice a détecté des moteurs d'IA, des modèles, des dépendances d'exécution ou des fichiers de galerie vocale téléchargés. Voulez-vous également les supprimer ? Cela peut libérer plusieurs Go. Les projets de livres audio, les fichiers audio exportés et les paramètres seront conservés.
german.RemoveDownloadedAIDataPrompt=LocalText2Voice hat heruntergeladene KI-Engines, Modelle, Laufzeitabhängigkeiten oder Sprachgalerie-Dateien gefunden. Sollen diese ebenfalls entfernt werden? Dadurch können mehrere GB freigegeben werden. Hörbuchprojekte, exportierte Audiodateien und Einstellungen bleiben erhalten.
italian.RemoveDownloadedAIDataPrompt=LocalText2Voice ha rilevato motori IA, modelli, dipendenze di runtime o file della galleria vocale scaricati. Vuoi rimuoverli? Puoi liberare diversi GB. I progetti di audiolibri, gli audio esportati e le impostazioni verranno conservati.
portuguese.RemoveDownloadedAIDataPrompt=O LocalText2Voice encontrou motores de IA, modelos, dependências de runtime ou ficheiros da galeria de vozes transferidos. Deseja removê-los também? Isto pode libertar vários GB. Os projetos de audiolivros, os áudios exportados e as definições serão mantidos.
japanese.RemoveDownloadedAIDataPrompt=ダウンロード済みの AI エンジン、モデル、ランタイム依存関係、または音声ギャラリーのファイルが見つかりました。これらも削除しますか？数 GB の空き容量を確保できる場合があります。オーディオブックのプロジェクト、書き出した音声、設定は保持されます。
arabic.RemoveDownloadedAIDataPrompt=عثر LocalText2Voice على محركات ذكاء اصطناعي أو نماذج أو تبعيات تشغيل أو ملفات معرض أصوات تم تنزيلها. هل تريد حذفها أيضًا؟ قد يؤدي ذلك إلى توفير عدة غيغابايت. سيتم الاحتفاظ بمشروعات الكتب الصوتية والملفات الصوتية المصدرة والإعدادات.

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
var
  RemoveDownloadedAIData: Boolean;

function UserDataRoot(): String;
begin
  Result := ExpandConstant('{#UserDataDir}');
end;

function DownloadedAIDataExists(): Boolean;
var
  Root: String;
begin
  Root := UserDataRoot();
  Result :=
    DirExists(Root + '\models') or
    DirExists(Root + '\runtimes') or
    DirExists(Root + '\voice-gallery') or
    DirExists(ExpandConstant('{app}\runtimes\python311\engine-deps'));
end;

procedure RemoveDataTree(const Path: String);
begin
  if not DirExists(Path) then
    Exit;

  Log('Removing LocalText2Voice data directory: ' + Path);
  if not DelTree(Path, True, True, True) then
    Log('Could not completely remove LocalText2Voice data directory: ' + Path);
end;

function InitializeUninstall(): Boolean;
begin
  RemoveDownloadedAIData := False;
  if DownloadedAIDataExists() then
  begin
    if UninstallSilent() then
      Log('Silent uninstall: downloaded AI data will be preserved.')
    else
      RemoveDownloadedAIData :=
        MsgBox(
          ExpandConstant('{cm:RemoveDownloadedAIDataPrompt}'),
          mbConfirmation,
          MB_YESNO or MB_DEFBUTTON1
        ) = IDYES;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Root: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  Root := UserDataRoot();

  { These contain only temporary coordination and downloaded update files. }
  RemoveDataTree(Root + '\server');
  RemoveDataTree(Root + '\updates');

  if RemoveDownloadedAIData then
  begin
    RemoveDataTree(Root + '\models');
    RemoveDataTree(Root + '\runtimes');
    RemoveDataTree(Root + '\voice-gallery');

    { Optional Python engine packages are written beside the bundled runtime. }
    RemoveDataTree(
      ExpandConstant('{app}\runtimes\python311\engine-deps')
    );

    { Inno removes bundled Piper voices; this removes voices added later. }
    RemoveDataTree(ExpandConstant('{app}\voices'));
  end;

  { Remove only empty roots. Projects, exports, settings, music, and logs remain. }
  RemoveDir(Root);
  RemoveDir(ExpandConstant('{app}'));
end;

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
    '  "settings_schema_version": 16,' + #13#10 +
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
