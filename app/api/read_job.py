"""网页里点「读取当前工况」时,在后台线程跑一次 Aspen 读取。

为什么是"后台线程 + 轮询",而不是一个同步接口
--------------------------------------------
读数要 9–25 s(1000 流股约 76 s)。同步 HTTP 请求会让页面看起来卡死,
浏览器也可能因为等太久而显示转圈/超时。所以拆成两步:

    POST /api/read        立刻返回 {state: "running"}
    GET  /api/read/status 前端每 0.5 s 问一次,直到 state 变成 done / error

为什么线程里还要 CoInitialize
---------------------------
COM 是**按线程**初始化的:服务主线程初始化过,不代表新线程能用,
否则会报 "CoInitialize has not been called"。
`connect.attach()` 内部已经调过 `pythoncom.CoInitialize()`,所以线程里直接调它即可。

一次只允许一个读取任务
--------------------
跨进程 COM 调用本来就慢,两个读取叠在一起只会互相拖,还可能抢许可证。
第二个请求会拿到 409 和当前进度,前端据此显示"已有一次读取在进行中"。

只读
----
整个过程只调 `FindNode` / `.Value`,不调 `Run2` / `Reinit` —— 不会碰用户的模型。
"""
from __future__ import annotations

import threading
import time
import traceback

_lock = threading.Lock()

_IDLE: dict = {
    "state": "idle",        # idle / running / done / error
    "phase": "",            # 现在在干什么(人话)
    "done": 0,              # 当前阶段已处理条数
    "total": 0,             # 当前阶段总条数(0 = 算不出比例)
    "label": "",            # 当前处理的对象名(流股名 / 设备名)
    "error": "",            # state=error 时的完整人话提示
    "case_id": None,        # state=done 时新工况的 id
    "started_at": None,     # 开始时间(时间戳)
    "started_text": "",     # 开始时间(可读)
    "finished_at": None,
    "elapsed_sec": 0.0,
    "model_path": "",
    "stream_count": None,
    "block_count": None,
    "severity": "",
    "converged": None,
}

_state: dict = dict(_IDLE)


# ---------------------------------------------------------------- 状态读写

def snapshot() -> dict:
    """当前任务状态的一份拷贝(前端直接吃这个 JSON)。"""
    with _lock:
        snap = dict(_state)
    started = snap.get("started_at")
    if snap["state"] == "running" and started:
        snap["elapsed_sec"] = round(time.time() - started, 1)
    return snap


def _update(**fields) -> None:
    with _lock:
        _state.update(fields)


def _phase(name: str) -> None:
    # 换阶段时把条数归零,否则进度条会从上一阶段的 100% 突然倒退
    _update(phase=name, done=0, total=0, label="")


def _fail(message: str) -> None:
    _update(state="error", phase="", error=message,
            finished_at=time.time(), label="")


# ---------------------------------------------------------------- 启动

def start(db_path: str) -> tuple[dict, bool]:
    """开始一次读取。

    Returns:
        (状态快照, 是否真的启动了新任务)。已有任务在跑时返回 False。
    """
    with _lock:
        if _state["state"] == "running":
            return dict(_state), False
        _state.clear()
        _state.update(dict(_IDLE))
        _state.update({
            "state": "running",
            "phase": "连接 Aspen",
            "started_at": time.time(),
            "started_text": time.strftime("%H:%M:%S"),
        })

    thread = threading.Thread(target=_run, args=(db_path,),
                              name="aspen-read", daemon=True)
    thread.start()
    return snapshot(), True


# ---------------------------------------------------------------- 工作线程

def _run(db_path: str) -> None:
    # 延迟导入:没装 pywin32 的机器上服务本身还能起来,只在点按钮时给提示
    try:
        from app.aspen import connect, reader
        from app.store import db
    except Exception as exc:  # pragma: no cover - 只有环境坏掉才会进来
        _fail(f"缺少依赖:{type(exc).__name__}: {exc}\n\n"
              "读取 Aspen 需要 pywin32,请先执行:pip install pywin32")
        return

    try:
        try:
            session = connect.attach()
        except connect.AspenNotRunning as exc:
            # 这句提示已经是人话(Aspen 没开 / 权限不一致 / 在沙箱里)
            _fail(str(exc))
            return

        _update(model_path=session.model_path, phase="读取流股")

        def on_progress(done: int, total: int, label: str) -> None:
            _update(done=done, total=total, label=str(label))

        case = reader.read_case(session, progress=on_progress, on_phase=_phase)

        _phase("写入工况库")
        conn = db.open_db(db_path)
        try:
            case_id = db.save_case(conn, case)
        finally:
            conn.close()

        conv = case.get("convergence", {})
        stats = case.get("stats", {})
        _update(
            state="done",
            phase="完成",
            case_id=case_id,
            stream_count=stats.get("stream_count"),
            block_count=stats.get("block_count"),
            severity=conv.get("severity", ""),
            converged=bool(conv.get("converged")),
            finished_at=time.time(),
            elapsed_sec=round(time.time() - (_state.get("started_at") or time.time()), 1),
        )
    except Exception as exc:  # 兜底:任何异常都要变成页面上的提示,不能静默
        traceback.print_exc()
        _fail(f"读取失败:{type(exc).__name__}: {exc}")
