#ifndef AppBuild
  #define AppBuild "..\build-work\dist\PhotoBooth"
#endif
#ifndef OutputRoot
  #define OutputRoot "..\releases"
#endif
[Setup]
AppId={{BA3824F2-79A7-4D85-BC2D-23C3BDFD24B9}
AppName=美颜自拍亭
AppVersion=1.1.1
AppPublisher=爱打游戏的Arweil
AppComments=作者：爱打游戏的Arweil；联系方式 QQ:759377831
AppCopyright=Copyright (c) 2026 爱打游戏的Arweil
DefaultDirName={localappdata}\Programs\OpenPhotoBooth
DefaultGroupName=美颜自拍亭
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputRoot}
OutputBaseFilename=BeautyPhotoBooth-1.1.1-Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\PhotoBooth.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
InfoBeforeFile=..\docs\FREE_NOTICE.txt
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
LanguageCodePage=0
DialogFontName=Microsoft YaHei UI

[Messages]
SetupAppTitle=安装
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=卸载 %1
InformationTitle=提示
ConfirmTitle=确认
ErrorTitle=错误
ButtonBack=< 上一步
ButtonNext=下一步 >
ButtonInstall=安装
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是
ButtonNo=否
ButtonFinish=完成
ButtonBrowse=浏览...
ButtonWizardBrowse=浏览...
ButtonNewFolder=新建文件夹
ClickNext=点击“下一步”继续，或点击“取消”退出安装。
WelcomeLabel1=欢迎安装 [name]
WelcomeLabel2=本向导将安装 [name/ver]。%n%n本软件开源且免费，请注意不要受骗。
WizardLicense=开源许可
LicenseLabel=请阅读以下开源许可。
LicenseLabel3=项目代码采用 MIT 许可证。第三方组件按各自许可证提供。
LicenseAccepted=我接受许可条款
LicenseNotAccepted=我不接受许可条款
WizardInfoBefore=免费开源提示
InfoBeforeLabel=本软件开源且免费，请注意不要受骗。
InfoBeforeClickLabel=阅读后点击“下一步”继续。
WizardSelectDir=选择安装目录
SelectDirDesc=将 [name] 安装到哪里？
SelectDirLabel3=将安装到以下当前用户可写目录。
SelectDirBrowseLabel=点击“下一步”继续，或点击“浏览”选择其他可写目录。
DiskSpaceGBLabel=至少需要 [gb] GB 可用空间。
DiskSpaceMBLabel=至少需要 [mb] MB 可用空间。
WizardReady=准备安装
ReadyLabel1=已准备好安装 [name]。
ReadyLabel2a=点击“安装”开始，或点击“上一步”修改设置。
ReadyLabel2b=点击“安装”开始。
ReadyMemoDir=安装目录：
ReadyMemoGroup=开始菜单：
WizardPreparing=准备安装
PreparingDesc=正在准备安装 [name]。
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候。
FinishedHeadingLabel=[name] 安装完成
FinishedLabel=已完成安装。桌面已创建“美颜自拍亭”快捷方式。
FinishedLabelNoIcons=已完成安装 [name]。
ClickFinish=点击“完成”退出安装向导。
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。确定退出吗？
UninstallStatusLabel=正在卸载 %1，请稍候。
ConfirmUninstall=确定卸载 %1 吗？配置、素材和客户照片将保留。
UninstalledAll=%1 已卸载。配置、素材和照片保留在原安装目录。

[Dirs]
Name: "{app}\data"; Flags: uninsneveruninstall

[Files]
Source: "{#AppBuild}\PhotoBooth.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#AppBuild}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#AppBuild}\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall
Source: "{#AppBuild}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#AppBuild}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#AppBuild}\config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#AppBuild}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#AppBuild}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#AppBuild}\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#AppBuild}\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\美颜自拍亭"; Filename: "{app}\PhotoBooth.exe"; Parameters: "--fullscreen"; WorkingDir: "{app}"
Name: "{group}\美颜自拍亭"; Filename: "{app}\PhotoBooth.exe"; Parameters: "--fullscreen"; WorkingDir: "{app}"
Name: "{group}\模拟演练（不打印）"; Filename: "{app}\PhotoBooth.exe"; Parameters: "--demo --fullscreen"; WorkingDir: "{app}"
Name: "{group}\操作指南"; Filename: "{app}\docs\操作指南.pdf"

[Run]
Filename: "{app}\PhotoBooth.exe"; Parameters: "--fullscreen"; Description: "启动美颜自拍亭"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WizardSilent then
    MsgBox('本软件开源且免费，请注意不要受骗。' + #13#10 + '作者：爱打游戏的Arweil；联系方式 QQ:759377831' + #13#10 + #13#10 +
      '程序不收取激活费或订阅费。相机接口与打印机驱动需在本机另行合法安装。', mbInformation, MB_OK);
end;

procedure RemoveOwnOldShortcut(const ShortcutName: String);
var
  Shell: Variant;
  Link: Variant;
begin
  if FileExists(ShortcutName) then
    try
      Shell := CreateOleObject('WScript.Shell');
      Link := Shell.CreateShortcut(ShortcutName);
      if CompareText(Link.TargetPath, ExpandConstant('{app}\PhotoBooth.exe')) = 0 then
        DeleteFile(ShortcutName);
    except
      Log('Old shortcut retained because ownership could not be verified.');
    end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RemoveOwnOldShortcut(ExpandConstant('{userdesktop}\开源自拍亭.lnk'));
end;
