"""简历解析：支持 .pdf 与 .md。

PDF 策略（轻量 OCR）：
1. 先用 pypdf 抽取数字文本层；
2. 若文本过少（判定为扫描版/图片 PDF），则用 pypdfium2 渲染栅格化后再交给
   Tesseract (pytesseract) 做 OCR。

同时，无论文本版还是扫描版，都会把 PDF 按页渲染成图片（base64），以便连同文本
一起发给模型处理。
"""
from __future__ import annotations

import base64
import io
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field

import pypdf
import pytesseract
from PIL import Image

from hr_cv.config import settings

# Windows 下子进程默认会分配新的控制台窗口——打包版（--noconsole）里每次调用
# tesseract 都会闪现黑窗。这里给所有子进程统一加 CREATE_NO_WINDOW；
# 子进程均通过管道收发数据，该标志不影响输入输出。
if sys.platform == "win32":
    _orig_popen = subprocess.Popen

    def _no_window_popen(*args, **kwargs):
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        return _orig_popen(*args, **kwargs)

    subprocess.Popen = _no_window_popen

# 扩展名 -> 文档类型
SUPPORTED_EXTENSIONS = {".pdf", ".md"}

# 文本层字符数低于该值则判定为扫描版，触发 OCR
TEXT_MIN_CHARS = 60

# 渲染参数：OCR 用高分辨率，发给模型的图片用较低分辨率 + JPEG 压缩
OCR_SCALE = 2.0
VISION_MAX_PAGES = 3
MODEL_IMG_MAX_WIDTH = 1000
MODEL_IMG_JPEG_QUALITY = 70


def _pages_to_data_urls(images: list[Image.Image]) -> list[str]:
    """页面图片 -> 降采样 + JPEG 压缩后的 base64 data URL（控制请求体积）。"""
    urls = []
    for img in images:
        if img.width > MODEL_IMG_MAX_WIDTH:
            ratio = MODEL_IMG_MAX_WIDTH / img.width
            img = img.resize((MODEL_IMG_MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=MODEL_IMG_JPEG_QUALITY)
        urls.append(f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}")
    return urls


@dataclass
class ParsedResume:
    id: str
    filename: str
    format: str  # "pdf" | "md"
    content: str
    method: str  # "pypdf" | "ocr" | "md"
    # PDF 按页渲染的 base64 data URL，随请求发给模型
    pages: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    """压缩多余空白，便于后续喂给 LLM。"""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf_text_blob(data: bytes) -> str:
    """用 pypdf 抽取全部页的文本层。"""
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(pages)


def _configure_tesseract() -> None:
    """应用 tesseract 可执行路径。"""
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    # 确保语言数据目录可用（config 已在启动时设置 TESSDATA_PREFIX）
    pytesseract.get_tesseract_version()  # noqa: S110


def _render_pages(data: bytes, scale: float = OCR_SCALE, max_pages: int = VISION_MAX_PAGES) -> list[Image.Image]:
    """把 PDF 逐页渲染为灰度 PIL 图像。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    images = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        bitmap = page.render(scale=scale)
        img = bitmap.to_pil().convert("L")
        images.append(img)
    return images


def _ocr_images(images: list[Image.Image]) -> str:
    """对逐页图像做 OCR，转化成文本（分段拼接）。"""
    parts = []
    for img in images:
        parts.append(pytesseract.image_to_string(img, lang="chi_sim+eng"))
    return "\n\n".join(parts)


def quick_validate(filename: str, data: bytes) -> str | None:
    """上传时的轻量校验：PDF 必须能被 pdfium 打开（不渲染、不 OCR）。

    返回错误文案；None 表示通过。MD 仅要求可解码（utf-8 / gbk）。
    """
    lower = filename.lower()
    try:
        if lower.endswith(".pdf"):
            import pypdfium2 as pdfium

            with pdfium.PdfDocument(data) as doc:
                _ = len(doc)
        else:  # .md
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                data.decode("gbk")
        return None
    except Exception as e:  # noqa: BLE001 - 校验本身就要捕获一切打开错误
        return f"{type(e).__name__}: {e}"


def parse_file(filename: str, data: bytes) -> ParsedResume:
    """按扩展名解析一个简历文件，返回结构化结果。"""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型: {filename}（仅支持 PDF 与 MD）")

    rid = uuid.uuid4().hex

    if ext == ".md":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", errors="replace")
        return ParsedResume(rid, filename, "md", _normalize(text), "md")

    # PDF：渲染页面（供 OCR 与视觉模型共用）
    images = _render_pages(data)
    pages = _pages_to_data_urls(images) if settings.pdf_vision else []

    text = _extract_pdf_text_blob(data)
    if len(text.strip()) < TEXT_MIN_CHARS:
        _configure_tesseract()
        text = _ocr_images(images)
        method = "ocr"
    else:
        method = "pypdf"

    return ParsedResume(rid, filename, "pdf", _normalize(text), method, pages)


def parse_many(files: list[tuple[str, bytes]]) -> list[ParsedResume]:
    """批量解析多个文件，逐个出错不阻断整体。"""
    results: list[ParsedResume] = []
    for name, data in files:
        try:
            results.append(parse_file(name, data))
        except ValueError as e:  # 不支持的格式
            raise ValueError(str(e))
    return results