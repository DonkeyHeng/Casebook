"""按需读取一台换热器四端流股的物性(cp / λ / μ)并缓存到 `hx_props`。

为什么按需,不跟主读取一起读
---------------------------
3 个物性 × 4 条侧流股 = 12 次 COM 调用(还要乘上 `_lower_leaf` 的下钻),而且
**大多数模型根本没挂物性集**(实测本机 V15 的 `STRM_UPP` 只有 PL / MUMX / PBUB),
跟着主读取一起读只会让每次读工况慢一截、结果还全空。所以做成"打开哪台的抽屉
就读哪台",读完写进 `hx_props`(`case_id + stream`),第二次打开 0 次调用。

三条安全约束(与 `app/api/composition.py` 同一套)
----------------------------------------------
1. **只读**:只 FindNode / Value,不 Run / Reinit / Save;
2. **必须确认当前打开的 Aspen 就是这个工况的来源模型**;
3. **必须确认还是同一次运行**(比对 Run-Status 的 `RUNID`)。

与组成读取的差别:组成是用户点流股才读,读不到就报错让人处理;物性是抽屉的
附带信息,**读不到不能把抽屉打挂** —— 一律降级成 `note` + 空值,由 L2 自己去标
"缺物性",L1 结论不受影响。
"""

from __future__ import annotations

import threading

from app.design.heat import params
from app.store import db

# 一条一条来:跨进程 COM 调用叠加没有好处,还会同时占两个许可证请求
_lock = threading.Lock()


class PropsUnavailable(RuntimeError):
    """当前读不到(Aspen 没开、模型不是这个工况、或重跑过)。**不是错误**,是降级理由。"""


# ---------------------------------------------------------------- 公开入口


def get(conn, case, block: str, force: bool = False) -> dict:
    """取一台设备的四端物性:先看缓存,不全就去 Aspen 现读并回写缓存。

    永远返回结构完整的 dict;读不到时 `source="unavailable"`,给 `note`。
    """
    case_id = case["id"]
    blocks = [dict(b) for b in db.read_blocks(conn, case_id)]
    conns = [dict(c) for c in db.read_connections(conn, case_id)]
    streams = [dict(s) for s in db.read_streams(conn, case_id)]
    block_rec = next((b for b in blocks if b.get("name") == block), None)
    if block_rec is None:
        raise LookupError(f"工况 {case_id} 里没有设备 {block}")

    sides = params.build_sides(block_rec, conns, streams)
    wanted = _side_streams(sides)
    cached = {r["stream"]: dict(r) for r in db.read_hx_props(conn, case_id)}

    usable = all(name in cached and _has_value(cached[name]) for name in wanted)
    if usable and not force:
        return _payload(block_rec, sides, cached, "case", note=None)

    with _lock:
        cached = {r["stream"]: dict(r) for r in db.read_hx_props(conn, case_id)}
        usable = all(name in cached and _has_value(cached[name]) for name in wanted)
        if usable and not force:
            return _payload(block_rec, sides, cached, "case", note=None)

        try:
            live = _read_live(case, block, blocks, conns, streams)
        except PropsUnavailable as exc:
            return _payload(block_rec, sides, cached, "unavailable", note=str(exc))

        # 只缓存 Aspen 真读到的三项。**不把反推的 cp 写库**:反推值是"这一侧 + 这个
        # 负荷"的派生量,换工况就变;写进缓存会被下一次读当成实测值(违反 §7)。
        # 缺 cp 的反推统一在 `_payload()` 里按需算,并标 derived。
        for name, row in live.items():
            values = {
                "mu_cp": row.get("mu_cp"),
                "cp_jkgk": row.get("cp_jkgk"),
                "k_wmk": row.get("k_wmk"),
                "source": row.get("source") or "missing",
            }
            if any(values[k] is not None for k in ("mu_cp", "cp_jkgk", "k_wmk")):
                db.save_hx_props(conn, case_id, name, values)

        merged = {r["stream"]: dict(r) for r in db.read_hx_props(conn, case_id)}
        return _payload(block_rec, sides, merged, "live", note=None)


# ---------------------------------------------------------------- 实际读


def _read_live(case, block, blocks, conns, streams) -> dict[str, dict]:
    try:
        from app.aspen import connect, reader
    except Exception as exc:  # pragma: no cover - 只有环境坏掉才会进来
        raise PropsUnavailable(
            f"缺少依赖:{type(exc).__name__}: {exc};读取物性需要 pywin32"
        ) from exc

    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass

    session = _attach_matching(connect, case)
    _check_same_run(case, session)
    try:
        rows = reader.read_hx_props(session, streams, blocks=blocks, connections=conns, block=block)
    except Exception as exc:
        raise PropsUnavailable(f"读取 {block} 侧流股物性失败:{type(exc).__name__}: {exc}") from exc
    return rows


def _attach_matching(connect, case):
    try:
        models = connect.list_open_models()
    except connect.AspenNotRunning as exc:
        raise PropsUnavailable(str(exc)) from exc
    if not models:
        raise PropsUnavailable(
            "没有打开着的 Aspen 模型,读不到物性。\n\n"
            "物性是打开抽屉时现读的(全量读会把每次读工况拖慢数倍)。\n"
            "需要 cp / λ / μ 时,请先把模型打开并 Run 过;只想看 L1 可以忽略这条。"
        )
    want = str(case["model_path"] or "")
    norm = lambda p: str(p).replace("/", "\\").strip().lower()  # noqa: E731
    match = [m for m in models if norm(m) == norm(want)]
    if not match:
        opened = "\n".join(f"  - {m}" for m in models)
        raise PropsUnavailable(
            "现在打开的模型不是这个工况的来源模型,不能把它的物性记到这个工况上。\n\n"
            f"  工况来源:{want}\n  当前打开:\n{opened}\n\n"
            "L1 结论不依赖物性;要看 L2 请先打开对应模型。"
        )
    try:
        return connect.attach(match[0])
    except connect.AspenNotRunning as exc:
        raise PropsUnavailable(str(exc)) from exc


def _check_same_run(case, session) -> None:
    want = str(case["run_id"] or "").strip()
    if not want:
        return
    live = str(session.value(r"\Data\Results Summary\Run-Status\Output\RUNID") or "").strip()
    if not live or live == want:
        return
    raise PropsUnavailable(
        "当前打开的模型已经重新运行过了,物性和这个工况不是同一次运行的结果。\n\n"
        f"  这个工况({case['read_at']})的运行 ID:{want}\n"
        f"  当前模型里的运行 ID:{live}\n\n"
        "重新点一次「读取当前工况」拿一份新快照,物性才会跟着更新。"
    )


# ---------------------------------------------------------------- 组装


def _side_streams(sides: dict) -> list[str]:
    names: list[str] = []
    for side in sides.values():
        for name in [side.get("stream"), *(side.get("inlets") or [])]:
            if name and name not in names:
                names.append(name)
    return names


def _has_value(row: dict | None) -> bool:
    if not row:
        return False
    return any(row.get(k) is not None for k in ("mu_cp", "cp_jkgk", "k_wmk"))


def _payload(block_rec: dict, sides: dict, cached: dict, source: str, note: str | None) -> dict:
    duty = block_rec.get("duty_kw")
    side_out: dict[str, dict] = {}
    missing: list[str] = []
    for key, side in sides.items():
        name = side.get("stream")
        row = cached.get(name) if name else None
        info = params.side_props(side, row, duty)
        side_out[key] = {
            "stream": name,
            "mu_cp": info["mu_cp"],
            "cp_jkgk": info["cp_jkgk"],
            "k_wmk": info["k_wmk"],
            "source": info["source"],
            "missing": info["missing"],
            "notes": info["notes"],
        }
        # 只有挂着真实流股的一侧才并入顶层缺失:Heater 的公用工程侧本来就没有
        # 流股,它的 missing 不能算"缺物性"。
        if not name:
            continue
        for item in info["missing"]:
            if item not in missing:
                missing.append(item)

    streams_out = {}
    for name, row in cached.items():
        streams_out[name] = {
            "mu_cp": row.get("mu_cp"),
            "cp_jkgk": row.get("cp_jkgk"),
            "k_wmk": row.get("k_wmk"),
            "source": row.get("source"),
            "updated_at": row.get("updated_at"),
        }

    return {
        "block": block_rec.get("name"),
        "source": source,  # case = 库里缓存;live = 刚从 Aspen 读并已写库;unavailable = 读不到
        "note": note,
        "sides": side_out,
        "streams": streams_out,
        "missing": missing,
        "complete": not missing,
    }
