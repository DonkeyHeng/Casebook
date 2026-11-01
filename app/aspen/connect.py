"""附着到已在运行的 Aspen Plus,拿到底层数据树。

为什么不用 GetActiveObject("Apwn.Document")
-------------------------------------------
实测(Aspen Plus V15 / COM 41.0):用户手工打开的 Aspen 只把文档注册成**文件 moniker**
(形如 `D:\\...\\model.apw`),不注册 `Apwn.Document` 这个 ProgID。
所以 `GetActiveObject("Apwn.Document")` 永远返回 MK_E_UNAVAILABLE(-2147221021),
必须 `GetObject(apw 路径)` 才能附着。

另外:DSH 之类的沙箱里 ROT 是空的(连 Shell.Application 都拿不到),
本模块必须在**真实桌面会话**运行,否则会误报"没有打开的模型"。
"""
from __future__ import annotations

import pythoncom
import win32com.client

# Aspen 文档在 ROT 里的后缀
MODEL_SUFFIXES = (".apw", ".bkp", ".apwz", ".bkpz")

_NOT_RUNNING_HINT = """没有找到打开着的 Aspen 模型。请依次检查:

  1. Aspen Plus 是否已经打开,并且加载了 .apw(窗口标题形如 `xxx.apw - Aspen Plus V15`)
  2. Aspen 是否"以管理员身份运行" —— 若如此,本程序也必须用同样权限启动
  3. 本程序是否跑在沙箱/容器里 —— 沙箱内看不到 Aspen 的 COM 对象(ROT 为空),
     请在真实桌面会话(双击 .bat / 普通终端)里运行
"""


class AspenNotRunning(RuntimeError):
    """ROT 里找不到任何打开着的模型。"""


class AspenSession:
    """一个已附着的 Aspen 文档。

    `doc` 是 COM 文档对象,`tree` 是数据树(`doc.Tree`)。
    """

    def __init__(self, doc, tree, model_path: str):
        self.doc = doc
        self.tree = tree
        self.model_path = model_path

    @property
    def process_id(self):
        try:
            return int(self.doc.ProcessId)
        except Exception:
            return None

    def find(self, path: str):
        """按路径取节点,不存在返回 None(不抛异常)。"""
        try:
            return self.tree.FindNode(path)
        except Exception:
            return None

    def value(self, path: str):
        """取节点值,不存在/读不到返回 None。"""
        node = self.find(path)
        if node is None:
            return None
        try:
            return node.Value
        except Exception:
            return None

    def __repr__(self) -> str:
        return f"<AspenSession {self.model_path!r} pid={self.process_id}>"


def list_open_models() -> list[str]:
    """列出 ROT 里所有打开着的 Aspen 模型路径。

    同一份文档可能注册成多个 moniker(文件 + composite),这里做去重。
    """
    ctx = pythoncom.CreateBindCtx(0)
    found: list[str] = []
    try:
        rot = pythoncom.GetRunningObjectTable()
        for moniker in rot:
            try:
                name = moniker.GetDisplayName(ctx, None)
            except Exception:
                continue
            if not name.lower().endswith(MODEL_SUFFIXES):
                continue
            # 去掉 composite moniker 的 `!\` 尾巴
            clean = name.split("!\\")[0]
            if clean not in found:
                found.append(clean)
    except Exception as exc:  # ROT 不可用(沙箱/COM 未初始化)
        raise AspenNotRunning(f"读取 ROT 失败:{exc}\n\n{_NOT_RUNNING_HINT}") from exc
    return found


def attach(model_path: str | None = None) -> AspenSession:
    """附着到已打开的模型。

    Args:
        model_path: 指定要附着的 .apw 路径;None 时取 ROT 里的第一个。
    """
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass  # 已经初始化过(RPC_E_CHANGED_MODE 之类),不影响

    models = list_open_models()
    if not models:
        raise AspenNotRunning(_NOT_RUNNING_HINT)

    if model_path is None:
        chosen = models[0]
    else:
        norm = model_path.replace("/", "\\").lower()
        match = [m for m in models if m.replace("/", "\\").lower() == norm]
        if not match:
            available = "\n".join(f"  - {m}" for m in models)
            raise AspenNotRunning(
                f"没有找到已打开的模型:{model_path}\n当前打开的是:\n{available}"
            )
        chosen = match[0]

    try:
        doc = win32com.client.GetObject(chosen)
    except Exception as exc:
        raise AspenNotRunning(
            f"附着失败({chosen}):{exc}\n\n{_NOT_RUNNING_HINT}"
        ) from exc

    try:
        tree = doc.Tree
        tree.FindNode(r"\Data\Streams")  # 验证树可用
    except Exception as exc:
        raise AspenNotRunning(f"附着后拿不到数据树:{exc}") from exc

    return AspenSession(doc, tree, chosen)
