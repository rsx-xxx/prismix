; Inno Setup script. Build: ISCC packaging\Prismix.iss /DAppVersion=1.0.0
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1D2B8E-4C7A-4E59-9A3B-5D0B1A7C9E21}
AppName=Prismix
AppVersion={#AppVersion}
AppPublisher=rsx-xxx
DefaultDirName={autopf}\Prismix
DefaultGroupName=Prismix
OutputDir=..\dist
OutputBaseFilename=Prismix-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Prismix.exe
Compression=lzma2/fast
SolidCompression=no
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
CloseApplications=yes
RestartApplications=no
WizardStyle=modern

[Files]
Source: "..\dist\Prismix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Prismix"; Filename: "{app}\Prismix.exe"
Name: "{autodesktop}\Prismix"; Filename: "{app}\Prismix.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Prismix.exe"; Description: "Launch Prismix"; Flags: nowait postinstall skipifsilent
