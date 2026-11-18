"""工具脚本:塔剖面诊断、示例库安装、设备家族。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

import shutil

from app.store import db
from tests._harness import (
    REPO_ROOT,
    check,
    check_eq,
)


def test_probe_smoke() -> None:
    """探针脚本:离线也能导入与看帮助(不连 Aspen、不需要 pywin32)。

    探针是双击 .bat / ``python -m`` 跑的一次性诊断工具。pywin32 缺失只该在
    **真探测**时才报错,而不是导入期炸掉 —— CI 与纯计算环境没有 pythoncom,
    导入期炸会让"改了探针要冒烟验证"变成一句空话。pywin32 在时 probe_common
    也不连 Aspen(attach 只在 main 里调),所以这里全程离线。
    """
    import importlib
    import subprocess
    import sys

    modules = [
        "probe_aspen",
        "probe_common",
        "probe_blocks",
        "probe_components",
        "probe_pump",
        "probe_pv",
        "probe_topology",
        "probe_viscosity",
    ]
    for name in modules:
        check(f"探针 {name} 可导入(离线)", importlib.import_module(f"tools.{name}") is not None)

    scripts = [
        "probe_blocks",
        "probe_components",
        "probe_pump",
        "probe_pv",
        "probe_topology",
        "probe_viscosity",
    ]
    for name in scripts:
        run = subprocess.run(
            [sys.executable, "-m", f"tools.{name}", "--help"],
            check=False,  # --help 的退出码本身就是要检查的断言,不能让 run 先抛
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
        )
        check_eq(f"探针 {name} --help 退出码", run.returncode, 0)
        check(f"探针 {name} --help 有 usage", "usage" in run.stdout, run.stdout[:120])


def test_column_diag() -> None:
    """塔剖面诊断:低温越界时,标题里的级号与温度都要指向低温那一侧。"""
    from tools import column_diag

    profile = {"vars": {"B_TEMP": [80.0, 60.0, -120.0, 90.0], "LIQ_FLOW": [], "VAP_FLOW": []}}
    titles = [i["title"] for i in column_diag.check(profile, {"feed_info": []})]
    hit = [t for t in titles if "超出物理可能范围" in t]
    check("剖面:低温越界会报出来", bool(hit), str(titles))
    check(
        "剖面:标题指向最低温那一级(第 3 级 -120℃)", any("第 3 级 -120" in t for t in hit), str(hit)
    )


def test_install_sample() -> None:
    """内置示例库:能装进空库;覆盖前把原库备份成带时间戳的 .bak。"""
    from app.api import server

    root = REPO_ROOT
    tmp = root / "data" / "preview" / "_selftest-sample"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        db_path = tmp / "cases.db"
        ok, msg = server._install_sample(str(db_path), alert=False)
        check_eq("示例:装库成功", ok, True)
        check("示例:提示里有流股数", "121" in msg)

        conn = db.open_db(str(db_path))
        try:
            cases = db.list_cases(conn)
        finally:
            conn.close()
        check_eq("示例:工况条数", len(cases), 1)
        check_eq("示例:工况 id", cases[0]["id"], 23)
        check_eq("示例:流股数", cases[0]["stream_count"], 121)

        ok2, msg2 = server._install_sample(str(db_path), alert=False)
        check_eq("示例:重复装载也成功", ok2, True)
        backups = sorted(tmp.glob("cases.db.*.bak"))
        check(
            "示例:覆盖前已备份(带时间戳)",
            len(backups) == 1,
            f"实际备份 {[b.name for b in backups]}",
        )
        if backups:
            # 备份不是"顺手抄的文件",得是能打开的完整库
            conn = db.open_db(str(backups[0]))
            try:
                check_eq("示例:备份是可读的完整库", len(db.list_cases(conn)), 1)
            finally:
                conn.close()
        check("示例:没留下临时文件", not list(tmp.glob("*.installing*")))
        check("示例:提示里写了备份", "备份" in msg2)
        conn = db.open_db(str(db_path))
        try:
            check_eq("示例:覆盖后仍是同一条工况", len(db.list_cases(conn)), 1)
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_block_family() -> None:
    """block_family:把几十种 Aspen 类型收敛成 7 个页签,未知类型不能丢。"""
    from app.design import block_family as fam

    check_eq("家族:RadFrac → 塔", fam.family_of("RadFrac"), "column")
    check_eq("家族:Pump → 泵(独立一族)", fam.family_of("Pump"), "pump")
    check_eq("家族:Turbine → 涡轮机", fam.family_of("Turbine"), "turbine")
    check_eq("家族:Pump + 位号 K → 涡轮机", fam.family_of("Pump", "K0101"), "turbine")
    check_eq("家族:Pump + 位号 P → 泵", fam.family_of("Pump", "P0101"), "pump")
    check_eq("家族:Pump 不给位号时按泵算", fam.family_of("Pump"), "pump")
    check_eq("家族:k 开头小写也认", fam.family_of("pump", "k0201"), "turbine")
    check_eq("家族:未知类型 → 其他", fam.family_of("Welding"), "other")
    check_eq("家族:空类型 → 其他", fam.family_of(None), "other")
    check_eq("家族:大小写不敏感", fam.family_of("radfrac"), "column")

    blocks = [{"type": "Pump"}, {"type": "Pump"}, {"type": "RadFrac"}]
    check_eq("默认家族:有塔落塔", fam.pick_default(blocks), "column")
    check_eq(
        "家族计数:透平不算泵",
        fam.counts([{"type": "Pump", "name": "P0101"}, {"type": "Pump", "name": "K0101"}]),
        {"pump": 1, "turbine": 1},
    )
    check_eq(
        "默认家族:无塔落第一个非空",
        fam.pick_default([{"type": "Pump"}, {"type": "Heater"}]),
        "heat",
    )
    check_eq("默认家族:空清单回落塔", fam.pick_default([]), "column")
    check_eq("家族计数", fam.counts(blocks), {"pump": 2, "column": 1})
    check_eq(
        "家族表顺序",
        [f["key"] for f in fam.describe()],
        ["column", "heat", "flash", "react", "pump", "turbine", "move", "mix", "other"],
    )
