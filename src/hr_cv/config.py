"""全局配置：从环境变量 / .env 读取 LLM、数据目录与日志设置；负责日志初始化。"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
from pathlib import Path

from dotenv import load_dotenv

# 打包版（desktop.py 设置 HR_CV_NO_DOTENV=1）不读取 .env：
# 避免开发者的密钥被带进用户机器，打包版 LLM 配置一律通过页面「模型接入」保存。
if os.getenv("HR_CV_NO_DOTENV") != "1":
    load_dotenv()

APP_VERSION = "0.5.0"

# 项目根（src/hr_cv/config.py -> 上两级）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 若本机安装了 Tesseract 且含中文数据，设置 TESSDATA_PREFIX 以便 OCR 找到 chi_sim
_TESSDATA_HINTS = [
    os.getenv("TESSDATA_PREFIX", ""),
    str(Path(os.getenv("LOCALAPPDATA", "")) / "Tesseract-OCR" / "tessdata"),
    "C:/Program Files/Tesseract-OCR/tessdata",
]
for _td in _TESSDATA_HINTS:
    if _td and Path(_td).is_dir():
        os.environ["TESSDATA_PREFIX"] = _td
        break


def _mask(key: str) -> str:
    """API key 打码：仅日志/导出展示用。"""
    if not key:
        return "(未设置)"
    return f"{key[:6]}****{key[-4:]}" if len(key) > 12 else "****"


class Settings:
    # OpenAI 兼容接口（可对接 DeepSeek / Qwen / Moonshot 等）
    api_key: str = os.getenv("OPENAI_API_KEY", "")
    base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model: str = os.getenv("OPENAI_MODEL", "deepseek-v4-flash-vision-exp")
    # 是否允许将 PDF 渲染成图片发给模型（默认开启）
    pdf_vision: bool = os.getenv("PDF_VISION", "1").lower() in ("1", "true", "yes")

    # Tesseract OCR 可执行文件的路径（Windows 若未加入 PATH 可在此指定）
    tesseract_cmd: str = os.getenv("TESSERACT_CMD", "C:/Program Files/Tesseract-OCR/tesseract.exe")

    # 可选推理力度（low/high/max）：留空 = 不传参（走 DeepSeek 官方默认 high）。
    # low 可显著缩短思考阶段耗时，但回答质量可能下降，由使用方自行权衡。
    _effort = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
    reasoning_effort: str = _effort if _effort in ("low", "high", "max") else ""

    # 数据目录（SQLite 库、日志、遗留 JSON 迁移备份），可整体迁移到 %LOCALAPPDATA%
    data_dir: Path = Path(os.getenv("HR_CV_DATA_DIR", str(_PROJECT_ROOT / ".data")))
    # 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str = os.getenv("HR_CV_LOG_LEVEL", "INFO").upper()


settings = Settings()


def config_summary() -> str:
    """配置摘要（API key 脱敏），供启动日志与导出 meta 使用。"""
    return (
        f"version={APP_VERSION} | OS={platform.platform()} | Python={sys.version.split()[0]} | "
        f"data_dir={settings.data_dir} | model={settings.model} | "
        f"base_url={settings.base_url} | api_key={_mask(settings.api_key)} | "
        f"pdf_vision={settings.pdf_vision} | tesseract={settings.tesseract_cmd}"
    )


def setup_logging() -> None:
    """初始化日志：轮转文件 {data_dir}/logs/app.log（5MB × 5），原始格式、不吞异常。"""
    logs_dir = settings.data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        logs_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s:%(lineno)d | %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    root.addHandler(handler)
    logging.getLogger("hr_cv").info("应用启动 | %s", config_summary())
