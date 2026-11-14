"""按需读取一条流股的组成(液相 X / 气相 Y 摩尔分数)。

为什么按需,不跟着主读取一起读
---------------------------
18 组分 × 2 相 × 每条流股 = 36 次 COM 调用,是 6 个基础字段的 6 倍:
121 条流股从 9 s 涨到 41 s,1000 条要 340 s。而实际看的时候通常只关心几条流股,
所以做成"点开哪条读哪条"(实测单条 264 ms),读完就写进工况库,
第二次打开 0 ms,导出 AI 分析包时也能带上。

三条安全约束
-----------
1. **只读**:只调 `FindNode` / `.Value`,不碰模型;
2. **必须确认当前打开的 Aspen 就是这个工况的来源模型** —— 否则会把别的模型的
   组成写进这个工况,那是最难发现的一类错数据;
3. **必须确认还是同一次运行**(比对 Run-Status 的 `RUNID`)—— 工况快照是过去某个时刻的,
   模型如果之后重跑过,现在的组成和表里的温度/流量就不是同一次运行的结果,混在一起会算错。
   这种情况下宁可不给(项目一贯的"宁缺勿错"),并告诉用户该怎么办。
"""

from __future__ import annotations

import threading

from app.store import db

# 一条一条来:跨进程 COM 调用叠加没有好处,而且会同时占两个许可证请求
_lock = threading.Lock()


class CompositionError(RuntimeError):
    """读不到组成时的人话原因(直接显示在页面上)。"""


# ---------------------------------------------------------------- 公开入口


def get(conn, case, stream: str, force: bool = False) -> dict:
    """取一条流股的组成:先看工况库里有没有,没有就去 Aspen 现读并缓存。

    Args:
        conn: 数据库连接(调用方负责关闭)。
        case: 工况行(要 model_path / run_id / id)。
        stream: 流股名。
        force: 忽略缓存重新读一次。

    Raises:
        CompositionError: 读不到,或当前 Aspen 状态和这个工况对不上。
    """
    if not force:
        cached = db.read_stream_composition(conn, case["id"], stream)
        if cached:
            return _payload(
                stream,
                "case",
                [
                    {"component": r["component"], "x_liq": r["x_liq"], "y_vap": r["y_vap"]}
                    for r in cached
                ],
                run_id=case["run_id"],
            )

    with _lock:
        # 双重检查:等锁的这段时间里,别的请求可能已经读好并写库了
        if not force:
            cached = db.read_stream_composition(conn, case["id"], stream)
            if cached:
                return _payload(
                    stream,
                    "case",
                    [
                        {"component": r["component"], "x_liq": r["x_liq"], "y_vap": r["y_vap"]}
                        for r in cached
                    ],
                    run_id=case["run_id"],
                )

        rows, run_id = _read_live(case, stream)
        db.save_stream_composition(conn, case["id"], stream, rows)
        return _payload(stream, "live", rows, run_id=run_id)


# ---------------------------------------------------------------- 实际读


def _read_live(case, stream: str) -> tuple[list[dict], str]:
    try:
        from app.aspen import connect, reader
    except Exception as exc:  # pragma: no cover - 只有环境坏掉才会进来
        raise CompositionError(
            f"缺少依赖:{type(exc).__name__}: {exc}\n\n"
            "读取组成需要 pywin32,请先执行:pip install pywin32"
        ) from exc

    # 每个 HTTP 请求都在**新线程**里跑,而 COM 是按线程初始化的。
    # 不先初始化就去枚举 ROT,可能拿不到任何对象 —— 表现为"Aspen 明明开着却说没开"。
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass

    session = _attach_matching(connect, case)
    _check_same_run(connect, session, case)

    try:
        rows = reader.read_composition(session, stream)
    except Exception as exc:
        raise CompositionError(f"读取流股 {stream} 的组成失败:{type(exc).__name__}: {exc}") from exc

    if not rows:
        raise CompositionError(
            f"流股 {stream} 没有读到任何组分。\n\n"
            "可能原因:该流股的 X / Y 节点为空(纯能量流?),或者模型里没定义组分。"
        )

    run_id = str(session.value(r"\Data\Results Summary\Run-Status\Output\RUNID") or "")
    return rows, run_id


def _attach_matching(connect, case):
    """附着到**这个工况的来源模型**,不是"随便哪个打开着的模型"。"""
    try:
        models = connect.list_open_models()
    except connect.AspenNotRunning as exc:
        raise CompositionError(str(exc)) from exc

    if not models:
        raise CompositionError(
            "没有打开着的 Aspen 模型,读不到组成。\n\n"
            "组成是现从 Aspen 读的(不跟着主读取一起存,因为全量读会慢 4 倍)。\n"
            "要看组成,请先把模型打开:Aspen → File → Open → 这个工况对应的 .apw。"
        )

    want = str(case["model_path"] or "")
    norm = lambda p: str(p).replace("/", "\\").strip().lower()  # noqa: E731
    match = [m for m in models if norm(m) == norm(want)]
    if not match:
        opened = "\n".join(f"  - {m}" for m in models)
        raise CompositionError(
            "现在打开的模型不是这个工况的来源模型,不能把它的组成记到这个工况上。\n\n"
            f"  工况来源:{want}\n  当前打开:\n{opened}\n\n"
            "请先打开对应的 .apw 再点流股;或者先用当前模型点一次「读取当前工况」。"
        )

    try:
        return connect.attach(match[0])
    except connect.AspenNotRunning as exc:
        raise CompositionError(str(exc)) from exc


def _check_same_run(connect, session, case) -> None:
    """工况快照是过去某一刻的;模型若之后重跑过,组成就不是同一次运行的结果。"""
    want = str(case["run_id"] or "").strip()
    if not want:
        return  # 老工况没记运行 ID,不做判断(不阻止,但也不假装一致)
    live = session.value(r"\Data\Results Summary\Run-Status\Output\RUNID")
    live = str(live or "").strip()
    if not live or live == want:
        return
    raise CompositionError(
        "当前打开的模型已经重新运行过了,组成和这个工况不是同一次运行的结果。\n\n"
        f"  这个工况({case['read_at']})的运行 ID:{want}\n"
        f"  当前模型里的运行 ID:{live}\n\n"
        "混在一起会算出错的物料平衡,所以这里不读。\n"
        "→ 重新点一次「读取当前工况」拿一份新快照,再点流股看组成。"
    )


# ---------------------------------------------------------------- 组装返回


def _payload(stream: str, source: str, rows: list[dict], run_id: str = "") -> dict:
    def total(key: str):
        vals = [r.get(key) for r in rows]
        nums = [v for v in vals if isinstance(v, (int, float))]
        return round(sum(nums), 6) if nums else None

    return {
        "stream": stream,
        "source": source,  # case = 工况库里存的;live = 刚从 Aspen 读并已存库
        "run_id": run_id or "",
        "components": rows,
        "count": len(rows),
        "sum_x": total("x_liq"),
        "sum_y": total("y_vap"),
    }
