r"""把 Gemini 生成的圆形徽章做成 Windows 图标(.ico)。

为什么不是"直接另存为 ico"
------------------------
原图是 2048×2048 的 JPEG:圆形徽章 + 四角白底。
直接转会得到**白方块**图标 —— 在任务栏/桌面上很难看,深色主题下更明显。
所以要做三件事:

1. 抠底:把圆外的白色变成透明。这里**不做全局"白色→透明"**,
   否则徽章里 "DEEPSEEK" / "ASPEN PLUS" 的白色笔画会被一起镂空。
   改为按几何判定:画的是正圆,就按"到圆心的距离"给 alpha,圆外透明、边缘抗锯齿。
2. 按圆裁切:去掉四周白边,让徽章撑满图标。
3. 多尺寸:16/24/32/48/64/128 各存一份(256 太重,见下面 SIZES 的注释)。
   Windows 会按显示场景挑合适的那张,只放一张 256 的话 16px 下会糊成一团。

为什么自己拼 ICO 字节,不用 Pillow 的 ico 保存
-------------------------------------------
Pillow 会把**每个尺寸都写成 PNG 压缩条目**。浏览器(Chromium 读 favicon)没问题,
但资源管理器给**快捷方式**取图标走的是老 GDI 路径,只认 BMP(DIB)条目 ——
结果是:网页 favicon 正常,桌面图标却是一块白方块(2026-11-01 实际踩到)。
所以这里手写 ICO:所有尺寸都用 32bpp BMP + AND 掩码,兼容性最好。

用法:
    python tools/make_icon.py assets/icon-source.jpg assets/app-icon.ico

图标为什么要**再复制一份到 %LOCALAPPDATA%**
----------------------------------------
实测(2026-11-01):同一个 .ico,放在项目目录 `<projects-root>\...\assets\` 下,
资源管理器取不到 —— 桌面快捷方式显示通用的白色文件图标;
复制到 `%LOCALAPPDATA%\aspen-insight\` 后立刻正常。

疑因:项目目录带 DSH 沙箱加的 Deny ACE(`Everyone: DeleteSubdirectoriesAndFiles`),
Explorer 走那条"从 .lnk 的 IconLocation 取图标"的路径时失败。
（GDI+、ExtractIconEx、SHDefExtractIcon 在我的进程里都读得出来,唯独 Explorer 不认,极难查。）

结论:**桌面快捷方式必须指向项目目录之外的副本**,所以这个脚本会顺手部署一份。
"""
from __future__ import annotations

import os
import shutil
import struct
import sys

from PIL import Image

# 尺寸只做到 128。
#
# 256×256 惯例上要存成 PNG 压缩条目,存成 BMP 是非标准写法(单张就 270 KB)。
# 实测那条不是白页的原因(曾误判过),但既然没有 256 时 Windows 会拿 128 放大、
# 观感差别很小,就先保持 6 个尺寸,少一个变量。
SIZES = [16, 24, 32, 48, 64, 128]

# 桌面快捷方式用这份副本(项目目录里那份只给网页 favicon 用)
DEPLOY_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "aspen-insight"
)


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


def _dib_entry(img: Image.Image) -> bytes:
    """一张 RGBA 图 → 一个 ICO 内的 BMP(DIB)条目。

    结构 = BITMAPINFOHEADER(biHeight 写两倍) + XOR 位图(BGRA,自下而上)
         + AND 掩码(1 bit/像素,1=透明,行按 32 位对齐)。
    """
    w, h = img.size
    px = img.load()

    xor = bytearray()
    mask = bytearray()
    mask_row_bytes = ((w + 31) // 32) * 4

    for y in range(h - 1, -1, -1):          # 自下而上
        row = bytearray()
        bits = bytearray()
        cur = 0
        for x in range(w):
            r, g, b, a = px[x, y]
            row += bytes((b, g, r, a))      # BGRA
            if a == 0:
                cur |= 0x80 >> (x % 8)      # 全透明 → 掩码置 1
            if x % 8 == 7:
                bits.append(cur)
                cur = 0
        if w % 8:
            bits.append(cur)
        bits += b"\x00" * (mask_row_bytes - len(bits))
        xor += row
        mask += bits

    header = struct.pack(
        "<IiiHHIIiiII",
        40,            # biSize
        w, h * 2,      # biWidth / biHeight(XOR + AND 两张图叠起来)
        1, 32,         # biPlanes / biBitCount
        0,             # biCompression = BI_RGB
        len(xor) + len(mask),
        0, 0, 0, 0,
    )
    return header + bytes(xor) + bytes(mask)


def save_ico(images: list[Image.Image], dst: str) -> None:
    """把若干尺寸打包成 ICO(全部 BMP 条目)。"""
    blobs = [_dib_entry(im) for im in images]
    count = len(blobs)
    offset = 6 + 16 * count
    out = bytearray(struct.pack("<HHH", 0, 1, count))   # reserved / type=1(icon) / 条目数
    for im, blob in zip(images, blobs):
        w, h = im.size
        out += struct.pack(
            "<BBBBHHII",
            w % 256, h % 256,   # 256 要写成 0
            0, 0,               # 调色板数 / 保留
            1, 32,              # planes / bpp
            len(blob), offset,
        )
        offset += len(blob)
    for blob in blobs:
        out += blob
    with open(dst, "wb") as fh:
        fh.write(out)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]

    master = load_and_cut(src)
    print(f"裁切后 {master.size[0]}×{master.size[1]}")

    images = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    save_ico(images, dst)
    print(f"已写出 {dst}({len(images)} 个尺寸,全部 BMP 条目)")

    # 顺带导一张 256 的 PNG:预览/文档用,也方便检查透明边缘
    png = dst.rsplit(".", 1)[0] + "_256.png"
    images[-1].save(png)
    print(f"预览图 {png}")

    # 部署一份到项目目录之外 —— 桌面快捷方式必须指向这一份,否则 Explorer 取不到图标。
    # 部署失败不影响图标生成(比如在沙箱里跑,写用户目录会被拒),所以只警告。
    try:
        os.makedirs(DEPLOY_DIR, exist_ok=True)
        deployed = os.path.join(DEPLOY_DIR, os.path.basename(dst))
        shutil.copyfile(dst, deployed)
        print(f"已部署到 {deployed}")
        print("  桌面快捷方式的 IconLocation 要指向这个路径(项目目录里那份只给 favicon 用)")
    except OSError as exc:
        print(f"⚠️ 没能部署到 {DEPLOY_DIR}:{exc}")
        print(f"   桌面快捷方式需要图标放在项目目录之外,请手动把 {dst} 复制过去")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
