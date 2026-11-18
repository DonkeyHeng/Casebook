# tools/fixtures/ — COM 节点树夹具

给 `tools/selftest.py` 的 `test_reader_fixture*()` 回放用的**脱敏节点树**。
它们让 `app/aspen/reader.py` 能在**不装 Aspen、不装 pywin32**的机器上被整链验证:
从流股表一直查到焓平衡、单位缺口与组分字典。

| 目录 | 来源 | 内容 | 守住什么 |
| --- | --- | --- | --- |
| `v15-real/tree.json` | 真机采集(2026-11-18) | 8 流股 / 换热器 + 泵 + 45 级塔 / 1500 节点 | 真实 Aspen 树叶子的形状:HeatX 双侧、泵的 `PBUB`+`MUMX`、塔剖面;该工况判定 `error`,顺带守"状态差不等于表空" |
| `v15-typical/tree.json` | 手写 | 6 流股 / 塔 + 双流股换热器 + 泵 / 270 节点 | 全链路读取与换算(刻意用 t/hr、kPa、sqft、ft、kcal/hr 等非目标单位) |
| `v11-pump/tree.json` | 手写 | 1 流股 + 1 泵 / 72 节点 / V11 布局 | `PBUB` 下没有 `TOTAL` 层、粘度在 `MIXED` 叶子上 |

`v15-real` 采自作者自己的全流程模型:流股/设备名改成 `S01…`/`B01…`,引用到夹具外的
位号改成 `R01…`(没有反查表),`USER`/`MACHINE`/`NODENAME`/`RUNID` 已清。
数值是真实读数,只用于核对口径,**不能作为设计依据**。

## 只能用自己(或有明确授权)的模型

改名不等于脱敏。夹具里留下的是**真实读数**——温度、压力、流量、焓、热负荷、组分清单,
这些数字本身就是工艺流程信息,能反推产能与操作窗口。所以:

- 他人的模型、比赛模型、同事发来的 `.bkp`:**一律不采**。需要看别人的结构,只做
  口头或文档层面的结论(例如"V11 的 `PBUB` 没有 `TOTAL` 层"),不落盘、不入库。
- `v11-pump` 是**手写**的:只记录版本布局这一事实,数值全部是构造值,不含任何真实读数。
- `v15-typical` 同样是手写:数值按热平衡反算,用来钉换算口径。
- 采集脚本开头会打印这条警告;`AGENTS.md` §4 有对应约定。

## 文件格式(schema 1)

```json
{
  "case": "v15-typical",
  "meta": {
    "schema": 1,
    "source": "collector",
    "aspen_version": "V15",
    "unit_set": "METCBAR",
    "collected_at": "2026-11-19 09:12",
    "model_path": "model/v15-typical.apw",
    "server": "Aspen Plus V15 (fixture)",
    "unread_ok": ["\\Data\\Streams\\*\\Output\\PL\\MIXED\\LIQUID\\*"],
    "note": "…"
  },
  "nodes": {
    "\\Data\\Streams\\S01": {"kids": ["Output"], "attrs": {"6": "MATERIAL"}},
    "\\Data\\Streams\\S01\\Output\\RES_TEMP": {"v": 85.0, "u": "C"}
  }
}
```

节点字段:

| 键 | 含义 |
| --- | --- |
| `v` | 值。缺省 = 读不到,不是 0 |
| `u` | `UnitString`。缺省 = 空 |
| `kids` | 子节点名,**顺序即 `Elements` 顺序**。省略时按路径前缀推导(自然序,`"10"` 在 `"2"` 之后);空数组表示"确认是叶子" |
| `attrs` | OptionPack 属性,`"6"` 是可读类型(RadFrac / HeatX / Pump / MATERIAL) |
| `err` | 取 `Value` 时抛异常(模拟 COM 抽风) |

`meta.unread_ok` 是**故意留着不读**的路径(支持 `*` 通配),例如 `PL` 的组分叶子 ——
它的用处正是证明 reader 不会拿它当混合物 p_v。手写夹具的用例会检查"除 allowlist 外
没有死数据";真机采集的那份不查这条 —— 白名单本来就保留了 reader 的候选路径
(`HX_AREAP` / `FCOLD`)和整段 `STRM_UPP`,天然有大量未读节点。

## 怎么采(真机)

在**真实桌面会话**里打开 Aspen、Run 过模型,然后:

```bash
python tools\dump_fixture.py --case v15-typical --streams 8 --blocks 6 --aspen-version V15

python -m tools.selftest                      # 825 条,必须全绿
python -m tools.check_public_safe --all       # 脱敏门禁,必须 0 命中
```

采集脚本会自动改名(流股 → `S01…`、设备 → `B01…`),并把引用这些名字的字段值
一并改写;指向**没采的**流股/设备的位号改成 `R01…`。另外清掉
`RUNID` / `USER` / `MACHINE` / `NODENAME`,模型路径固定成 `model/<case>.apw`。
**原名不写进夹具**,没有反查表。改完可以这样自查:

```bash
python - <<'EOF'
import json
names = {"<模型里的流股/设备名>", ...}   # 从 probe_aspen 或界面抄几个
t = json.load(open("tools/fixtures/<case>/tree.json", encoding="utf-8"))
print([(p, e.get("v")) for p, e in t["nodes"].items()
       if isinstance(e.get("v"), str) and e["v"] in names])
EOF
```

## 约定

- **只采读取面**:流股 `Output`、设备 `Output` + `Input`、收敛状态、撕裂流、组分字典,
  且在这几层里再按 `dump_fixture.py` 的白名单过滤。实测单条流股的 `Output` 有 120 个
  子节点 / 469 个后代,其中 `STR_MAIN`(119)、`X1`/`X2`、各种 `*_CYCLE` 全是界面用的,
  reader 一个都不读 —— 不裁剪的话 8 条流股就能吃满节点上限,设备反而采不进来。
  整棵 `\Data` 有上万节点、几 MB,`check_public_safe` 对超过 2 MB 的文件直接跳过,
  等于脱敏门禁失效 —— 单份夹具必须远小于 2 MB。
- **`meta.truncated` 必须是 `false`**。撞到 `--cap` 时脚本会返回非 0 并标记,
  这种夹具是残缺的,reader 会静默读到空值,不要接进用例。
- **`reader.py` 新增要读的字段,要同步扩 `dump_fixture.py` 的白名单并重采**;
  `test_reader_fixture_real()` 里有一条"白名单覆盖 reader 依赖字段"的栅栏会先红。
- **夹具是观测记录,不是真值**。它能证明"结构没漂、换算没断",证明不了"这个读数对";
  读数对不对以 `probe_*.py` 的真机实测为准(见 `docs/aspen-field-map.md`)。
- **版本另开目录,不覆盖旧夹具**。Aspen 换了结构就新加一份(如 `v16-…`),
  旧夹具原地保留 —— 就地覆盖会让"这个口径曾经在哪版成立"失去证据。
- `v15-typical` / `v11-pump` 的 `source` 是 `handcrafted`:节点形状按
  `docs/aspen-field-map.md` 构造,数值按热平衡反算(所以 `balance` 应全 `ok`)。
  手写夹具的职责是钉换算口径,真机夹具(`v15-real`)的职责是钉真实叶子形状,
  两者都留 —— 不要用一个替换另一个。

## 能不能删

不能。删掉或改名会让 `test_reader_fixture*()` 直接失败(它按目录名加载)。
要换夹具,先把新夹具采进来、跑绿,再删旧的。
