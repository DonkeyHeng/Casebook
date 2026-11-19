"""data/ 目录的重定向:`--data-dir` 靠它让打包版复用仓库里的标准表与工况库。

不测这个的话,回归里看不出"路径在导入时被定死"这类问题 —— 桌面壳踩过一次。
"""

from __future__ import annotations

from pathlib import Path

from app import paths


def test_data_dir_override(tmp_path: Path) -> None:
    default = paths.data_dir("cases.db")
    try:
        paths.set_data_dir(tmp_path)
        assert paths.data_dir("cases.db") == tmp_path.resolve() / "cases.db"
        assert paths.data_dir("preview", "std") == tmp_path.resolve() / "preview" / "std"
    finally:
        paths.set_data_dir(None)
    assert paths.data_dir("cases.db") == default


def test_data_dir_override_none_is_default() -> None:
    paths.set_data_dir(None)
    assert paths.data_dir("window.json").name == "window.json"
    assert paths.data_dir("window.json").parent.name == "data"
