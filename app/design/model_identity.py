"""模型身份:内容哈希优先,文件名兜底。

为什么需要
----------
原来「同模型」= `cases.model_name` 完全一致,而它只是文件名。
同名不同内容的两份模型会被当成同一个,p_v 沿用 / 工段边界沿用 /
手填 CAS / 设计扬程修正项就会串。这里把身份升级成模型文件内容的
sha256;读不到文件时返回 None、退回文件名 —— 不编造身份,
`model_hash` 列后加的老工况也不会因此失效。

本模块只做纯计算与本地文件读取,不碰 Aspen COM,也不写库。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PREFIX = "sha256:"

# (路径, 大小, mtime) → 内容哈希或 None。模型改了 key 自然变,缓存不会给旧值。
_CACHE: dict[tuple[str, int, float], str | None] = {}


def hash_file(path) -> str | None:
    """模型文件内容的 sha256(hex);路径为空 / 文件不存在 / 读不了返回 None。

    按 (路径, 大小, mtime) 缓存:同一份文件重复读工况不重复算全文。
    读不到一律返回 None,不拿文件名或大小拼一个假哈希顶替。
    """
    raw = str(path or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw)
        st = p.stat()
        key = (raw, st.st_size, st.st_mtime)
        if key in _CACHE:
            return _CACHE[key]
        h = hashlib.sha256()
        with p.open("rb") as fp:
            for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except (OSError, ValueError):
        return None
    _CACHE[key] = digest
    return digest


def key_of(model_hash, model_name) -> str:
    """统一身份键:有 hash 用 hash,没有用文件名(老工况兼容)。

    所有「按模型挂」的手填数据、以及找同模型的历史工况,都必须走这一个
    函数算键,别再各自直接拿 model_name 比 —— 否则又会出现只改一处的分叉。
    """
    h = str(model_hash or "").strip()
    if h:
        return PREFIX + h.removeprefix(PREFIX)
    return str(model_name or "").strip()


def same_model_key_sql(alias: str = "c") -> str:
    """SQL 侧算同一个键,给「找同模型更早工况」的 WHERE 用。

    与 `key_of()` 保持完全一致的口径:有 hash → 'sha256:'||hash,否则文件名。
    写成 SQL 表达式是为了不把整张 cases 表拉回 Python 再过滤。
    """
    return (
        f"(CASE WHEN {alias}.model_hash IS NOT NULL AND {alias}.model_hash <> '' "
        f"THEN 'sha256:' || {alias}.model_hash "
        f"ELSE COALESCE({alias}.model_name, '') END)"
    )
