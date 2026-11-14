"""性能基准:在真实打开的模型上测量各阶段读取耗时。

用法:
    python -m tools.bench            # 当前打开的模型
    python -m tools.bench --repeat 3 # 重复几次取平均

用途:
  * 决定"懒加载粒度"(一次性全读 vs 先读名字再按需读字段)
  * 估算 200–1000 流股时的耗时
"""

from __future__ import annotations

import argparse
import time

from app.aspen import connect, reader


def timed(label: str, fn):
    t0 = time.perf_counter()
    result = fn()
    dt = time.perf_counter() - t0
    print(f"  {label:<30}{dt * 1000:9.0f} ms")
    return result, dt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1, help="完整读取重复次数")
    args = ap.parse_args()

    calls = {"n": 0}

    print("1) 附着")
    session, t_attach = timed("attach()", lambda: connect.attach())
    orig_find = session.find

    def counting_find(path):
        calls["n"] += 1
        return orig_find(path)

    session.find = counting_find

    n_streams = len(reader.child_names(session.find(r"\Data\Streams")))
    n_blocks = len(reader.child_names(session.find(r"\Data\Blocks")))
    print(f"  模型:{session.model_path}")
    print(f"  规模:{n_streams} 条流股 / {n_blocks} 个设备")

    print("\n2) 分阶段耗时")
    calls["n"] = 0
    _, t_conv = timed("read_convergence()", lambda: reader.read_convergence(session))
    c_conv = calls["n"]

    calls["n"] = 0
    streams, t_streams = timed("read_streams()", lambda: reader.read_streams(session))
    c_streams = calls["n"]

    calls["n"] = 0
    blocks, t_blocks = timed("read_blocks()", lambda: reader.read_blocks(session))
    c_blocks = calls["n"]

    calls["n"] = 0
    _, t_comp1 = timed(
        "read_composition(第一条)", lambda: reader.read_composition(session, streams[0]["name"])
    )
    c_comp1 = calls["n"]

    print("\n3) 全量读取(含/不含组成)")
    calls["n"] = 0
    case, t_case = timed("read_case()", lambda: reader.read_case(session))
    c_case = calls["n"]

    calls["n"] = 0
    case_c, t_case_c = timed(
        "read_case(composition=True)",
        lambda: reader.read_case(session, include_composition=True),
    )
    c_case_c = calls["n"]

    # 重复读取取算术平均:以前写 (t_case + t) / 2 是递推加权,
    # 越新的样本权重越大,外推会被最后一次的抖动带偏
    case_times = [t_case]
    for _ in range(max(0, args.repeat - 1)):
        _, t = timed("read_case() 重复", lambda: reader.read_case(session))
        case_times.append(t)
    t_case = sum(case_times) / len(case_times)

    print("\n4) 结论")
    per_call = (t_streams + t_blocks) / max(1, c_streams + c_blocks) * 1000
    print(f"  COM 调用次数: 收敛 {c_conv} / 流股 {c_streams} / 设备 {c_blocks} / 全量 {c_case}")
    print(f"  单次 FindNode 平均:{per_call:.2f} ms")
    print(f"  流股读取:{t_streams * 1000:.0f} ms({c_streams / max(1, n_streams):.1f} 次调用/条)")
    print(f"  设备读取:{t_blocks * 1000:.0f} ms({c_blocks / max(1, n_blocks):.1f} 次调用/个)")
    print(f"  组成读取:{t_comp1 * 1000:.0f} ms/条(18 组分 × 2 相)")
    repeat_note = f"({len(case_times)} 次算术平均)" if len(case_times) > 1 else ""
    print(f"  全量(不含组成):{t_case:.2f} s{repeat_note}")
    print(f"  全量(含组成):  {t_case_c:.2f} s")

    print("\n5) 规模外推(按线性)")
    base = t_case
    for n in (200, 500, 1000):
        factor = n / max(1, n_streams)
        print(
            f"  {n:>4} 流股 ≈ {base * factor:5.1f} s(不含组成) / {t_case_c * factor:5.1f} s(含组成)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
