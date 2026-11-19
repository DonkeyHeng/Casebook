"""用例清单:显示名 → 用例函数。

`tools/selftest.py` 按这份清单跑(不装 pytest 也能跑);
pytest 直接按 `tests/test_*.py` 收集,两边的用例是同一批。

显示名沿用迁移前 `tools/selftest.py` 里那套说法,免得日志和文档对不上。
"""

from __future__ import annotations

from tests import (
    test_api,
    test_aspen,
    test_balance,
    test_judge,
    test_pipe,
    test_pump,
    test_pump_line,
    test_report,
    test_store,
    test_tools,
)

CASES: list[tuple[str, object]] = [
    ("判定规则 judge()", test_judge.test_judge),
    ("空模型 mark_empty()", test_judge.test_empty),
    ("焓平衡 balance.compute()", test_balance.test_balance),
    ("物料衡算 mass_balance", test_balance.test_mass_balance),
    ("摩尔守恒 mole_balance", test_balance.test_mole_balance),
    ("能量衡算 energy_report", test_balance.test_energy_report),
    ("物料平衡宽表 stream_table", test_report.test_stream_table),
    ("衡算报告 report_export", test_report.test_report_export),
    ("衡算报告-勾选设备", test_report.test_report_picked),
    ("报错波及范围 impact.build()", test_report.test_impact),
    ("管路选型 design.pipe", test_pipe.test_pipe),
    ("2026 工程校核 §17", test_pipe.test_pipe_2026_rules),
    ("第三轮精细化 §19", test_pipe.test_pipe_third_round),
    ("选型校验 validate() V1–V14", test_pipe.test_pipe_checks),
    ("标准表补录说明 setup_guide()", test_pipe.test_pipe_setup),
    ("标准表导入 import_standards()", test_pipe.test_std_import),
    ("标准表模板与填写提示词", test_pipe.test_std_template),
    ("牌号范围与材质类别", test_pipe.test_material_scope),
    ("管道等级表 piping-class", test_pipe.test_piping_class),
    ("标准表版本化", test_pipe.test_std_versioning),
    ("管路阻力 Re / λ / Σhf", test_pipe.test_hydraulics),
    ("内置示例库 install_sample()", test_tools.test_install_sample),
    ("设备家族 block_family", test_tools.test_block_family),
    ("塔剖面诊断 column_diag", test_tools.test_column_diag),
    ("探针离线冒烟 probe_*", test_tools.test_probe_smoke),
    ("泵口径 design.pump", test_pump.test_pump),
    ("泵 p_v 读取", test_pump.test_pump_pv_read),
    ("设备表泵列 blocks", test_pump.test_block_columns),
    ("设备进出温度", test_pump.test_side_temperatures),
    ("分类导出 excel", test_pump.test_family_export),
    ("选泵提示词 pump_prompt", test_pump.test_pump_prompt),
    ("泵 p_v 存储", test_pump.test_pump_pv_store),
    ("泵 p_v 沿用", test_pump.test_pump_pv_inherit),
    ("单位表 units", test_aspen.test_units),
    ("边界夹具 fixtures", test_aspen.test_fixtures),
    ("组成整条替换 stream_components", test_aspen.test_composition_replace),
    ("组分字典 CAS + 中文名", test_aspen.test_component_dict),
    ("COM 夹具回放 read_case", test_aspen.test_reader_fixture),
    ("COM 夹具回放(真机采集)", test_aspen.test_reader_fixture_real),
    ("出口一致性 exports", test_report.test_export_consistency),
    ("同源校验 guard", test_api.test_guard),
    ("HTTP 入口 body / 静态路径", test_api.test_http_entry),
    ("路由表与分发", test_api.test_routes),
    ("模型身份与选型台账", test_store.test_model_identity_and_selection),
    ("同模型旧版本兜底", test_store.test_model_identity_fallback),
    ("设计参数来源透传", test_store.test_pump_design_source_passthrough),
    ("λ 口径持久化与校验", test_store.test_lambda_method),
    ("泵两侧管段计算与存储", test_pump_line.test_pump_line_formula),
    ("泵管段缺一侧", test_pump_line.test_pump_line_missing_side),
    ("泵两侧流股待指定", test_pump_line.test_pump_line_ambiguous_and_pick),
    ("管段记录乐观锁", test_pump_line.test_pump_line_store),
    ("台账用修正 NPSH", test_pump_line.test_pump_line_selection_corrected),
    ("管道页参数校验", test_pump_line.test_pipe_options_validate),
    ("管段记录 HTTP 往返", test_pump_line.test_line_http_roundtrip),
]


def verify_coverage() -> list[str]:
    """核对清单没漏用例、也没写错名字。

    为什么要这个
    ------------
    `CASES` 是手工清单,而 pytest 是直接扫 `tests/test_*.py` 收用例的 —— 两边各有一份
    "有哪些用例"的事实。新增用例时忘了登记进 `CASES`,`python -m tools.selftest`
    就会**少跑一条**而且一声不响(退出码还是 0)。这里把两份事实对齐,漏了当场报。

    返回问题描述列表(空 = 一致)。
    """
    import inspect

    problems: list[str] = []
    listed = {fn for _name, fn in CASES}
    for module in (
        test_api,
        test_aspen,
        test_balance,
        test_judge,
        test_pipe,
        test_pump,
        test_pump_line,
        test_report,
        test_store,
        test_tools,
    ):
        for name, fn in vars(module).items():
            if not name.startswith("test_") or not inspect.isfunction(fn):
                continue
            if fn not in listed:
                problems.append(f"{module.__name__}.{name} 没登记进 CASES")
    for name, fn in CASES:
        if not callable(fn):
            problems.append(f"CASES 里的 {name} 不可调用")
            continue
        owner = getattr(fn, "__module__", "")
        if owner not in {
            m.__name__
            for m in (
                test_api,
                test_aspen,
                test_balance,
                test_judge,
                test_pipe,
                test_pump,
                test_pump_line,
                test_report,
                test_store,
                test_tools,
            )
        }:
            problems.append(f"CASES 里的 {name} 指向别的模块({owner})")
    return problems
