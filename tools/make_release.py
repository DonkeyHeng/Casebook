# -*- coding: utf-8 -*-
"""tools/make_release.py —— 打两个发布包(都在 `data/preview/dist/` 下)。

1. **源码包** `Casebook-<版本>.zip`
   `git archive HEAD`,带 `Casebook/` 前缀;只保留运行需要的东西 ——
   开发者文档 / 占位目录靠 `.gitattributes` 的 `export-ignore` 挡掉,
   再塞一份中文 `安装说明.txt`(来自 `docs/install-text.txt`)。
2. **Windows 包** `Casebook-windows-x64.zip`
   `Casebook.exe` + `安装说明.txt` + `LICENSE`,解压出来只有这三个文件,
   给不装 Python 的同学用。

```bash
python -m tools.build_exe        # 先出 exe
python -m tools.make_release     # 再打两个包(会打印大小与 SHA-256)
```

上传与 Release 说明由发布流程负责(见 `docs/install.md`),本脚本只管出包。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "data" / "preview" / "dist"
TXT_SRC = ROOT / "docs" / "install-text.txt"
TXT_NAME = "安装说明.txt"
PREFIX = "Casebook"
EXE = DIST / "Casebook.exe"
VERSION = "1.2.0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _txt_bytes() -> bytes:
    """`安装说明.txt` 的字节内容。

    仓库里是 LF,但下载的人是拿记事本打开的 —— 统一转成 CRLF,
    免得老系统上整份文档挤成一行。
    """
    raw = TXT_SRC.read_bytes().replace(b"\r\n", b"\n")
    return raw.replace(b"\n", b"\r\n")


def _add_file(zip_path: Path, arcname: str, content: bytes) -> None:
    """往已有 zip 里加一个文件(git archive 不支持指定目标路径,只能改写一遍)。"""
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(zip_path) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr(arcname, content)
    tmp.replace(zip_path)


def build_source_zip() -> Path:
    out = DIST / f"{PREFIX}-{VERSION}.zip"
    out.unlink(missing_ok=True)
    subprocess.run(["git", "archive", "--format=zip", f"--prefix={PREFIX}/",
                    "-o", str(out), "HEAD"], cwd=str(ROOT), check=True)
    _add_file(out, f"{PREFIX}/{TXT_NAME}", _txt_bytes())
    return out


def build_exe_zip() -> Path:
    out = DIST / f"{PREFIX}-windows-x64.zip"
    if not EXE.is_file():
        raise FileNotFoundError(f"没有 exe,先跑 python -m tools.build_exe:{EXE}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(EXE, f"{PREFIX}/{EXE.name}")
        z.writestr(f"{PREFIX}/{TXT_NAME}", _txt_bytes())
        z.write(ROOT / "LICENSE", f"{PREFIX}/LICENSE")
    return out


def main() -> int:
    if not TXT_SRC.is_file():
        print("缺少中文说明:", TXT_SRC)
        return 1
    DIST.mkdir(parents=True, exist_ok=True)
    for path in (build_source_zip(), build_exe_zip()):
        size = path.stat().st_size
        print(f"{path.name}:{size:,} 字节(约 {size / 1024 / 1024:.2f} MB),"
              f"SHA-256 {_sha256(path)}")
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
        roots = sorted({n.split("/")[1] for n in names if n.count("/") >= 1})
        print(f"  文件 {len(names)} 个;根目录下:{'、'.join(roots)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
