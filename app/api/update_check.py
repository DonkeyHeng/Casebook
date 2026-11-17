# -*- coding: utf-8 -*-
"""app/api/update_check.py —— 手动「检查更新」的版本比对。

只做一件事:向 GitHub 发一个 HEAD 请求,从 `/releases/latest` 的 302 重定向里
读出最新 tag,和本地 `__version__` 比一比。**不下载、不解压、不替换、不写盘。**

为什么用重定向而不是 REST API
------------------------------
`api.github.com` 未认证请求的限额是 60 次/小时/IP(GitHub 官方文档),
企业 NAT 下多人共享时很容易耗尽;而 `github.com` 的 `/releases/latest`
302 不占这个配额,且 A 方案只需要 tag 一个字段。

隐私与安全边界
--------------
* 请求地址固定为 `app/version.py` 里的 `LATEST_URL`,不接受任何用户输入;
* 只带 User-Agent(含版本号),**不携带任何本地工况数据**、不读 cookie;
* 自定义重定向处理器:只要 `Location`,不真的去下载 Release 页面 HTML;
* `Location` 必须落在仓库自己的 `/releases/tag/` 前缀下,其余一律判为异常;
* 5 秒超时;离线 / 超时 / 响应异常时返回可读提示,不抛到页面、不写日志刷屏。

只有用户在侧栏点击按钮才会走到这里(POST /api/update/check)。
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app import paths, version

TIMEOUT_SECONDS = 5.0
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_TAG_CHARS_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class UpdateCheckError(Exception):
    """检查失败(离线 / 超时 / 响应异常):只给页面一句人话,不当崩溃处理。"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拦下 3xx,不让 urllib 跟着跳到 Release 页面把整份 HTML 拉回来。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913
        return None


def parse_version(tag: str) -> tuple[int, int, int] | None:
    """`v1.3.0` → `(1, 3, 0)`;不符合三段数字版本时返回 None。"""
    match = _VERSION_RE.match(str(tag or "").strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def latest_tag(timeout: float = TIMEOUT_SECONDS) -> tuple[str, str]:
    """返回 (最新 tag, 该 tag 的 Release 页面 URL);失败抛 UpdateCheckError。"""
    request = urllib.request.Request(
        version.LATEST_URL,
        method="HEAD",
        headers={"User-Agent": f"Casebook/{version.__version__}"},
    )
    opener = urllib.request.build_opener(_NoRedirect)
    location = ""
    try:
        with opener.open(request, timeout=timeout) as response:
            location = response.headers.get("Location") or ""
    except urllib.error.HTTPError as exc:
        if exc.code not in _REDIRECT_CODES:
            raise UpdateCheckError(f"GitHub 返回 HTTP {exc.code}") from exc
        location = exc.headers.get("Location") or ""
    except (OSError, ValueError) as exc:
        # 不把 exc 的文本回给页面:可能带本地代理 / 内网信息,这里只留异常类型名
        raise UpdateCheckError(f"无法连接 GitHub({type(exc).__name__})") from exc

    if not location.startswith(version.TAG_URL_PREFIX):
        raise UpdateCheckError("GitHub 返回的重定向格式不符合预期")
    tag = location[len(version.TAG_URL_PREFIX) :].split("/")[0].split("?")[0].split("#")[0]
    if not tag or not _TAG_CHARS_RE.fullmatch(tag):
        raise UpdateCheckError("GitHub 返回的版本号格式不符合预期")
    return tag, location


def check() -> dict:
    """给 `POST /api/update/check` 用的结果字典。

    `has_update` 有三种取值:`True` / `False` / `None`(版本号不是三段数字,
    无法自动比对)。失败时 `ok=False` 并带一句中文原因,页面据此退化成
    「手动打开 Releases 页面」。
    """
    result: dict = {
        "ok": True,
        "current": version.__version__,
        "latest": None,
        "has_update": None,
        "release_url": None,
        "releases_url": version.RELEASES_URL,
        "frozen": paths.is_frozen(),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        tag, url = latest_tag()
    except UpdateCheckError as exc:
        result["ok"] = False
        result["message"] = str(exc)
        return result

    result["latest"] = tag
    result["release_url"] = url
    local, remote = parse_version(result["current"]), parse_version(tag)
    if local is None or remote is None:
        result["message"] = "版本号不是三段数字格式,无法自动比对,请到 Releases 页面查看。"
    else:
        result["has_update"] = remote > local
    return result
