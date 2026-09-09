"""打包版桌面入口：pywebview 原生窗口 + 本地 uvicorn 服务 + 单实例守卫。

仅打包（PyInstaller）场景使用本模块：打包脚本以 ``hr_cv.desktop:main`` 为入口；
开发模式 ``uv run hr-cv`` 走 ``hr_cv.main:run``（8000 起、杀旧实例），不经过本文件。

与 dev 模式的关键差异：
- 环境变量必须在导入任何 hr_cv 模块之前设置好（config.py 在 import 时读取环境变量）；
- 单实例策略为「复用」而非「杀旧」：从 8765 起探测端口，被占端口若 /api/health
  验证为本应用则直接复用，否则视为外部程序占用顺延接管；
- 数据目录默认落 %LOCALAPPDATA%\\hr-cv（可用 HR_CV_DATA_DIR 覆盖）。
"""
from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# 无控制台护栏：PyInstaller --noconsole 窗口模式下 stdout/stderr 为 None，
# 任何库触碰（如 uvicorn 日志的 sys.stdout.isatty()）都会直接崩溃。
# 统一重定向到 devnull，必须在最早期执行。
# ---------------------------------------------------------------------------
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# ---------------------------------------------------------------------------
# 环境设置：必须先于任何 hr_cv 导入（config.py 在 import 时读取环境变量）
# ---------------------------------------------------------------------------
# 打包资源基目录：PyInstaller 冻结态为解包目录 _MEIPASS；dev 直跑为项目根
# （src/hr_cv/desktop.py 的上两级），便于直接运行本模块做无头验证。
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))

# 随包 Tesseract：打包脚本把 tesseract.exe 与 tessdata 收集到 <base>/tesseract/
_TESSERACT_EXE = _BASE / "tesseract" / "tesseract.exe"
if _TESSERACT_EXE.exists():
    os.environ.setdefault("TESSERACT_CMD", str(_TESSERACT_EXE))
    os.environ.setdefault("TESSDATA_PREFIX", str(_BASE / "tesseract" / "tessdata"))

# 数据目录：打包版落 %LOCALAPPDATA%\hr-cv（SQLite、日志、desktop.lock）
os.environ.setdefault(
    "HR_CV_DATA_DIR",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hr-cv"),
)

# 打包版不读取 .env（开发者的密钥不应随包带到用户机器），LLM 配置走页面「模型接入」
os.environ["HR_CV_NO_DOTENV"] = "1"

# 无头开关：HR_CV_HEADLESS=1 时跳过 GUI 常驻服务（供自动化测试驱动本入口）
HEADLESS = os.environ.get("HR_CV_HEADLESS", "") == "1"

# 命名互斥体：供安装/卸载器（Inno AppMutex）检测应用是否在运行，
# 防止文件被删除时应用仍在运行的"半死"状态。模块级引用防止 GC 释放句柄。
_MUTEX = None
if sys.platform == "win32":
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "AI简历筛选助手运行互斥体")

# ---- 环境就绪，此后才允许导入 hr_cv ----
from hr_cv.config import settings  # noqa: E402
from hr_cv import main as _server_mod  # noqa: E402,F401  仅导入以构建 FastAPI app（uvicorn 按字符串加载同一模块），绝不调用其 run()

logger = logging.getLogger("hr_cv.desktop")

PORT_START = 8765    # 桌面模式起始端口
PORT_ATTEMPTS = 20   # 8765~8784 共 20 个候选
_LOCK_FILE = "desktop.lock"


def _port_free(port: int) -> bool:
    """与 main.py 相同的 bind 探测：能成功绑定即视为空闲。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_ours(port: int) -> bool:
    """GET /api/health 判断端口上的服务是否为本应用（app == "hr-cv"）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "hr-cv"
    except Exception:  # noqa: BLE001  连接失败 / 404 / 非 JSON 都视为「不是本应用」
        return False


def _lock_path() -> Path:
    return settings.data_dir / _LOCK_FILE


def _cleanup_lock() -> None:
    """atexit 钩子：仅当锁内 pid 仍是当前进程时删除锁文件（复用模式绝不碰别人的锁）。"""
    lp = _lock_path()
    try:
        data = json.loads(lp.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception:  # noqa: BLE001
        logger.exception("读取锁文件失败，跳过清理: %s", lp)
        return
    if data.get("pid") != os.getpid():
        logger.info("锁文件属于其他实例 (pid=%s)，不清理", data.get("pid"))
        return
    try:
        lp.unlink(missing_ok=True)
        logger.info("已清理锁文件: %s", lp)
    except OSError:
        logger.exception("删除锁文件失败: %s", lp)


def _drop_stale_lock() -> None:
    """启动前清理陈旧锁：锁内端口已空闲（持有者已死）或 pid 为当前进程 → 删除。"""
    lp = _lock_path()
    if not lp.exists():
        return
    try:
        data = json.loads(lp.read_text(encoding="utf-8"))
        port = int(data.get("port") or 0)
        pid = int(data.get("pid") or 0)
    except Exception:  # noqa: BLE001  锁文件损坏同样视为陈旧
        logger.warning("锁文件损坏，按陈旧锁清理: %s", lp)
        lp.unlink(missing_ok=True)
        return
    if pid == os.getpid() or (port and _port_free(port)):
        logger.info("清理陈旧锁 (pid=%d port=%d): %s", pid, port, lp)
        lp.unlink(missing_ok=True)


def _take_ownership(port: int) -> None:
    """服务端认领：建数据目录、写锁文件、注册退出清理。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _lock_path().write_text(
        json.dumps({"pid": os.getpid(), "port": port, "ts": time.time()}),
        encoding="utf-8",
    )
    atexit.register(_cleanup_lock)
    logger.info("认领端口 %d，锁文件: %s", port, _lock_path())


def _claim_port() -> tuple[int, bool]:
    """从 8765 起探测 20 个端口，返回 (port, reuse)。

    - 空闲 → 认领为服务端（写锁 + atexit 清理），返回 (port, False)；
    - 被占但 /api/health 验证为本应用 → 复用已有实例，返回 (port, True)；
    - 被其他程序占用 → 顺延。
    """
    _drop_stale_lock()
    for port in range(PORT_START, PORT_START + PORT_ATTEMPTS):
        if _port_free(port):
            _take_ownership(port)
            return port, False
        if _is_ours(port):
            return port, True
        logger.info("端口 %d 被其他程序占用，顺延探测", port)
    raise RuntimeError(f"{PORT_START}~{PORT_START + PORT_ATTEMPTS - 1} 端口均不可用，请手动释放后重试")


def _start_server(port: int):
    """后台线程启动 uvicorn 服务 hr_cv.main:app（绝不调用 main.run()）。

    log_config=None：跳过 uvicorn 自带的 dictConfig（其彩色 formatter 会触碰
    sys.stdout，无控制台模式下为 None；本应用日志统一走 config.setup_logging
    的轮转文件 app.log，uvicorn 日志经 root 传播同样落入该文件）。
    """
    import uvicorn

    cfg = uvicorn.Config(
        "hr_cv.main:app",
        host="127.0.0.1",
        port=port,
        log_config=None,
        timeout_graceful_shutdown=3,  # 页面残留的 keep-alive 连接不阻塞退出
    )
    srv = uvicorn.Server(cfg)
    _CLOSE_STATE["server"] = srv
    threading.Thread(target=srv.run, daemon=True, name="hr-cv-server").start()
    return srv


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    """轮询 /api/health 直到服务就绪，超时返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_ours(port):
            return True
        time.sleep(0.2)
    return False


def _run_headless(port: int, server) -> None:
    """无头模式：不弹窗，常驻服务直到被终止（供自动化测试）。"""
    if os.environ.get("HR_CV_HEADLESS_EXIT") == "1":
        # 复用模式专用出口：打印后立即返回（进程退出，不影响被复用的实例）
        print(f"HR_CV_REUSE_OK port={port}", flush=True)
        return
    print(f"HR_CV_READY port={port}", flush=True)
    try:
        while server and not server.should_exit:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass


class _JsApi:
    """暴露给前端 window.pywebview.api 的桥接。

    WebView2 默认不处理文件下载，页面内 location.href/blob 下载在桌面窗口里
    会"没反应"；这里用原生保存对话框让日志导出真正落盘。
    """

    def __init__(self, port: int):
        self._port = port

    def request_exit(self) -> None:
        """由页面「退出确认」弹窗调用：真正关闭窗口（JS API 线程调用安全）。"""
        logger.info("页面确认退出，销毁窗口")
        _force_destroy()

    def save_export(self) -> str:
        """弹出原生另存为对话框，把日志 zip 写入所选路径；取消返回空串。"""
        try:
            import webview
            window = webview.windows[0] if webview.windows else None
            if not window:
                return ""
            target = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=f"hr-cv-logs-{time.strftime('%Y%m%d-%H%M')}.zip",
            )
            if not target:
                return ""
            path = target[0] if isinstance(target, (list, tuple)) else str(target)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self._port}/api/logs/export", timeout=120
            ) as r:
                data = r.read()
            with open(path, "wb") as f:
                f.write(data)
            logger.info("日志已通过原生对话框导出: %s（%d 字节）", path, len(data))
            return path
        except Exception:  # noqa: BLE001 - 原始堆栈进日志，前端展示错误信息
            logger.exception("桌面导出日志失败")
            raise


# 关闭状态：destroy() 会再次触发 closing 事件，必须用标志区分
# 「用户点 ×」（拦截并弹确认）与「确认后的程序化销毁」（放行）。
_CLOSE_STATE = {"destroy_requested": False, "window": None, "server": None}


def _force_destroy() -> None:
    """确认退出后的程序化销毁：先置放行标志再销毁窗口（无头模式则直接停服）。"""
    _CLOSE_STATE["destroy_requested"] = True
    server = _CLOSE_STATE.get("server")
    if server:
        server.should_exit = True
    window = _CLOSE_STATE.get("window")
    if window:
        window.destroy()


def _watch_exit_event() -> None:
    """监听安装/卸载器（Inno [Code]）的退出信号：触发后绕过确认直接优雅退出。"""
    WAIT_OBJECT_0 = 0x0
    INFINITE = 0xFFFFFFFF
    while True:
        res = ctypes.windll.kernel32.WaitForSingleObject(_EXIT_EVENT, INFINITE)
        if res == WAIT_OBJECT_0:
            logger.info("收到安装/卸载器退出信号，优雅关闭")
            _force_destroy()
            return


# 命名退出事件：与 installer.iss 的 AppExitEventName 保持一致
_EXIT_EVENT = None
if sys.platform == "win32":
    _EXIT_EVENT = ctypes.windll.kernel32.CreateEventW(None, False, False, "AI简历筛选助手退出事件")
    threading.Thread(target=_watch_exit_event, daemon=True, name="hr-cv-exit-watcher").start()


def _native_exit_confirm(window) -> None:
    """原生 MessageBox 兜底确认（页面脚本缺失/唤起失败时使用），确认后销毁窗口。"""
    if sys.platform == "win32":
        MB_OKCANCEL, MB_ICONQUESTION, MB_TOPMOST, IDOK = 0x0, 0x20, 0x40000, 1
        res = ctypes.windll.user32.MessageBoxW(
            0,
            "确定退出AI简历筛选助手吗？",
            "AI简历筛选助手",
            MB_OKCANCEL | MB_ICONQUESTION | MB_TOPMOST,
        )
        if res == IDOK:
            _force_destroy()
    else:
        _force_destroy()


def main(driver=None) -> None:
    """桌面入口（PyInstaller 以 hr_cv.desktop:main 启动，与打包脚本约定的函数名）。

    driver：可选的后台钩子，在窗口显示后于独立线程执行（供自动化测试驱动
    退出确认全流程）；生产环境不传，行为不变。
    """
    port, reuse = _claim_port()
    server = None
    if reuse:
        logger.info("检测到已有实例 (port=%d)，直接复用", port)
    else:
        server = _start_server(port)
        if not _wait_ready(port):
            raise RuntimeError("本地服务启动失败")

    if HEADLESS:
        _run_headless(port, server)
        return

    import webview

    window = webview.create_window(
        "AI简历筛选助手",
        f"http://127.0.0.1:{port}",
        width=1200,
        height=800,
        min_size=(960, 640),
        js_api=_JsApi(port),
    )
    _CLOSE_STATE["window"] = window

    confirm_flag = {"busy": False}  # 防止重复点 × 时弹出多个确认框

    def on_closing():
        """关闭前二次确认。

        注意：closing 事件在 UI 线程同步触发，此处严禁调用 evaluate_js 等
        需要 UI 线程的接口（互相等待 → 窗口未响应，确认框永远弹不出来）。
        做法：先返回 False 取消本次系统关闭，再由后台线程 evaluate_js 唤起
        页面内居中的「退出确认」弹窗（后台线程调用 evaluate_js 是安全的）；
        页面脚本缺失（旧缓存）时回退原生 MessageBox。
        """
        if _CLOSE_STATE["destroy_requested"]:
            return True  # 页面/原生确认后的程序化销毁（destroy 会再次触发本事件），放行
        if confirm_flag["busy"]:
            return False
        confirm_flag["busy"] = True

        def ask_page():
            # evaluate_js 在 WebView2 异常时可能永久挂起 → busy 卡死、后续点 × 全部失效。
            # 因此放到带超时的内层线程：6 秒无响应即回退原生对话框。
            result = {"shown": None}

            def _invoke():
                try:
                    result["shown"] = window.evaluate_js(
                        "window.hrExitConfirm ? window.hrExitConfirm() : false"
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("evaluate_js 唤起退出确认失败")

            t = threading.Thread(target=_invoke, daemon=True)
            t.start()
            t.join(timeout=6)
            try:
                if result["shown"] is True:
                    logger.info("页面退出确认弹窗已展示")
                elif result["shown"] is False:
                    logger.info("页面退出确认脚本缺失（旧缓存），回退原生对话框")
                    _native_exit_confirm(window)
                else:
                    logger.warning("evaluate_js 唤起退出确认超时/失败，回退原生对话框")
                    _native_exit_confirm(window)
            except Exception:  # noqa: BLE001
                logger.exception("退出确认回退失败")
            finally:
                confirm_flag["busy"] = False

        threading.Thread(target=ask_page, daemon=True).start()
        return False  # 取消本次关闭，是否真正退出由页面确认结果决定

    window.events.closing += on_closing
    if driver is not None:
        webview.start(driver)
    else:
        webview.start()

    # 复用模式：server 为 None，窗口关闭仅退出当前进程，不触碰被复用的服务；
    # 服务模式：窗口关闭后优雅停掉本进程启动的服务（锁文件由 atexit 清理）。
    if server:
        server.should_exit = True


if __name__ == "__main__":
    main()
