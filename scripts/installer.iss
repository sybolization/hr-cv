; Inno Setup 6 安装包脚本：AI简历筛选助手（用户级安装，无需管理员权限）
; 编译：ISCC.exe scripts\installer.iss，产物：dist\AI简历筛选助手-Setup.exe
; 注意：AppVersion 需与 src/hr_cv/config.py 的 APP_VERSION 保持一致（当前 0.5.0）

#define MyAppName "AI简历筛选助手"
#define MyAppVersion "0.5.0"
#define MyAppExeName "AI简历筛选助手.exe"

[Setup]
; 固定 GUID：升级/卸载凭此识别应用，勿改动
AppId={{85D84EC5-0DF9-45E9-BE07-A23E553087FE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=AI简历筛选助手
DefaultDirName={localappdata}\Programs\AI简历筛选助手
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=AI简历筛选助手-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; 应用运行中的自动关闭由 [Code] 段完成：安装/卸载前 SetEvent 退出信号并等待
; 应用优雅退出（最多 10 秒）。不用 AppMutex 硬拦截——它在静默模式下会直接
; 终止卸载器，轮不到退出信号生效。
[Tasks]
; 桌面快捷方式为可选项，默认勾选
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\AI简历筛选助手\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; 开始菜单快捷方式
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; 桌面快捷方式（对应上方 desktopicon 任务）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后可选启动应用（静默安装时不启动）
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

; 卸载清理策略见下方 [UninstallDelete]：
; 卸载时清空整个安装目录；用户数据（SQLite、日志、desktop.lock）
; 位于 %LOCALAPPDATA%\hr-cv —— 两者路径不同、天然隔离，卸载后用户数据完整保留。

; 卸载末尾清空整个安装目录（清掉 Inno 未登记的运行时生成文件与残留空目录）。
; 用户数据在 %LOCALAPPDATA%\hr-cv，与此路径隔离，不受影响。
[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// 安装/卸载前通知运行中的应用优雅退出：
// desktop.py 创建命名退出事件，这里 SetEvent 后等待互斥体释放（应用退出），
// 避免文件被占用导致卸载失败或应用进入"半关闭"状态。
const
  AppMutexName = 'AI简历筛选助手运行互斥体';
  AppExitEventName = 'AI简历筛选助手退出事件';
  EVENT_MODIFY_STATE = $0002;
  SYNCHRONIZE = $00100000;

function OpenEventW(dwDesiredAccess: Cardinal; bInheritHandle: LongBool; lpName: WideString): Cardinal;
  external 'OpenEventW@kernel32.dll stdcall';
function OpenMutexW(dwDesiredAccess: Cardinal; bInheritHandle: LongBool; lpName: WideString): Cardinal;
  external 'OpenMutexW@kernel32.dll stdcall';
function SetEvent(hEvent: Cardinal): LongBool;
  external 'SetEvent@kernel32.dll stdcall';
function CloseHandle(hObject: Cardinal): LongBool;
  external 'CloseHandle@kernel32.dll stdcall';

// 通知应用退出并等待其结束（最多约 10 秒）；返回 True 表示应用已退出或本就未运行
function SignalAppExitAndWait(): Boolean;
var
  hEvent, hMutex: Cardinal;
  i: Integer;
begin
  Result := True;
  hEvent := OpenEventW(EVENT_MODIFY_STATE, False, AppExitEventName);
  if hEvent = 0 then
    Exit; // 应用未运行（事件不存在），无需处理
  try
    SetEvent(hEvent);
    for i := 1 to 20 do
    begin
      hMutex := OpenMutexW(SYNCHRONIZE, False, AppMutexName);
      if hMutex = 0 then
        Exit; // 互斥体已释放 = 应用已退出
      CloseHandle(hMutex);
      Sleep(500);
    end;
    Result := False; // 超时仍未退出
  finally
    CloseHandle(hEvent);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  SignalAppExitAndWait();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  Log('InitializeUninstall: 发送应用退出信号');
  SignalAppExitAndWait();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    Log('usUninstall: 再次发送应用退出信号');
    SignalAppExitAndWait();
  end;
end;
