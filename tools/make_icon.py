"""把 Gemini 生成的圆形徽章做成 Windows 图标(.ico)。

为什么不是"直接另存为 ico"
------------------------
原图是 2048×2048 的 JPEG:圆形徽章 + 四角白底。
直接转会得到**白方块**图标 —— 在任务栏/桌面上很难看,深色主题下更明显。
所以要做三件事:

1. 抠底:把圆外的白色变成透明。这里**不做全局"白色→透明"**,
   否则徽章里 "DEEPSEEK" / "ASPEN PLUS" 的白色笔画会被一起镂空。
   改为按几何判定:画的是正圆,就按"到圆心的距离"给 alpha,圆外透明、边缘抗锯齿。
2. 按圆裁切:去掉四周白边,让徽章撑满图标。
3. 多尺寸:16/24/32/48/64/128/256 各存一份。
   Windows 会按显示场景挑合适的那张,只放一张 256 的话 16px 下会糊成一团。

用法:
    python tools/make_icon.py "Gemini_Generated_Image_xxx.jpg" assets/aspen-insight.ico
"""
from __future__ import annotations

import sys

from PIL import Image

# 这些尺寸覆盖:桌面小图标(16)、列表(24/32)、中等(48)、大图标(64/128)、超大(256)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def load_and_cut(path: str) -> Image.Image:
    """读图 → 圆形抠底 → 按圆裁切,返回正方形 RGBA。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size

    # 1) 找徽章的外接框:徽章本身是深蓝,亮度远低于白底
    gray = im.convert("L")
    mask = gray.point(lambda v: 255 if v < 200 else 0)
    box = mask.getbbox()
    if not box:
        raise SystemExit("没能在图里找到徽章(整张图都太亮?)")
    x0, y0, x1, y1 = box

    # 2) 圆心与半径:取外接框中心 + 平均半边长(比"最大距离"更抗噪)
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    radius = ((x1 - x0) + (y1 - y0)) / 4

    # 3) 按半径生成 alpha:圆内不透明,边缘 1.5px 渐隐(抗锯齿)
    side = int(round(radius * 2))
    left = int(round(cx - radius))
    top = int(round(cy - radius))
    im = im.crop((left, top, left + side, top + side))

    alpha = Image.new("L", (side, side), 0)
    px = alpha.load()
    r_in = radius - 1.5
    r_out = radius + 0.5
    for y in range(side):
        dy = y + 0.5 - radius
        for x in range(side):
            dx = x + 0.5 - radius
            d2 = dx * dx + dy * dy
            if d2 <= r_in * r_in:
                px[x, y] = 255
            elif d2 >= r_out * r_out:
                px[x, y] = 0
            else:
                d = d2 ** 0.5
                px[x, y] = int(255 * (r_out - d) / (r_out - r_in))

    im = im.convert("RGBA")
    im.putalpha(alpha)
    return im


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]

    master = load_and_cut(src)
    print(f"裁切后 {master.size[0]}×{master.size[1]}")

    # 先缩到 256 再写 ICO:Pillow 会从这张按需生成各尺寸,边缘比从 2048 直接采样更一致
    big = master.resize((256, 256), Image.LANCZOS)
    big.save(dst, format="ICO", sizes=[(s, s) for s in SIZES if s <= 256])
    print(f"已写出 {dst}")

    # 顺带导一张 256 的 PNG:预览/文档用,也方便检查透明边缘
    png = dst.rsplit(".", 1)[0] + "_256.png"
    big.save(png)
    print(f"预览图 {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
