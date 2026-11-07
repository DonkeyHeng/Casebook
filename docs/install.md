# 安装与试用说明(给拿到本项目的同学)

> 这份文档写给**第一次用、不写代码**的人:从零开始装到能在浏览器里看到自己的 Aspen 模型。
> 全程都是复制粘贴,不需要编译器、不需要 Node、不需要动系统设置。
>
> 不想看 markdown 的话:下载包里根目录有一份纯文本的 **`安装说明.txt`**,内容一样,记事本就能看。
>
> 只想看看界面、手头没有 Aspen?跳到 **§6 没有 Aspen 也能先看界面**。
> 想知道管路选型要额外准备什么,见 **§7**。

## 0. 一句话版本

```bash
git clone https://github.com/DonkeyHeng/Casebook.git
cd Casebook
python -m pip install -r requirements.txt
python -m app.api          # 浏览器会自动打开 http://127.0.0.1:8000
```

然后**在 Aspen Plus 里打开你的 `.apw` 并算完**,回到网页点左上角「读取当前工况」。

下面是把每一步拆开、加上校验与排错的长版本。

## 1. 先决条件

| 项 | 要求 | 怎么确认 |
| --- | --- | --- |
| 系统 | **Windows 10 / 11**(唯一支持平台,Aspen 的 COM 只有 Windows 有) | 桌面上能看到「此电脑」 |
| Aspen Plus | **V15**,你自己的正版许可证(其它版本未验证) | 能正常打开 `.apw` 并算完一次 |
| Python | **3.10 及以上**(本机实测 3.15) | 命令行里 `python --version` |
| 依赖 | `pywin32`、`openpyxl`(只有这两个) | 见 §3 |
| 权限 | 能装 Python、能在自己的用户目录写文件(不需要管理员) | —— |
| 会话 | **真实桌面会话**:远程桌面/服务会话里看不到 Aspen 的 COM 对象 | —— |

> 本项目**只读** Aspen:不调用 Run / Reinit,也不会保存你的模型。
> 但它必须和 Aspen **同一权限**运行 —— 如果 Aspen 是"以管理员身份运行",本工具也要。

## 2. 拿到项目文件(三选一)

### 2.1 Windows 包(给不装 Python 的同学,推荐)

1. 打开 <https://github.com/DonkeyHeng/Casebook/releases>
2. 下载 `Casebook-windows-x64.zip`(约 30 MB)
3. 解压到一个**路径里没有空格、不要放 OneDrive 同步盘**的地方,比如 `D:\Casebook`
   - 解压后只有三个文件:`Casebook.exe`、`安装说明.txt`、`LICENSE`
4. 双击 `Casebook.exe`:弹出一个黑色窗口(程序本体,别关),浏览器自动打开界面
   - 不用装 Python、不用 pip、不用改系统设置
   - 没有 Aspen 想先看界面:**不用开命令行** —— 首页提示「还没有工况」时,
     点卡片里的 **「载入示例工况」** 按钮(原库会先备份成 `cases.db.bak`)
   - 命令行方式也可以:关掉窗口后执行 `.\Casebook.exe --sample`

> exe 没有代码签名,个别杀毒软件会提示"未知程序";它只监听本机 `127.0.0.1`,不往外网发数据。

### 2.2 源码压缩包(有 Python 的同学)

1. 打开 <https://github.com/DonkeyHeng/Casebook/releases>
2. 下载 **Source code (zip)** 资产,例如 `Casebook-1.0.2.zip`(约 610 KB)
3. 解压到一个**路径里没有空格、不要放 OneDrive 同步盘**的地方,比如 `D:\Casebook`
   - 解压后是一个 `Casebook` 文件夹,根目录能看到 `start.cmd`、`requirements.txt`、
     `安装说明.txt`、`app`
   - 只给开发者的 `AGENTS.md`、`.github/`(贡献指南、安全策略)与 `model/`、`model-full/`、
     `standards/` 等占位目录**不在包里**(见 `.gitattributes` 的 `export-ignore`)

> 压缩包里**没有** Aspen 模型、**没有**标准 PDF 原文、**没有**你自己的本地工况库(`data/cases.db`);
> 它附带一份示例工况库 `samples/case-23-error.db`,用来离线看界面(见 §6)。

### 2.3 git clone(要跟着更新的人)

```bash
git clone https://github.com/DonkeyHeng/Casebook.git
cd Casebook
```

以后更新就是 `git pull`。

## 3. 装 Python 与依赖

### 3.1 装 Python

到 <https://www.python.org/downloads/windows/> 下载 **Python 3.12 或更新的 64 位安装包**,
安装第一屏**务必勾上 `Add python.exe to PATH`**,然后一路下一步。

装完**新开一个**命令行窗口验证:

```bash
python --version          # 应该显示 Python 3.1x.x
python -m pip --version   # 应该显示 pip 版本
```

> **坑**:如果输入 `python` 弹出的是微软应用商店,说明系统里那个"应用执行别名"占了位置。
> 要么到「设置 → 应用 → 高级应用设置 → 应用执行别名」把 `python.exe` / `python3.exe` 关掉,
> 要么用 `py -3 --version`(项目里的 `start.cmd` 会自动退回到 `py -3`)。

### 3.2 装依赖

在**项目文件夹里**(能看到 `requirements.txt` 的那一层)执行:

```bash
python -m pip install -r requirements.txt
```

只有两个包:`pywin32`(读写 Aspen 的 COM 接口)和 `openpyxl`(导出 Excel)。

国内网络慢的话加上清华镜像,下载更快:

```bash
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

不放心可以验证一下(两行都不报错即可):

```bash
python -c "import win32com.client, openpyxl; print('依赖就绪')"
```

> **坑**:如果之后运行时报 `ModuleNotFoundError: No module named 'win32com'`,
> 多半是「装依赖的 Python」和「运行项目的 Python」不是同一个。用
> `python -m pip show pywin32` 和 `where python` 对照一下路径。

## 4. 启动

> 用的是 **Windows 包(exe)** 的话,第 2.1 节的"双击 exe"就是这一步;
> 下面的命令行参数对 exe 同样适用,只是把 `python -m app.api` 换成 `.\Casebook.exe`。

### 4.1 双击 `start.cmd`(最省事)

它会依次检查 Python、检查依赖、启动服务,并自动打开浏览器;
保持那个黑色窗口开着,关掉窗口服务就停了。

### 4.2 命令行启动(要看报错时用这个)

```bash
python -m app.api
```

看到 `127.0.0.1:8000` 字样就说明起来了,浏览器没自动开就手动访问 <http://127.0.0.1:8000>。

常用参数:

```bash
python -m app.api --port 8001            # 8000 被占用时换端口
python -m app.api --no-browser           # 不自动开浏览器
python -m app.api --db data/cases.db      # 指定工况库位置(默认就在 data/ 下)
python -m app.api --sample                # 装入内置示例工况(原库备份成 .bak)
```

停止服务:网页左下角的「退出」按钮,或者直接关掉那个命令行窗口。

### 4.3 第一次打开会看到什么

- 首页提示"还没有工况" —— **正常**,仓库不带本地工况库(那是私人数据)
- 第一次启动会自动生成 `data/cases.db`(空库)
- 左栏「管道」页会显示"标准表未录入" —— **正常**,原因见 §7,不影响"读取工况"

## 5. 接上自己的 Aspen 模型

1. **先在 Aspen Plus 里打开 `.apw`**,并让计算正常收敛(Aspen 窗口标题形如 `xxx.apw - Aspen Plus V15`)
2. 回到网页,点左上角蓝色按钮 **「读取当前工况」**
3. 等进度条走完(百来条流股大约 15–40 秒),之后左栏会多出这个工况
4. 读数存在本地库里,**存完就可以关掉 Aspen**,流股、设备、流程、对比都还能看

### 5.1 报"没有找到打开着的 Aspen 模型"

这是最常见的一条,按顺序排查:

1. Aspen 是否**已经打开**并加载了 `.apw`(没打开的话本工具不会替你启动,冷启动太慢)
2. Aspen 是否"以管理员身份运行"—— 若是,本工具也必须同样提权运行
3. 是否在**远程桌面 / 服务会话 / 沙箱**里跑 —— 这类会话里 ROT 是空的,看不到 Aspen 的对象,
   必须在正常桌面会话里运行

仍不行就用诊断脚本,把输出发给维护者:

```bash
python tools/probe_aspen.py
```

## 6. 没有 Aspen 也能先看界面

仓库里带了一份**真实但失败**的工况快照(121 流股 / 77 模块,含两个塔的报错),
用来演示界面和判定逻辑,不需要 Aspen、不需要许可证。

源码包(有 `samples` 目录):

```bash
copy samples\case-23-error.db data\cases.db     # 覆盖前先备份你自己的 data\cases.db
python -m app.api
```

Windows 包(exe,示例库已经打进程序里):双击 `Casebook.exe` 打开界面,首页点
**「载入示例工况」** 按钮即可;命令行方式:

```powershell
.\Casebook.exe --sample
```

打开网页就能看到这个工况的流股、设备、问题横幅、流程图与对比页。
细节见 [`samples/README.md`](../samples/README.md)。

## 7. 管路选型(可选):要自己导入三张国标表格

「管道」页按流股算管径与壁厚,依赖三张表:**GB/T 17395-2024 表 1 / 表 4** 和 **GB/T 20801.1-2025 表 B.1**。
这些数值受著作权保护,**不随项目分发**,所以仓库和安装包里都没有。

自己要补的话:

1. 取得上面两份标准(正版纸质/电子,或单位订阅的标准库)
2. 整理成三个 CSV(文件名固定)
3. 打开「管道」页右上角的 **「标准表」** 按钮 → 选文件 → 「导入并立即生效」,不用重启服务

导入前会校验文件名、编码、必需列与行数;只要有一个文件不合格就整单不写,不会留下半份坏数据。
想要一份"复制给 AI 让它帮着抄数"的提示词,见
[`docs/pipe-standards-extract-prompt.md`](pipe-standards-extract-prompt.md);
页码与分页结构见 [`docs/pipe-standards-index.md`](pipe-standards-index.md)。

不导入也能用其它所有功能,只是「管道」页会一直显示"标准表未录入"。

> 如果你的网页上**没有**「标准表」按钮,说明拿到的是更早的版本:那就把三个 CSV
> 放进 `data/preview/std/docx/`(没有这个目录就新建),然后重启服务再刷新页面。

## 8. 数据存在哪里、怎么升级、怎么卸载

| 内容 | 位置 | 要不要备份 |
| --- | --- | --- |
| 工况库(所有读过的快照) | `data/cases.db` | **要** |
| 管路选型的三张标准表 | `data/preview/std/docx/*.csv` | 要(重新录入很费劲) |
| 管道等级表配置 | `data/config/piping-classes.json` | 建议要 |
| 导出物(Excel / AI 包) | 你自己选的保存位置 | —— |

`data/` 整个目录都不入库、不上传,换机器拷走它就行。

- **升级**:下载新压缩包解压到新目录,把旧目录的 `data/` 整个拷过去。旧库会自动补齐新增的表结构
  (`app/store/db.py` 的 `init_schema` 里有 `ALTER TABLE … ADD COLUMN` 的迁移逻辑)。
- **卸载**:删掉项目文件夹即可,不写注册表、不装服务。
- **清空数据**:关掉服务后删 `data/cases.db`(先备份),下次启动会重新建空库。

## 9. 常见问题

| 现象 | 处理 |
| --- | --- |
| `python` 不是内部或外部命令 | 装 Python 时没勾 `Add python.exe to PATH`;重装或改用 `py -3` |
| 输入 `python` 弹出应用商店 | 关掉应用执行别名(见 §3.1),或用 `py -3` |
| 双击 `start.cmd` 黑窗口一闪而过 | 在该文件夹里按住 Shift 右键 → 「在此处打开 PowerShell 窗口」→ 手动跑 `python -m app.api`,看报错 |
| 端口 8000 被占用 | `python -m app.api --port 8001`,再访问 `http://127.0.0.1:8001` |
| 浏览器没自动打开 | 手动访问 `http://127.0.0.1:8000` |
| 点「读取当前工况」说找不到 Aspen | 见 §5.1 三条:Aspen 打开了吗、权限一致吗、是不是远程/沙箱会话 |
| 读取很慢、像卡住了 | 大模型逐节点读,百条流股 15–40 秒;页面上有进度与"卡在哪一步"的提示 |
| 表格里有很多 `—` | 不是没结果,是单位没被换算表覆盖(多见于老模型的 ENG 单位集),顶部「单位诊断」有计数 |
| 管道页显示"标准表未录入" | 正常,见 §7;不影响其它功能 |
| 关掉命令行窗口后网页打不开了 | 服务跟着窗口一起停了;重新启动即可 |
| 想换一台电脑继续看历史工况 | 把 `data/` 拷过去(工况已存在库里,不需要 Aspen) |

## 10. 实测记录

- 2026-11-07:把 Release 的 `Casebook-1.0.2.zip` 解压到干净目录,按 §3 装好依赖后
  `python -m app.api` 启动成功;首次启动自动创建 `data/cases.db`,首页返回 200,
  `/api/cases` 返回 `[]`(空库),`/api/pipe-standards` 正常返回三张表的状态 ——
  与 §4.3、§7 的描述一致。
- 精确体积与文件数、SHA-256 见 Release 说明。**这几个数每次重打都会变**,所以不写死在这里 ——
  写死过一次,下一轮就过时了(文档写 603,400,实际已经 625,100)。
- 想核对下载是否完整:命令行执行 `certutil -hashfile Casebook-1.0.2.zip SHA256`,
  和 Release 说明里那行 SHA-256 对比即可。
- 读数与导出在同一天用 Aspen Plus V15 + Python 3.15 验证过;其它 Aspen 版本、其它 Python 版本
  **未验证**,遇到问题请提 issue 并附 `python --version` 与报错原文。
