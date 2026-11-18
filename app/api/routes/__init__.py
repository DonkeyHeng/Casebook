"""路由表与按域拆开的 Handler。

两张表:

* `ROUTES` —— 处理函数只要 `(self, m, query)`,自己开库(或根本不用库)。
  所有 POST / DELETE 都在这里;
* `CASE_ROUTES` —— 需要"先解析工况 id、开库、确认工况存在"的 GET。
  分发器 `Handler._api_cases()` 统一做好这三件事再调用,handler 拿到的
  `case` 就是已经查出来的那一行,不用再查一次。

**加接口 = 在表里加一行 + 在对应域里加一个方法**,不用再去三个 do_* 里找分支。
表里写的处理函数名在 `Handler` 上找不到时,`selftest` 的 `test_routes()` 会报错。
"""

from __future__ import annotations

from app.api.router import Route, route

from .cases import CaseRoutes
from .exports import ExportRoutes
from .meta import MetaRoutes
from .pipes import PipeRoutes
from .pumps import PumpRoutes

__all__ = [
    "CASE_ROUTES",
    "ROUTES",
    "CaseRoutes",
    "ExportRoutes",
    "MetaRoutes",
    "PipeRoutes",
    "PumpRoutes",
]

_CASE = r"/api/cases/(?P<case_id>\d+)"
_PUMP = _CASE + r"/pumps/(?P<block>[^/]+)"

ROUTES: list[Route] = [
    # ---- 元信息:不依赖工况 ----
    route("GET", r"/api/health", "get_health", "健康检查"),
    route("GET", r"/api/version", "get_version", "本地版本号与仓库地址"),
    route("GET", r"/api/read/status", "get_read_status", "读取进度(前端每 0.5 s 轮询)"),
    route("POST", r"/api/read", "post_read", "从已打开的 Aspen 读一次(后台线程)"),
    route("POST", r"/api/shutdown", "post_shutdown", "停服务"),
    route("POST", r"/api/update/check", "post_update_check", "手动检查更新(唯一出站请求)"),
    route("POST", r"/api/sample/install", "post_sample_install", "载入内置示例工况"),
    route("GET", r"/api/compare", "get_compare", "两个工况并排对比"),
    route("GET", r"/api/block-families", "get_block_families", "设备类型 → 家族"),
    # ---- 标准表与管道等级表:不依赖工况 ----
    route("GET", r"/api/pipe-tables", "get_pipe_tables", "三张标准录到什么程度"),
    route("GET", r"/api/pipe-standards", "get_pipe_standards", "标准表导入弹窗的状态"),
    route("POST", r"/api/pipe-standards", "post_pipe_standards", "导入三个 CSV 并重新加载"),
    route(
        "GET",
        r"/api/pipe-standards-prompt",
        "get_pipe_standards_prompt",
        "「复制填写提示词」的正文",
    ),
    route(
        "GET",
        r"/api/pipe-standards-template\.zip",
        "get_pipe_standards_template",
        "下载模板 CSV",
    ),
    route("GET", r"/api/piping-classes", "get_piping_classes", "管道等级表内容"),
    route("POST", r"/api/piping-classes", "post_piping_classes", "保存等级表"),
    route("DELETE", r"/api/piping-classes", "delete_piping_classes", "清空等级表"),
    # ---- 泵口径常量:不依赖工况 ----
    route("GET", r"/api/pump-spec", "get_pump_spec", "泵口径常量"),
    route("GET", r"/api/pump-prompt", "get_pump_prompt", "「复制选泵提示词」的正文"),
    # ---- 工况:列表与删除(不需要预先开库) ----
    route("GET", r"/api/cases", "get_cases", "工况列表"),
    route("DELETE", _CASE, "delete_case", "删掉一个工况及其明细"),
    # ---- 按工况写入:各处理函数自己开库 ----
    route("POST", _PUMP + r"/pv", "post_pump_pv", "保存饱和蒸气压"),
    route("POST", _PUMP + r"/design-head", "post_pump_design", "保存设计扬程参数"),
    route("POST", _PUMP + r"/selection", "post_pump_selection", "保存选型台账"),
    route("POST", _PUMP + r"/head-loss", "post_pump_head_loss", "保存管路阻力"),
    route(
        "POST",
        _CASE + r"/pumps/inherit-pv",
        "post_pump_inherit_pv",
        "从同模型更早工况沿用 p_v",
    ),
    route("POST", _CASE + r"/sections", "post_case_sections", "保存工段边界"),
    route(
        "POST",
        _CASE + r"/sections/inherit",
        "post_case_sections_inherit",
        "沿用同模型更早工况的工段边界",
    ),
    route("POST", _CASE + r"/components/cas", "post_case_component_cas", "手填组分 CAS"),
]

CASE_ROUTES: list[Route] = [
    route("GET", _CASE, "get_case", "单个工况详情"),
    route("GET", _CASE + r"/streams", "get_case_streams", "流股表"),
    route(
        "GET",
        _CASE + r"/streams/(?P<stream>[^/]+)/composition",
        "get_case_composition",
        "单条流股的组成(按需从 Aspen 读)",
    ),
    route("GET", _CASE + r"/blocks", "get_case_blocks", "设备表"),
    route("GET", _CASE + r"/connections", "get_case_connections", "设备 ↔ 流股连接关系"),
    route("GET", _CASE + r"/flowsheet", "get_case_flowsheet", "流程图数据"),
    route("GET", _CASE + r"/impact", "get_case_impact", "报错设备的波及范围"),
    route("GET", _CASE + r"/pipes", "get_pipes", "管路选型一览表"),
    route("GET", _CASE + r"/columns", "get_case_columns", "塔列表"),
    route(
        "GET",
        _CASE + r"/columns/(?P<block>[^/]+)",
        "get_case_column_stages",
        "塔逐板剖面",
    ),
    route("GET", _CASE + r"/sections", "get_case_sections", "工段边界"),
    route("GET", _PUMP + r"/hydraulics", "get_pump_hydraulics", "管路阻力 Re / λ / Σhf"),
    route("GET", _PUMP + r"/design-head", "get_pump_design", "设计扬程辅助"),
    route("GET", _PUMP + r"/selection", "get_pump_selection", "选型台账"),
    route("GET", _CASE + r"/export\.xlsx", "get_export_xlsx", "整本 Excel"),
    route("GET", _CASE + r"/export-family\.xlsx", "get_export_family", "当前分类 Excel"),
    route("GET", _CASE + r"/pipes\.docx", "get_export_pipes_docx", "管道选型一览表 Word"),
    route("GET", _CASE + r"/export\.ai\.zip", "get_export_ai", "AI 分析包"),
    route("GET", _CASE + r"/balance-report\.docx", "get_export_report", "物料与能量衡算报告"),
]
