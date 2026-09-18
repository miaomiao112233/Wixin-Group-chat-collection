; -*- Inno Setup Script -*-
; WxSum Windows 安装包。由 scripts/release_build.py 调用 ISCC 编译，
; 版本号通过 /DAppVersion=x.y.z 传入。
;
; 关键：per-user 安装（PrivilegesRequired=lowest + %LOCALAPPDATA%\Programs），
; 不弹 UAC、安装目录普通权限可写——自动更新器（外部 bat 覆盖文件）
; 才能正常工作。切勿改回 {autopf}（Program Files 需管理员权限，更新会失败）。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppVersion4 AppVersion + ".0"

[Setup]
; 固定 AppId：升级/卸载靠它识别同一产品，不要每次生成新 GUID
AppId={{B7A2C4E1-9D3F-4A6B-8E5C-2F1D8A9B0C7E}
AppName=WxSum
AppVersion={#AppVersion}
AppPublisher=WxSum
AppPublisherURL=https://github.com/miaomiao112233/Wixin-Group-chat-collection
AppSupportURL=https://github.com/miaomiao112233/Wixin-Group-chat-collection
AppComments=微信群消息监控总结工具
VersionInfoVersion={#AppVersion4}

DefaultDirName={localappdata}\Programs\WxSum
DefaultGroupName=WxSum
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=dist
OutputBaseFilename=WxSum-v{#AppVersion}-Setup
SetupIconFile=installer\app.ico
UninstallDisplayIcon={app}\WxSum.exe
UninstallDisplayName=WxSum {#AppVersion}

Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardResizable=no
ShowLanguageDialog=no
CloseApplications=yes
RestartIfNeededByRun=no
MinVersion=10.0

[Languages]
; 使用项目自带的简体中文语言包（随 git 管理，换机器/CI 也不依赖安装目录内容）
Name: "chinesesimp"; MessagesFile: "installer\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加图标:"

[Files]
Source: "dist\WxSum\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\WxSum"; Filename: "{app}\WxSum.exe"
Name: "{group}\卸载 WxSum"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WxSum"; Filename: "{app}\WxSum.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WxSum.exe"; Description: "立即启动 WxSum"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 仅清理程序运行产生的空目录，不碰 %LOCALAPPDATA%\WxSum 用户数据
Type: filesandordirs; Name: "{app}\输出"
