# -*- coding: utf-8 -*-
"""tools/build_exe.py —— 打 Windows 单文件 exe(PyInstaller)。

产物:`data/preview/dist/aspen-insight.exe`(单文件,双击即用,不需要装 Python)。

```bash
python -m tools.build_exe           # 直接打包
python -m tools.build_exe --check    # 只检查 PyInstaller 装没装
```

两个说明:

* PyInstaller 需要一个普通脚本当入口,而本项目的入口是 `python -m app.api`
  (`app/api/__main__.py`),所以这里临时生成一个两行的 `entry.py`,
  放在 `data/preview/build/` 下(已被 `.gitignore` 覆盖),不打进仓库。
* 静态文件(`app/web`)、图标(`assets`)、示例工况库(`samples/`)都塞进包里;
  运行时 `app/paths.py` 会把只读资源指到解包目录,把 `data/` 指到 exe 旁边。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "data" / "preview" / "dist"
BUILD = ROOT / "data" / "preview" / "build"
EXE_NAME = "aspen-insight"

ENTRY = '''# -*- coding: utf-8 -*-
"""PyInstaller 入口:等价于 `python -m app.api`(由 tools/build_exe.py 生成)。"""
import sys

from app.api.server import main

sys.exit(main())
'''


def _pyinstaller_ok() -> bool:
    try:
        out = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"],
                             capture_output=True, text=True, timeout=120)
    except Exception:
        return False
    if out.returncode != 0:
        return False
    print("PyInstaller:", out.stdout.strip())
    return True


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not _pyinstaller_ok():
        print("没有 PyInstaller。先装(只有开发需要,不进 requirements.txt):")
        print(f"    {sys.executable} -m pip install pyinstaller")
        return 1
    if "--check" in argv:
        return 0

    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    entry = BUILD / "entry.py"
    entry.write_text(ENTRY, encoding="utf-8")

    sep = os.pathsep   # Windows 上是 ';'
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--console",
        "--name", EXE_NAME,
        "--icon", str(ROOT / "assets" / "app-icon.ico"),
        "--add-data", f"{ROOT / 'app' / 'web'}{sep}app/web",
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--add-data", f"{ROOT / 'samples' / 'case-23-error.db'}{sep}samples",
        "--paths", str(ROOT),
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        str(entry),
    ]
    print("打包中(第一次要 1–3 分钟)…")
    proc = subprocess.run(args, cwd=str(ROOT))
    if proc.returncode != 0:
        print("打包失败,退出码:", proc.returncode)
        return proc.returncode

    exe = DIST / f"{EXE_NAME}.exe"
    if not exe.is_file():
        print("没找到产物:", exe)
        return 1
    blob = exe.read_bytes()
    print()
    print("产物:", exe)
    print("大小:%.1f MB" % (len(blob) / 1024 / 1024))
    print("SHA-256:", hashlib.sha256(blob).hexdigest().upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
