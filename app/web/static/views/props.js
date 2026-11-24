/* Casebook 视图 · props

Aspen 物性组配置教程:首页「Aspen 物性组」卡片与数据页 p_v 浮层共用同一个弹窗。
不读写 state、不发请求 —— 只做开合、焦点与 aria 状态。

配置本身在 Aspen 侧完成,本工具只读结果节点;教程不发任何写入请求。
*/

let lastFocus = null;

export function initPropsGuide() {
  const modal = document.getElementById("propModal");
  if (!modal) return;

  document.getElementById("homePropOpen")?.addEventListener("click", openPropsGuide);
  document.getElementById("propClose")?.addEventListener("click", closePropsGuide);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closePropsGuide();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) closePropsGuide();
  });
}

export function openPropsGuide() {
  const modal = document.getElementById("propModal");
  if (!modal) return;
  lastFocus = document.activeElement;

  // 从 p_v 浮层进入时先收起浮层,避免两个层叠着
  const pvBody = document.getElementById("pvHelpBody");
  if (pvBody) {
    pvBody.classList.remove("show");
    document.getElementById("pvHelpBtn")?.setAttribute("aria-expanded", "false");
  }

  modal.classList.remove("hidden");
  document.getElementById("propClose")?.focus();
}

export function closePropsGuide() {
  const modal = document.getElementById("propModal");
  if (!modal) return;
  modal.classList.add("hidden");
  if (lastFocus && lastFocus.isConnected) lastFocus.focus();
  lastFocus = null;
}
