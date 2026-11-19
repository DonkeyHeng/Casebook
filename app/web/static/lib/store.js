/* 所有页面共用的页面状态:当前工况、流股/设备表、各页子视图。

为什么单独一个模块
------------------
以前靠全局作用域共享:普通 <script> 里 `const state` 其实不在 window 上,
其它文件能读到全靠脚本加载顺序与全局词法环境 —— 换个加载方式就断。
现在每个模块显式 `import { state }`,依赖关系在文件头上就能读出来。

这里只放**数据**,不放渲染。字段归属(谁读写谁,详见各视图模块的 docstring):

  table        streams / blocks / tab / family / families / compare / impact
               / pumpSpec / filter
  pump         pump / pumpDesign / pumpSelection / pumpFold / pumpSpec
  composition  column / streams
  flow         flow
  report       report(勾选 / 过滤 / 数据缓存;字段挂在这里由 views/report.js 初始化)
  shell        current / cases / page / emptyText / emptyIsError / bannerCollapsed
               (载入工况时顺带重置各页子状态)
  read         不直接持有状态,读完经 shell 的 loadCase / refreshCases 更新
*/

export const state = {
  cases: [],
  current: null,
  streams: [],
  blocks: [],
  tab: "streams",          // 数据页里的子视图:streams / blocks / compare
  page: "home",            // 当前页面:home / streams / blocks / compare
  emptyText: "",           // 空态文案(读不到工况列表等错误也走这里)
  emptyIsError: false,     // 空态是「出错」还是「本来就还没有工况」
  flow: {                  // 流程页:数据 + 布局 + 视图 + 聚焦
    caseId: null, data: null, layout: null, view: null, focus: null,
    center: null,          // 被点中的设备(和 2 层邻居分开配色)
    showLabels: false,
  },
  filter: "",
  family: null,            // 设备页当前家族(column/heat/…),null = 还没挑
  families: [],            // 家族表(/api/block-families)
  pumpSpec: null,          // 泵口径常量(/api/pump-spec):g / 电机效率 / 档位表 / 公式说明
  pump: {
    margin: 1,             // 泵页顶部参数条的余量:默认「原样」,和下拉框的初始值保持一致
    promptText: null,      // 「复制选泵提示词」的正文缓存(/api/pump-prompt,首次点击才取)
  },
  // 设计扬程辅助:两侧管段与「管道」页共用同一份 pipe_run(按模型 + 流股);
  // 这里只放页面态:后端返回的视图、两侧未保存的编辑值、展开的那一侧
  pumpDesign: {
    view: null,
    versions: {},
    edits: {},
    openSide: null,
    legacy: null,
    source: null,
  },
  pumpSelection: null,
  // 泵参数卡三个折叠区的展开状态(同一会话内切设备回来保持);
  // 详细参数是主读数,默认展开;两块人填区默认收起
  pumpFold: { params: true, design: false, selection: false },
  bannerCollapsed: false,
  compare: { baseId: null, otherId: null, data: null, scope: "streams" },
  impact: null,            // 报错波及范围(/api/cases/{id}/impact):哪些流股不能用
  column: null,
};
