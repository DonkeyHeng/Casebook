"""断言与公共辅助:用例只管写判定,失败信息在这里统一成一句人话。

为什么要有这一层
----------------
原来的用例全在一个文件里,自己维护 PASSED / FAILED 两个列表。这里保留同样的
"一条一条记"的口径,但**失败立刻抛 AssertionError** —— pytest 认这个,失败会
指向具体那一行,不用自己猜是哪条没过。
"""

from __future__ import annotations

from pathlib import Path

# 仓库根:用例要从这里找 samples/、data/ 这些路径。tools/ 与 tests/ 都直接挂在
# 仓库根下,所以两处算出来的根目录是同一个。
REPO_ROOT = Path(__file__).resolve().parents[1]

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    """断言一条;不通过就抛 AssertionError(消息里带标签与细节)。"""
    if cond:
        PASSED.append(label)
        return
    FAILED.append((label, detail))
    raise AssertionError(f"{label}" + (f" — {detail}" if detail else ""))


def check_eq(label: str, got, want) -> None:
    check(label, got == want, f"期望 {want!r},实际 {got!r}")


def check_close(label: str, got, want: float, tol: float = 1e-6) -> None:
    ok = got is not None and abs(float(got) - want) <= tol
    check(label, ok, f"期望 {want!r} ±{tol},实际 {got!r}")


def _streams(**h) -> list[dict]:
    return [{"name": k, "enthalpy_flow_kw": v} for k, v in h.items()]


def _blocks(*rows) -> list[dict]:
    return [dict(r) for r in rows]


def _conns(*rows) -> list[dict]:
    return [{"block": b, "direction": d, "stream": s, "side": side} for b, d, s, side in rows]


def _mb_streams(*rows) -> list[dict]:
    """(名字, 质量流量 kg/h[, 摩尔流量, 温度, 压力]) → streams 表。缺的不补 0。"""
    out = []
    for row in rows:
        name, mass = row[0], row[1]
        out.append(
            {
                "name": name,
                "mass_flow_kgh": mass,
                "mole_flow_kmolh": row[2] if len(row) > 2 else None,
                "temperature_c": row[3] if len(row) > 3 else None,
                "pressure_bar": row[4] if len(row) > 4 else None,
            }
        )
    return out


def _mb_blocks(*rows) -> list[dict]:
    return [{"name": name, "type": btype} for name, btype in rows]


def _mb_conns(*rows) -> list[dict]:
    """(设备, in/out, 流股[, side]) → connections 表。"""
    out = []
    for row in rows:
        out.append(
            {
                "block": row[0],
                "direction": row[1],
                "stream": row[2],
                "side": row[3] if len(row) > 3 else None,
            }
        )
    return out


def _energy(eid, state, dh=None, ref=None, kind="", note="") -> dict:
    """balance.compute() 结果的形状(报告只认这份,不重算)。"""
    return {
        eid: {
            "dh_kw": dh,
            "ref_kw": ref,
            "ref_kind": kind,
            "balance_state": state,
            "balance_note": note,
        }
    }


# 注意:这是**测试用的合成表**,不是标准值。真实表要等 GB/T 17395 表 1/表 4
# 与 GB/T 20801.1 表 B.1 录入(见 docs/_internal/prompt-vision-extract.md)。
SYNTH_TABLES = {
    "dn_to_od": {15: 22.0, 25: 32.0, 50: 57.0, 100: 108.0, 600: 610.0},
    "od_to_walls": {
        22.0: (2.0, 2.5),
        32.0: (2.5, 3.0),
        57.0: (3.0, 3.5),
        108.0: (4.0, 4.5),
        610.0: (6.0, 8.0),
    },
    "stress": {"S30408": {20: 137.0, 100: 130.0, 200: 118.0, 300: 106.0, 400: 95.0}},
}
