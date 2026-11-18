"""用例清单自身的核对。

`python -m tools.selftest` 按 `tests/_suite.py` 的清单跑,pytest 按文件里的
`test_*` 收用例 —— 两份事实必须一致,否则少跑一条用例没人会发现。
"""

from __future__ import annotations

from tests._harness import check
from tests._suite import CASES, verify_coverage


def test_suite_coverage() -> None:
    """tests/ 里的每条用例都在清单里,清单里也没有指向别处的名字。"""
    problems = verify_coverage()
    check("用例清单:tests/ 里的用例都已登记", not problems, ";".join(problems[:3]))
    check("用例清单:非空", len(CASES) > 0, f"只有 {len(CASES)} 条")
