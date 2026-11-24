/* 前后端共用的最小工具:请求封装 + 数字/文本格式化。

为什么单独一个模块
------------------
`app.js` 与 `pipe.js` 此前为两段普通脚本,共用全局作用域:pipe.js 调用 app.js 的
esc() 49 次、fmt() 10 次、api() 4 次,且 index.html 中 pipe.js 位于 app.js **之前**;
可运行仅因调用均发生在事件回调中,晚于两侧加载完成,上移至顶层即抛 ReferenceError。
抽出本模块后,依赖由 import 显式声明,与加载顺序无关。

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
