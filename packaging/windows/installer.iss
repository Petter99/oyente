; Instalador de Windows (Inno Setup 6). Lo invoca scripts\build-windows.ps1 con:
;   ISCC /DAppName=... /DAppVersion=... /DAppExe=... /DSourceDir=... /DOutputDir=... installer.iss
; El AppId es FIJO (identidad interna): cambiar el nombre visible no crea otra instalación.

#ifndef AppName
  #define AppName "Transcriptor"
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppExe
  #define AppExe AppName + ".exe"
#endif

[Setup]
AppId={{6E2B5C0A-7D1F-4E59-9C3B-2A1F8D4E7B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\..\assets\branding\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
LicenseFile=..\..\docs\legal\terminos.md

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; Flags: unchecked
Name: "startup"; Description: "Abrir al iniciar Windows"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Solo los archivos del programa. NUNCA se borran la bóveda de Obsidian, las notas ni la configuración.
Type: filesandordirs; Name: "{app}"
