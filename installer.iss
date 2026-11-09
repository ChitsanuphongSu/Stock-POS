#define MyAppName "SP Toys POS"
#define MyAppVersion "0.1.0"
[Setup]
AppId={{6C0F453E-A087-49E3-A437-EA034415663A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\SP Toys POS
DefaultGroupName=SP Toys POS
OutputDir=installer-output
OutputBaseFilename=SP Toys POS Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
[Files]
Source: "dist\SP Toys POS\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{group}\SP Toys POS"; Filename: "{app}\SP Toys POS.exe"; IconFilename: "{app}\_internal\assets\sp-toys.ico"
Name: "{autodesktop}\SP Toys POS"; Filename: "{app}\SP Toys POS.exe"; IconFilename: "{app}\_internal\assets\sp-toys.ico"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "สร้างไอคอนบน Desktop"; Flags: unchecked
[Run]
Filename: "{app}\SP Toys POS.exe"; Description: "เปิด SP Toys POS"; Flags: nowait postinstall skipifsilent
