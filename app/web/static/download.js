/* Casebook · download —— 导出文件的"往哪存"

浏览器内:导出经跳转/锚点,由下载栏接手。
桌面壳(app/desktop.py,内置 WebView)无下载栏,文件会落入默认下载目录且难以定位;
壳内改由主进程写入 data/exports/ 并打开文件夹(Windows 资源管理器)。

调用方只需要一行,剩下的都不变:

    if (await saveFromUrl(url)) return;   // 壳里已接手
    window.location.href = url;            // 浏览器:老路径

`installShellHooks()` 另外把 `window.open(外链)` 转给主进程——WebView 里新窗口
打不开,中联选型那种外链会变成"点了没反应"。
*/

/* 只在壳里存在:pywebview 注入的 window.pywebview.api(方括号取值,别撞核心模块的 api) */
function bridge() {
  const wv = window.pywebview;
  const shell = wv && wv["api"];
  return shell && typeof shell.save_export === "function" ? shell : null;
}

export function inShell() {
  return !!bridge();
}

let notifyTimer = 0;

/* 复用页面顶部的 #dataNotice(role=status):存盘成功/失败都让用户看得见 */
function notify(result) {
  const el = document.getElementById("dataNotice");
  if (!el) return;
  el.classList.remove("hidden");
  el.textContent = result && result.ok
    ? `已保存到 ${result.path}(文件夹已打开)`
    : `导出失败:${(result && result.error) || "未知原因"}`;
  clearTimeout(notifyTimer);
  notifyTimer = setTimeout(() => el.classList.add("hidden"), 12000);
}

/* 桌面壳:让主进程按地址取回文件并落盘。返回 true 表示"壳已接手",调用方别再跳转。
   浏览器里返回 false,调用方照原样走。 */
export async function saveFromUrl(url) {
  const shell = bridge();
  if (!shell) return false;
  try {
    notify(await shell.save_export(url));
  } catch (err) {
    notify({ ok: false, error: String(err) });
  }
  return true;
}

let hooked = false;

/* 壳里点「退出」:直接让主进程销毁窗口。返回 true 表示"壳已接手"。
   WebView 内 window.close() 无法关闭自身;浏览器内按原路径处理。 */
export function quitShell() {
  const shell = bridge();
  if (!shell || typeof shell.quit !== "function") return false;
  try {
    shell.quit();
    return true;
  } catch (err) {
    return false;
  }
}

/* 外链改走系统默认浏览器。壳还没就绪时等 pywebviewready 再来一次。 */
export function installShellHooks() {
  if (hooked) return;
  const attach = () => {
    const shell = bridge();
    if (!shell || typeof shell.open_external !== "function") return false;
    const original = window.open.bind(window);
    window.open = (url, ...rest) => {
      if (typeof url === "string" && /^https?:/i.test(url)) {
        shell.open_external(url);
        return null;
      }
      return original(url, ...rest);
    };
    hooked = true;
    return true;
  };
  if (!attach() && typeof window.addEventListener === "function") {
    window.addEventListener("pywebviewready", attach, { once: true });
  }
}
