"""回归自测的入口 —— 不需要 Aspen、不需要网络、不需要 pytest。

    python -m tools.selftest                  # 只跑纯函数用例(秒级)
    python -m tools.selftest --db data/cases.db   # 再加"库里每个工况"的一致性检查
    python -m tools.selftest -v               # 逐条打印通过的用例

用例本体在 `tests/`(已迁到 pytest,`python -m pytest tests/ -q` 跑同一批);
这个文件只是薄入口,保留是因为:

* 不装 pytest 的机器也能跑;
* 输出还是"一行一条用例"的流水,适合贴进交接记录;
* `--db` 顺手检查一个具体工况库的结论是否与当前规则一致。

为什么要有这套用例
------------------
`judge()` 的判定规则和 `balance.py` 的容差口径**没有别的测试网**。实测中被改错过三次:
  ① 用 `UOSSTAT=1` 判 error —— 把"7 个告警、0 个报错"的样本模型判成"不建议用来算收率";
  ② 用 `PER_ERROR=1` 判 warning —— 一条理由都写不出来却判了黄;
  ③ 用撕裂流 `ERR > ERR_TOL` 判 error —— Aspen 控制面板根本没报这个错。
三次都是"看起来更严格",实际是误报。改动这两处的人(包括 AI)必须能立刻看到行为变了。

三组用例
--------
1. `judge()`:状态码 + 模块 BLKSTAT + 撕裂流 → ok / warning / error 的边界
2. `balance.compute()`:热负荷 / 换热器冷侧 / 机械功比值 / 绝热 / 不校验 / 无法校验
3. `--db`:库里每个工况"存下来的结论"必须等于"现在规则重算的结论"
   —— 这条不过,说明规则改了但没跑 `python -m tools.repair_cases --apply`
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判定 / 焓平衡 / 波及范围的回归自测")
    parser.add_argument("--db", default="", help="额外检查这个工况库(如 data/cases.db)")
    parser.add_argument("-v", "--verbose", action="store_true", help="逐条打印通过的用例")
    args = parser.parse_args(argv)

    from tests._harness import FAILED, PASSED, SKIPPED
    from tests._suite import CASES, verify_coverage
    from tests.test_store import check_db

    # 清单与 tests/ 对不上就先停:否则会少跑一条用例还一声不响
    problems = verify_coverage()
    if problems:
        print("用例清单与 tests/ 对不上:")
        for item in problems:
            print(f"  - {item}")
        return 1

    print("Casebook 自测")
    for name, fn in CASES:
        print(f"\n[{name}]")
        try:
            fn()
        except AssertionError as exc:
            print(f"  ✗ {exc}")
        except Exception as exc:  # noqa: BLE001 —— 用例抛非断言异常(fixture 缺失等)
            # 也要记成一条不通过并继续跑下一组,否则一条崩掉整趟,后面几十组全不跑。
            FAILED.append((f"{name} 异常中断", f"{type(exc).__name__}: {exc}"))
            print(f"  ✗ {name} 异常中断:{type(exc).__name__}: {exc}")

    if args.db:
        print(f"\n[库内一致性 {args.db}]")
        try:
            check_db(args.db)
        except FileNotFoundError:
            print("  找不到该库,跳过")

    if args.verbose:
        for label in PASSED:
            print(f"  ok   {label}")

    print()
    total = len(PASSED) + len(FAILED)
    if FAILED:
        print(f"✗ {len(FAILED)} 条不通过(共 {total} 条通过/不通过,另有跳过 {len(SKIPPED)} 条):")
        for label, detail in FAILED:
            print(f"  - {label}")
            if detail:
                print(f"      {detail}")
        if SKIPPED:
            print("\n跳过(本机缺标准表,不算通过也不算失败):")
            for item in SKIPPED:
                print(f"  - {item}")
        return 1
    if SKIPPED:
        print(f"✓ 全部通过:{len(PASSED)} 条,跳过 {len(SKIPPED)} 条:")
        for item in SKIPPED:
            print(f"  - {item}")
        return 0
    print(f"✓ 全部通过:{len(PASSED)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
