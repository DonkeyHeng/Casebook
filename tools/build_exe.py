"""tools/build_exe.py —— 打 Windows 单文件 exe(PyInstaller)。

两种产物:

* 默认:浏览器版 `data/preview/dist/Casebook.exe`(双击起服务 → 用 Edge 的 --app 窗口);
* `--desktop`:桌面壳版 `data/preview/dist/Casebook-Desktop.exe`(内置 WebView2 窗口,
  不依赖本机浏览器,入口 `app/desktop.py`)。

```bash
python -m tools.build_exe            # 浏览器版
python -m tools.build_exe --desktop  # 桌面壳版
python -m tools.build_exe --check    # 只检查 PyInstaller 装没装
```

两个说明:

* PyInstaller 需要一个普通脚本当入口,而项目的入口是 `python -m app.api`
  (`app/api/__main__.py`),所以这里临时生成一个入口脚本,放在
  `data/preview/build/` 下(已被 `.gitignore` 覆盖),不打进仓库。
* 静态文件(`app/web`)、图标(`assets`)、示例工况库(`samples/`)都塞进包里;
  运行时 `app/paths.py` 会把只读资源指到解包目录,把 `data/` 指到 exe 旁边。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "data" / "preview" / "dist"
BUILD = ROOT / "data" / "preview" / "build"
EXE_NAME = "Casebook"

ENTRY = '''# -*- coding: utf-8 -*-
"""PyInstaller 入口:等价于 `python -m app.api`(由 tools/build_exe.py 生成)。"""
import sys

from app.api.server import main

sys.exit(main())
'''

ENTRY_DESKTOP = '''# -*- coding: utf-8 -*-
"""PyInstaller 入口:等价于 `python -m app.desktop`(由 tools/build_exe.py 生成)。"""
import sys

from app.desktop import main

sys.exit(main())
'''


def _pyinstaller_ok() -> bool:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception:
        return False
    if out.returncode != 0:
        return False
    print("PyInstaller:", out.stdout.strip())
    return True


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    desktop = "--desktop" in argv
    # 桌面壳默认不带控制台;打包出错排查时用 --console 打一个看得见报错的版本
    console = "--console" in argv
    # 排查打包问题时用 --onedir:产物是一整个目录(不压缩、退出不清临时目录),能逐个比对文件
    onedir = "--onedir" in argv
    if not _pyinstaller_ok():
        print("没有 PyInstaller。先装(只有开发需要,不进 requirements.txt):")
        print(f"    {sys.executable} -m pip install pyinstaller")
        return 1
    if desktop:
        try:
            import webview  # noqa: F401
        except Exception:
            print("桌面壳版还需要 pywebview(打进包里的窗口库):")
            print(f"    {sys.executable} -m pip install pywebview")
            return 1
    if "--check" in argv:
        return 0

    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    entry = BUILD / ("entry_desktop.py" if desktop else "entry.py")
    entry.write_text(ENTRY_DESKTOP if desktop else ENTRY, encoding="utf-8")
    name = f"{EXE_NAME}-Desktop" if desktop else EXE_NAME
    if desktop and console:
        name += "-console"

    sep = os.pathsep  # Windows 上是 ';'
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir" if onedir else "--onefile",
        # 浏览器版留控制台:起不来时错误要看得见。桌面壳版是窗口程序,日志落 desktop.log
        "--console" if (console or not desktop) else "--windowed",
        "--name",
        name,
        "--icon",
        str(ROOT / "assets" / "app-icon.ico"),
        "--add-data",
        f"{ROOT / 'app' / 'web'}{sep}app/web",
        "--add-data",
        f"{ROOT / 'assets'}{sep}assets",
        "--add-data",
        f"{ROOT / 'samples' / 'case-23-error.db'}{sep}samples",
        # 标准表格式模板(只有表头与虚构示例值):「下载 CSV 模板」与填写提示词要读它
        "--add-data",
        f"{ROOT / 'samples' / 'pipe-standards-template'}{sep}samples/pipe-standards-template",
        "--paths",
        str(ROOT),
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        str(entry),
    ]
    if desktop:
        # pywebview 的窗口后端与它的静态资源(js / WebView2 加载器)不是普通 import,
        # PyInstaller 的分析看不见,必须整包收进来
        args[args.index("--name") : args.index("--name")] = [
            "--collect-all",
            "webview",
            "--collect-all",
            "pythonnet",
            # clr_loader 是 pythonnet 的引导层:少了它,冻结后报
            # "Failed to resolve Python.Runtime.Loader.Initialize"
            "--collect-all",
            "clr_loader",
            "--copy-metadata",
            "pythonnet",
            "--copy-metadata",
            "clr-loader",
            "--hidden-import",
            "clr",
        ]
    print(f"打包中({name},第一次要 1–3 分钟)…")
    proc = subprocess.run(args, cwd=str(ROOT), check=False)
    if proc.returncode != 0:
        print("打包失败,退出码:", proc.returncode)
        return proc.returncode

    exe = (DIST / name / f"{name}.exe") if onedir else (DIST / f"{name}.exe")
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
