# docs/ — 文档

| 文档 | 内容 | 什么时候读 |
| --- | --- | --- |
| `install.md` | **安装与试用说明**:装 Python 与依赖、启动、接自己的 Aspen、没有 Aspen 怎么先看界面、数据在哪、常见问题 | 第一次装、发给别人测试时 |
| `install-text.txt` | 上一份的**纯文本版**(发布包根目录里改名为 `安装说明.txt`) | 打包发布、改安装步骤时 |
| `aspen-field-map.md` | Aspen Plus COM 的**真实字段路径**(实测版本)。流股、组成、设备、收敛、塔剖面都在这里 | 排错、二次开发、要加新字段之前 |
| `api.md` | 本地 HTTP 接口参考(读取 / 对比 / 导出 / 组成) | 排错、写集成时 |
| `conventions.md` | 文件命名、目录归属、README 与提交信息规范 | **新建 / 改名文件之前** |
| `pipe-class-codes.md` | HG/T 20519.6 管道等级号规则 + 本项目约定(压力代号 / 顺序号 / 材质代号 / 绝热后缀) | 写管路选型代码、核对等级号之前 |
| `pipe-standards-index.md` | 管路选型要用的国标表格**索引**:表 1 / 表 4 / 表 B.1 各在第几页、分页结构与校验方法 | 要录标准数据、或怀疑页码不对时 |
| `pipe-standards-extract-prompt.md` | **交给 AI 的录入提示词**:要哪几张表、三个 CSV 的文件名与列、格式硬要求、自检办法 | 自己要补标准数据时(整段复制给能看图的 AI) |
| `pipe-selection-plan.md` | 管路选型功能方案:口径、校验项与不做的事 | 改选型算法之前 |
| `pump-selection-plan.md` | 选泵功能方案:泵参数从哪来、NPSHₐ 口径、中联选型入口与「不自动对接」的理由 | 改泵参数或做选型台账之前 |

## 阅读顺序

1. `install.md` —— 先把应用跑起来
2. `aspen-field-map.md` —— 数据从哪来、字段叫什么
3. 当前任务对应的文档(如管路选型:`pipe-selection-plan.md` + `pipe-class-codes.md`;泵参数与选型:`pump-selection-plan.md` + `aspen-field-map.md` §13)

## 约定

- 结论里的数字必须**实测**,注明样本规模与工况号;没验证就写「未验证」,不估算当实测。
- 标准原文放 `standards/`(不入库,需自备),**只有规则摘要进 docs**。
