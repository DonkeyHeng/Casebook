/* 前后端共用的最小工具:请求封装 + 数字/文本格式化。

为什么单独一个模块
------------------
`app.js` 与 `pipe.js` 以前是两段普通脚本,共用全局作用域:pipe.js 调 app.js 里
的 esc() 49 次、fmt() 10 次、api() 4 次,而 index.html 里 pipe.js 还排在 app.js
**前面** —— 能跑只是因为那些调用都发生在事件回调里,晚于两侧加载完成。谁把它们
提到顶层就是 ReferenceError。抽到这个模块后,依赖关系由 import 明说,与加载
顺序无关。

只放"不依赖页面状态"的东西:这里不许读 `state`、不许碰 DOM。
*/

export function writeInit(extra = {}) {
  return { ...extra, headers: { "X-Casebook-Client": "1", ...(extra.headers || {}) } };
}

export async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `请求失败:${res.status}`);
  }
  return res.json();
}

export async function apiPost(path, body) {
  const res = await fetch(path, writeInit({
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.message || `请求失败:${res.status}`);
  }
  return res.json();
}

/* DELETE 单独一个:api() 只做 GET,别为了它把方法参数塞进去到处传 */
export async function apiDelete(path) {
  const res = await fetch(path, writeInit({ method: "DELETE" }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `请求失败:${res.status}`);
  }
  return res.json();
}

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

export function fmt(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value !== "number") return esc(value);
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtVisc(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return esc(value);
  return fmt(value, Math.abs(value) >= 10 ? 2 : 4);
}

export function fmtDelta(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return esc(value);
  if (value === 0) return "0";
  const sign = value > 0 ? "+" : "";
  return sign + fmt(value, digits);
}
