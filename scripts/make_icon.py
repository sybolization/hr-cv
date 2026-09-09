"""生成应用图标 assets/hr-cv.ico（纯标准库，无 Pillow 依赖）。

ICO 结构：ICONDIR(6B) + N×ICONDIRENTRY(16B) + N 张 BMP 图像。
每张 BMP = BITMAPINFOHEADER(40B) + 自下而上 BGRA 像素 + AND 掩码（行按 4 字节对齐）。
绘制内容（归一化坐标，v 自上而下）：
  - 深色圆角方块底（#0d0d0d，圆角半径约 20%）；
  - 白色文档矩形（宽约 44%、高约 56%，居中偏上，圆角）；
  - 文档左上角橙红色（#ef4146）小三角折角；
  - 文档下部两根灰色（#e5e5e5）短横线。
像素级逐点判断，不做抗锯齿。
"""
from __future__ import annotations

import struct
from pathlib import Path

SIZES = (16, 32, 48, 256)
OUT = Path(__file__).resolve().parents[1] / "assets" / "hr-cv.ico"

# 调色板（RGBA）
BG = (13, 13, 13, 255)      # #0d0d0d 深色底
DOC = (255, 255, 255, 255)  # 白色文档
FOLD = (239, 65, 70, 255)   # #ef4146 折角
BAR = (229, 229, 229, 255)  # #e5e5e5 文本行


def _in_rrect(u: float, v: float, x0: float, y0: float, x1: float, y1: float,
              r_tl: float = 0.0, r_tr: float = 0.0, r_br: float = 0.0, r_bl: float = 0.0) -> bool:
    """点 (u,v) 是否在圆角矩形内（左上角为原点，v 向下；各角半径可独立为 0=直角）。"""
    if u < x0 or u > x1 or v < y0 or v > y1:
        return False
    for cx, cy, r, in_corner in (
        (x0 + r_tl, y0 + r_tl, r_tl, u < x0 + r_tl and v < y0 + r_tl),
        (x1 - r_tr, y0 + r_tr, r_tr, u > x1 - r_tr and v < y0 + r_tr),
        (x1 - r_br, y1 - r_br, r_br, u > x1 - r_br and v > y1 - r_br),
        (x0 + r_bl, y1 - r_bl, r_bl, u < x0 + r_bl and v > y1 - r_bl),
    ):
        if r > 0 and in_corner and (u - cx) ** 2 + (v - cy) ** 2 > r * r:
            return False
    return True


def _in_triangle(u: float, v: float, ax, ay, bx, by, cx, cy) -> bool:
    """点 (u,v) 是否在三角形内（符号面积法）。"""
    d1 = (u - bx) * (ay - by) - (ax - bx) * (v - by)
    d2 = (u - cx) * (by - cy) - (bx - cx) * (v - cy)
    d3 = (u - ax) * (cy - ay) - (cx - ax) * (v - ay)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _pixel(u: float, v: float) -> tuple[int, int, int, int]:
    """返回归一化坐标 (u,v) 处的 RGBA 颜色。"""
    # 白色文档：宽 44%（x 0.28~0.72）、高 56%（y 0.20~0.76，居中偏上）；
    # 左上角保持直角，供折角贴合
    if _in_rrect(u, v, 0.28, 0.20, 0.72, 0.76, r_tr=0.045, r_br=0.045, r_bl=0.045):
        if _in_triangle(u, v, 0.28, 0.20, 0.41, 0.20, 0.28, 0.33):  # 橙红折角
            return FOLD
        if 0.36 <= u <= 0.64 and (0.55 <= v <= 0.59 or 0.63 <= v <= 0.67):  # 灰色短横线
            return BAR
        return DOC
    # 深色圆角方块底（圆角半径 20%）
    if _in_rrect(u, v, 0.0, 0.0, 1.0, 1.0, 0.20, 0.20, 0.20, 0.20):
        return BG
    return (0, 0, 0, 0)  # 透明


def _bitmap(size: int) -> bytes:
    """单尺寸 BMP：BITMAPINFOHEADER + 自下而上 BGRA 像素 + AND 掩码。"""
    xor = bytearray()
    for row in range(size):
        v = ((size - 1 - row) + 0.5) / size  # bottom-up：第 row 行对应自上而下第 size-1-row 行
        for x in range(size):
            u = (x + 0.5) / size
            r, g, b, a = _pixel(u, v)
            xor += bytes((b, g, r, a))
    # AND 掩码：32bpp 全 0（透明度由 alpha 通道表达），每行按 4 字节对齐
    mask_row = ((size + 31) // 32) * 4
    and_mask = bytes(mask_row * size)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,                        # biSize
        size,                      # biWidth
        size * 2,                  # biHeight = 高×2（XOR + AND 两段）
        1,                         # biPlanes
        32,                        # biBitCount
        0,                         # biCompression = BI_RGB
        len(xor) + len(and_mask),  # biSizeImage
        0, 0, 0, 0,                # 像素密度 / 调色板色数 / 重要色
    )
    return header + bytes(xor) + and_mask


def build() -> bytes:
    """组装完整 ICO 文件字节。"""
    blobs = [(s, _bitmap(s)) for s in SIZES]
    icondir = struct.pack("<HHH", 0, 1, len(blobs))  # 保留字 0 / 类型 1=图标 / 数量
    offset = 6 + 16 * len(blobs)
    entries = b""
    images = b""
    for size, blob in blobs:
        entries += struct.pack(
            "<BBBBHHII",
            size % 256,  # 宽（256 → 0）
            size % 256,  # 高（256 → 0）
            0,           # 调色板色数
            0,           # 保留
            1,           # 颜色平面数
            32,          # 位深
            len(blob),   # 数据长度
            offset,      # 数据偏移
        )
        images += blob
        offset += len(blob)
    return icondir + entries + images


def verify(data: bytes) -> list[tuple[int, int]]:
    """回读校验 ICO 结构（数量 / 尺寸表 / 偏移连续 / 总长度），返回 [(尺寸, 字节数)]。"""
    reserved, ico_type, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0 and ico_type == 1, f"ICONDIR 头非法: type={ico_type}"
    assert count == len(SIZES), f"图像数量不符: {count} != {len(SIZES)}"
    expect_offset = 6 + 16 * count
    results = []
    for i in range(count):
        w, h, _, _, planes, bpp, size, off = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i: 22 + 16 * i])
        assert planes == 1 and bpp == 32, f"第 {i} 项 planes/bpp 非法"
        assert off == expect_offset, f"第 {i} 项偏移不连续: {off} != {expect_offset}"
        expect_offset += size
        real = 256 if (w == 0 and h == 0) else max(w, h)
        bi_size, bw, bh = struct.unpack("<Iii", data[off:off + 12])
        assert bi_size == 40, f"第 {i} 项 BITMAPINFOHEADER 非法"
        assert bw == real and bh == real * 2, f"第 {i} 项尺寸不符: {bw}x{bh} 应为 {real}x{real * 2}"
        results.append((real, size))
    assert expect_offset == len(data), f"总长度不符: {expect_offset} != {len(data)}"
    return results


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    OUT.write_bytes(data)
    sizes = verify(OUT.read_bytes())
    print(f"已生成 {OUT}（{len(data)} 字节）")
    for s, n in sizes:
        print(f"  - {s}x{s}: {n} 字节")
    print("校验 PASS：ICONDIR / 尺寸表 / 偏移与总长度一致")


if __name__ == "__main__":
    main()
