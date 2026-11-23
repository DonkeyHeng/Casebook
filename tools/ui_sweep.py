"""界面回归自检:无头浏览器过一遍 7 个页签,截图 + 收集控制台报错。

改完 style.css / app.js 之后跑一次,比"肉眼看一眼"可靠:
它会把 JS 异常、console.error、以及请求失败都收集起来,退出码非 0 表示有问题。

    python -m tools.ui_sweep                     # 浅色 + 深色各扫一遍
    python -m tools.ui_sweep --theme light       # 只扫浅色
    python -m tools.ui_sweep --out data/preview/ui-check
    python -m tools.ui_sweep --heat --base http://127.0.0.1:8016   # 换热抽屉点击链路

`--heat` 会真的保存一台换热器并写一次默认参数库,只能对**临时库/临时服务**跑;
脚本会在结束时把原有的 `data/config/hx-*.json` 恢复回去。

依赖:websocket-client(`python -m pip install websocket-client`)、本机 Chrome 或 Edge。
服务要先起来(`python -m app.api --port 8000`);脚本不会自己启动服务。
截图落在 --out 指定目录,**不写进仓库**(那里默认在 .gitignore 里)。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PAGES = ["home", "streams", "blocks", "compare", "flow", "pipes", "report"]

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("找不到 Chrome/Edge,请装一个,或用 --browser 指定路径")


class Cdp:
    """够用就好的 CDP 客户端:只做导航、执行脚本、截图。"""

    def __init__(self, port: int):
        import websocket  # 延迟导入:没装依赖时给出更清楚的报错

        self.ws = None
        deadline = time.time() + 20
        target = None
        while time.time() < deadline:
            try:
                raw = (
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2)
                    .read()
                    .decode()
                )
                page = next(t for t in json.loads(raw) if t.get("type") == "page")
                target = page["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.25)
        if not target:
            raise SystemExit("浏览器没起来(调试端口无响应)")
        self.ws = websocket.create_connection(target, timeout=40, max_size=128 * 1024 * 1024)
        self._id = 0
        self.errors: list[str] = []
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")
        self.cmd("Log.enable")

    def cmd(self, method: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                detail = msg["params"]["exceptionDetails"]
                exc = (detail.get("exception") or {}).get("description", "")
                self.errors.append(f"JS 异常: {exc[:200] or detail.get('text', '')}")
                continue
            if msg.get("method") == "Log.entryAdded":
                entry = msg["params"]["entry"]
                if entry.get("level") == "error":
                    self.errors.append(
                        f"{entry.get('source', 'log')}: {entry.get('text', '')[:200]}"
                    )
                continue
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            return None
        return r["result"].get("value")

    def goto(self, url: str, wait_ms: int = 2200) -> None:
        self.cmd("Page.navigate", url=url)
        time.sleep(wait_ms / 1000.0)
        for _ in range(40):
            if self.js("document.readyState") == "complete":
                return
            time.sleep(0.2)

    def shot(self, path: str) -> None:
        r = self.cmd("Page.captureScreenshot", format="png")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


#: `--heat` 只对临时库跑:它会保存一台设备、写一次参数库,再恢复原配置。
def _backup_hx_config() -> dict[str, bytes]:
    root = os.path.join("data", "config")
    backup = {}
    if not os.path.isdir(root):
        return backup
    for name in os.listdir(root):
        if name.startswith("hx-") and name.endswith(".json"):
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                backup[name] = f.read()
    return backup


def _restore_hx_config(backup: dict[str, bytes]) -> None:
    root = os.path.join("data", "config")
    if os.path.isdir(root):
        for name in os.listdir(root):
            if name.startswith("hx-") and name.endswith(".json") and name not in backup:
                os.unlink(os.path.join(root, name))
    for name, data in backup.items():
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, name), "wb") as f:
            f.write(data)


def heat_drawer_flow(cdp: Cdp, base: str) -> None:
    """换热抽屉点击链路:开 HeatX/Heater → 改管程/污垢/预设 → 保存 → 重载验证。

    先选设备数最多的工况;设备列表按 API 返回的顺序取第一台换热器。
    任一步失败直接 raise SystemExit,避免"脚本绿了但抽屉没打开"。
    """
    cdp.goto(base + "/#blocks", 1500)
    case_id = cdp.js(
        "fetch('/api/cases').then(r=>r.json()).then(list=>{"
        "const hit=list.slice().sort((a,b)=>(b.block_count||0)-(a.block_count||0))[0];"
        "return hit ? hit.id : null;})"
    )
    if not case_id:
        raise SystemExit("换热回归:工况列表为空")
    # 工况列表是异步加载的:等选项出现再选,否则 value 设不进去,state 仍是空
    for _ in range(30):
        if cdp.js("document.getElementById('caseSelect').options.length"):
            break
        time.sleep(0.2)
    picked = cdp.js(
        f"""(() => {{
          const sel=document.getElementById('caseSelect');
          const has=[...sel.options].some(o=>o.value==='{case_id}');
          sel.value = has ? '{case_id}' : (sel.options[0]||{{}}).value;
          if (!sel.value) return null;
          sel.dispatchEvent(new Event('change',{{bubbles:true}}));
          return sel.value;
        }})()"""
    )
    if not picked:
        raise SystemExit("换热回归:工况下拉没有可选项")
    time.sleep(2.0)
    opened = cdp.js(
        """(() => {
          const box=document.getElementById('blockFamilies');
          const btn=box && box.querySelector('button[data-family="heat"]');
          if (!btn) return 'no-heat-family';
          btn.click();
          return 'ok';
        })()"""
    )
    if opened != "ok":
        raise SystemExit(f"换热回归:找不到换热分类({opened})")
    for _ in range(40):
        if cdp.js("document.querySelector('#table .heat-link') ? 1 : 0"):
            break
        time.sleep(0.2)
    info = cdp.js(
        """(() => {
          const link=document.querySelector('#table .heat-link');
          if (!link) return null;
          link.click();
          return {block: link.textContent.trim()};
        })()"""
    )
    if not info:
        raise SystemExit("换热回归:换热表里没有 .heat-link")
    for _ in range(40):
        if cdp.js("document.getElementById('heatTube') ? 1 : 0"):
            break
        time.sleep(0.2)
    panel = cdp.js(
        """(() => {const p=document.getElementById('heatPanel');
          return {visible: p && !p.classList.contains('hidden'),
                  title: document.getElementById('heatTitle').textContent};})()"""
    )
    if not panel or not panel.get("visible"):
        raise SystemExit("换热回归:抽屉没打开")

    # 改管程侧 + 污垢档:取值都来自当前下拉,不硬编码设备类型
    changed = cdp.js(
        """(() => {
          const tube=document.getElementById('heatTube');
          const values=[...tube.options].map(o=>o.value).filter(Boolean);
          tube.value=values.find(v=>v!==tube.value) || tube.value;
          const foul=document.getElementById('heatFoulTube');
          const first=[...foul.options].find(o=>o.value);
          if (first) foul.value=first.value;
          const preset=document.getElementById('heatPreset');
          if (preset) {
            const p=[...preset.options].find(o=>o.value);
            if (p) preset.value=p.value;
          }
          return {tube: tube.value, foul: foul.value,
                  preset: preset ? preset.value : null};
        })()"""
    )
    if not changed:
        hint = cdp.js("document.getElementById('heatHint').textContent")
        form = cdp.js("document.getElementById('heatForm').textContent.slice(0,300)")
        raise SystemExit(f"换热回归:抽屉表单没有管程/污垢下拉(hint={hint!r}, form={form!r})")
    cdp.js("document.getElementById('heatSave').click(); 1")
    time.sleep(2.2)
    saved = cdp.js(
        f"""fetch('/api/cases/{case_id}/heat').then(r=>r.json()).then(d => {{
          const row=d.rows.find(r=>r.block==='{info["block"]}');
          return row ? {{tube: row.case.tube_fluid,
                          foul: (row.design_row||{{}}).fouling_tube_key,
                          preset: (row.design_row||{{}}).utility_key,
                          verdict: row.case.verdict}} : null;
        }})"""
    )
    if not saved:
        raise SystemExit("换热回归:保存后 GET 找不到该设备")
    if saved["tube"] != changed["tube"]:
        raise SystemExit(f"换热回归:管程侧未持久化({changed} → {saved})")
    if changed["foul"] and saved["foul"] != changed["foul"]:
        raise SystemExit(f"换热回归:污垢档未持久化({changed} → {saved})")
    if changed["preset"] and saved.get("preset") != changed["preset"]:
        raise SystemExit(f"换热回归:公用工程预设未持久化({changed} → {saved})")

    # 再开一台 HeatX:与 Heater 的管程选项不同,防止只测了工艺侧分支
    heatx = cdp.js(
        """(() => {
          const rows=[...document.querySelectorAll('#table tbody tr')];
          const hit=rows.find(r=>r.cells[1] && r.cells[1].textContent.includes('HeatX'));
          const link=hit && hit.querySelector('.heat-link');
          if (!link) return null;
          link.click();
          return link.textContent.trim();
        })()"""
    )
    if not heatx:
        raise SystemExit("换热回归:没有 HeatX 行")
    for _ in range(40):
        if cdp.js("document.getElementById('heatTube') ? 1 : 0"):
            break
        time.sleep(0.2)
    hx_changed = cdp.js(
        """(() => {
          const tube=document.getElementById('heatTube');
          const values=[...tube.options].map(o=>o.value).filter(Boolean);
          tube.value=values.find(v=>v!==tube.value) || tube.value;
          document.getElementById('heatSave').click();
          return tube.value;
        })()"""
    )
    time.sleep(2.2)
    hx_saved = cdp.js(
        f"""fetch('/api/cases/{case_id}/heat').then(r=>r.json()).then(d => {{
          const row=d.rows.find(r=>r.block==='{heatx}');
          return row ? row.case.tube_fluid : null;
        }})"""
    )
    if hx_saved != hx_changed:
        raise SystemExit(f"换热回归:HeatX 管程未持久化({hx_changed} → {hx_saved})")
    print(f"  HeatX 抽屉 OK:{heatx} · 管程 {hx_changed}")

    # 参数库:改一个经验 U → 保存 → 确认来源变 user → 恢复内置 → 来源回 builtin
    cdp.js("document.getElementById('heatConfigBtn').click(); 1")
    time.sleep(0.8)
    cfg_open = cdp.js(
        """(() => {const p=document.getElementById('heatConfigPanel');
          return p && !p.classList.contains('hidden');})()"""
    )
    if not cfg_open:
        raise SystemExit("换热回归:参数库弹窗没打开")
    cdp.js(
        """(() => {const b=document.querySelector('.heat-cfg-tab[data-tab="u-reference"]');
          if (b) b.click(); return 1;})()"""
    )
    time.sleep(0.5)
    bumped = cdp.js(
        """(() => {
          const input=document.querySelector('#heatConfigPanel input[data-field="default"]');
          if (!input) return null;
          input.value=String(Number(input.value)+10);
          document.getElementById('heatConfigSave').click();
          return input.value;
        })()"""
    )
    if not bumped:
        raise SystemExit("换热回归:参数库没有可编辑的 U 值")
    time.sleep(1.2)
    user_cfg = cdp.js(
        "fetch('/api/heat-config').then(r=>r.json()).then(d=>d['u-reference'].source)"
    )
    if user_cfg != "user":
        raise SystemExit(f"换热回归:参数库保存后 source={user_cfg},应为 user")
    cdp.js("document.getElementById('heatConfigReset').click(); 1")
    time.sleep(1.2)
    builtin_cfg = cdp.js(
        "fetch('/api/heat-config').then(r=>r.json()).then(d=>d['u-reference'].source)"
    )
    if builtin_cfg != "builtin":
        raise SystemExit(f"换热回归:恢复内置后 source={builtin_cfg},应为 builtin")

    # 刷新重载:徽标必须离开"读取中…"
    cdp.goto(base + "/#blocks", 1500)
    cdp.js(
        """(() => {const b=document.querySelector('#blockFamilies button[data-family="heat"]');
          if (b) b.click(); return 1;})()"""
    )
    time.sleep(2.0)
    badge = cdp.js(
        """(() => {
          const cell=document.querySelector('#table tbody .hx-cell');
          return cell ? cell.textContent.trim() : null;
        })()"""
    )
    if not badge or "读取中" in badge:
        raise SystemExit(f"换热回归:刷新后徽标未渲染({badge!r})")
    print(f"  换热抽屉链路 OK:{info['block']} · 徽标 {badge!r} · 参数库 user→builtin")


def run(
    base: str,
    out_dir: str,
    themes: list[str],
    browser: str,
    width: int,
    height: int,
    heat: bool = False,
) -> int:
    port = 9333
    profile = tempfile.mkdtemp(prefix="casebook-ui-sweep-")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    cdp = None
    try:
        cdp = Cdp(port)
        for theme in themes:
            # 先设好主题再进页面,避免截图时还在"上一套主题"里
            cdp.goto(base + "/#home", 1200)
            cdp.js(f"""(() => {{
              document.documentElement.dataset.theme = "{theme}";
              try {{ localStorage.setItem("casebook-theme", "{theme}"); }} catch (e) {{}}
              return 1;
            }})()""")
            for name in PAGES:
                cdp.goto(f"{base}/#{name}")
                cdp.shot(os.path.join(out_dir, f"{theme}-{name}.png"))
        if heat:
            backup = _backup_hx_config()
            try:
                heat_drawer_flow(cdp, base)
            finally:
                _restore_hx_config(backup)
        return len(cdp.errors)
    finally:
        if cdp:
            for err in cdp.errors:
                print("  [报错]", err)
            cdp.close()
        proc.terminate()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Casebook 界面回归自检")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="服务地址")
    ap.add_argument(
        "--out",
        default=os.path.join("data", "preview", "ui-check"),
        help="截图输出目录(默认 data/preview/ui-check,不入库)",
    )
    ap.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    ap.add_argument("--browser", default=None, help="浏览器可执行文件路径")
    ap.add_argument("--size", default="1440x900", help="窗口尺寸,如 1024x768")
    ap.add_argument(
        "--heat",
        action="store_true",
        help="额外跑换热抽屉点击链路(会写库与参数库,只对临时服务跑)",
    )
    args = ap.parse_args(argv)

    try:
        width, height = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        raise SystemExit("--size 要写成 1024x768 这种") from None

    themes = ["light", "dark"] if args.theme == "both" else [args.theme]
    browser = args.browser or find_browser()

    print(f"浏览器: {browser}")
    print(f"目标:   {args.base}  主题: {'+'.join(themes)}  尺寸: {width}x{height}")
    print(f"截图:   {args.out}")
    errors = run(args.base, args.out, themes, browser, width, height, heat=args.heat)
    if errors:
        print(f"发现 {errors} 条控制台报错,见上。")
        return 1
    print(f"{len(PAGES)} 个页签 × {len(themes)} 套主题,无控制台报错。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
