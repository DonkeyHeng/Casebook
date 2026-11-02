# aspen-insight — 会话工作约定

## 0. 接手先读

- 只读 `HANDOFF.md` 第 0 节 + `docs/aspen-field-map.md` 就能接手。**不要重新访谈、不要重新探测 Aspen。**
- 当前下一步是**管路选型**:口径已定稿、代码未写。开工只读 `docs/pipe-selection-plan.md`(§14 定稿口径 + §15 开工清单)与 `docs/pipe-class-codes.md`。

## 1. 动手前先查现成实现

新功能 / 新算法 / 没做过的技术问题,先看别人怎么做的,再动手:

1. `web_search` 加 `site:github.com` 定位候选仓库;拿不到就走下面的 API。
2. 有 token 时用 **Python** 调 API(`web_fetch` 带不了请求头,用不了 token):

   ```python
   import urllib.request, json, os
   tok = os.environ.get("GITHUB_TOKEN") or open(r"<token-file>").read().strip()
   req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}", "User-Agent": "dsh"})
   ```

   - 代码搜索:`https://api.github.com/search/code?q=<关键词>+in:file+language:python`(必须带 token,匿名 401)
   - 仓库搜索:`https://api.github.com/search/repositories?q=<关键词>&sort=stars&per_page=10`
3. 读单文件全文:`https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>`。
4. 要看整个仓库:Python 抓 `https://codeload.github.com/<owner>/<repo>/zip/refs/heads/<branch>`,再用 `zipfile` 解压。
5. 交付时补一句「参考了什么 / 为什么不用它」,再开工。

## 2. 网络与工具的真相(别重复踩)

- `web_fetch` 可用,但**只能传 URL、不能加请求头** → 匿名,带不了 token。
- **沙箱里 Python 有完整外网**(`urllib.request` 实测 200),这是调 GitHub API 的正路。
- `curl` 不可用:DNS 能解析,但 TLS 报 `schannel: AcquireCredentialsHandle failed (SEC_E_NO_CREDENTIALS)`。
- `git clone` 不可用:`cannot create standard input pipe for remote-https`。`git add/commit/log/diff` 正常;`git push` 需提权。
- 匿名额度很小且出口 IP 共享(约 60 次/小时),**查不动就直接说查不动**,不许假装查过、不许凭印象编仓库名。

## 3. 改代码的硬约束

- 静态文件(HTML/JS/CSS)改完刷新即生效;**Python 改了必须重启服务**(`pythonw -m app.api --port 8000` 或双击 `start.cmd`)。
- 动了判定 / 单位 / 焓平衡口径,先跑 `python -m tools.selftest --db data/cases.db`,再跑 `python -m tools.repair_cases --apply` 回算历史工况 —— 不用回 Aspen 重读。
- `HANDOFF.md` §0.3、§2 已拍板的决定不要再翻;夹点分析 / 公用工程成本用户明确说**暂缓**。

## 4. 数字纪律

- 结论里的数字必须来自实测,注明样本规模与工况号(现有 #19–#22)。没验证就写「未验证」,不要估算当实测。
