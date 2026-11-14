#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_public_safe.py —— 开源前 / 提交前的敏感信息自检。

用法(都在项目根目录跑):

    python -m tools.check_public_safe              # 工作区(已入库文件)
    python -m tools.check_public_safe --staged     # 只查本次暂存的内容(pre-commit 用)
    python -m tools.check_public_safe --history    # 追加全历史扫描
    python -m tools.check_public_safe --all        # 工作区 + 历史
    python -m tools.check_public_safe --strict     # 中危也算失败

退出码:0 = 通过;1 = 有问题(默认只在有高危时);2 = 不在 git 仓库里;
        3 = 扫描失败(git 调用出错,结果不可信)。

只报告**位置与类型**,不回显明文:命中的片段一律打码。

项目专属的禁词 / 放行规则放在 `.public-safe.local`(已被 .gitignore 排除),
每行一条,支持两种写法:

    ban: 某某大学              # 额外禁词
    allow: 某段正则            # 额外放行(命中的内容不再报)
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 2_000_000          # 超过就跳过(多半是模型 / 图片 / 数据库)
SELF = "tools/check_public_safe.py"

# ---- 规则表:(级别, 类别, 名称, 正则) --------------------------------------
RULES = [
    ("高", "密钥", "私钥文件头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("高", "密钥", "云厂商 AccessKey", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("高", "密钥", "GitHub Token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("高", "密钥", "OpenAI 风格 Key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("高", "密钥", "Slack Token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("高", "账户", "硬编码口令",
     re.compile(r"""(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key)\b\s*[:=]\s*['"][^'"\s]{4,}['"]""")),
    ("高", "连接串", "带凭据的数据库 URL",
     re.compile(r"(?i)\b(?:mongodb|postgres(?:ql)?|mysql|redis|amqp)://[^\s:/@]+:[^\s/@]+@")),

    ("中", "隐私", "中国大陆手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("中", "隐私", "身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("中", "隐私", "邮箱地址", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),

    ("中", "本机路径", "Windows 用户目录",
     re.compile(r"(?i)\b[A-Za-z]:[\\/]+Users[\\/]+(?!\.{2})[^\\/\s\"')]+")),
    ("中", "本机路径", "Unix 家目录", re.compile(r"(?<![\w/.])/(?:home|Users)/(?!\.{2})[A-Za-z0-9._-]+")),
    ("中", "本机路径", "Windows 应用数据目录", re.compile(r"(?i)\bAppData[\\/]+(?:Local|Roaming)\b")),

    ("中", "内网", "私有网段 IP",
     re.compile(r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b")),
    ("中", "内网", "内部域名",
     re.compile(r"(?i)(?<![.\w-])[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|corp|internal|lan|intranet)\b(?!\.)")),
]

# 邮箱里这些域名是公开占位,不必报
EMAIL_ALLOW = re.compile(
    r"(?i)@(?:users\.noreply\.github\.com|noreply\.github\.com|example\.(?:com|org|invalid))$")

LEVEL_ORDER = {"高": 0, "中": 1, "低": 2}


class ScanError(RuntimeError):
    """git 调用失败。安全门禁必须"失败关闭":空输出不能被当成"没命中"。"""


def sh(*args, **kw):
    """跑 git;返回码非 0 直接抛 ScanError。

    以前这里不看返回码:仓库读不到、对象损坏或 git 执行异常时 stdout 是空的,
    扫描结果会变成"未发现敏感内容" —— 公开前的门禁等于失效。
    """
    proc = subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().replace("\n", " ")[:200]
        raise ScanError(f"git {' '.join(args)} 失败(返回码 {proc.returncode}):{detail}")
    return proc


def mask(s: str) -> str:
    s = s.strip()
    if len(s) <= 6:
        return s[:1] + "***"
    return s[:3] + "***" + s[-2:]


def load_local_rules():
    """读取 .public-safe.local 里的 ban / allow 规则。"""
    bans, allows = [], []
    p = Path(".public-safe.local")
    if not p.exists():
        return bans, allows
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("ban:"):
            term = line[4:].strip()
            if term:
                bans.append((term, re.compile(re.escape(term))))
        elif line.startswith("allow:"):
            try:
                allows.append(re.compile(line[6:].strip()))
            except re.error:
                pass
    return bans, allows


def scan_text(path: str, text: str, bans, allows, hits):
    if any(a.search(text) for a in allows):
        return
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        for level, cat, name, rx in RULES:
            for m in rx.finditer(line):
                frag = m.group(0)
                if name == "邮箱地址" and EMAIL_ALLOW.search(frag):
                    continue
                hits.append((level, cat, name, path, idx, mask(frag)))
        for term, rx in bans:
            if rx.search(line):
                hits.append(("高", "禁词", term, path, idx, mask(term)))


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def scan_bytes(path, data, bans, allows, hits):
    if len(data) > MAX_BYTES or is_binary(data):
        return
    scan_text(path, data.decode("utf-8", "ignore"), bans, allows, hits)


# ---- 三种取内容的模式 --------------------------------------------------------
def iter_worktree():
    out = sh("ls-files", "-z").stdout
    for p in out.split("\0"):
        if not p or p == SELF:
            continue
        fp = Path(p)
        if fp.is_file():
            yield (p, fp.read_bytes())


def iter_staged():
    out = sh("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").stdout
    for p in out.split("\0"):
        if not p or p == SELF:
            continue
        fp = Path(p)
        if fp.is_file():
            yield (p, fp.read_bytes())


def iter_history():
    """遍历所有可达 blobs,按 sha 去重;每个 blob 只扫一次。

    内容用 Popen 流式读:一次性 capture_output 会把全部历史内容读进内存,
    历史长 / 大文件多时能吃掉几个 GB;流式读只保留当前这一个 blob。
    """
    listing = sh("rev-list", "--objects", "--all").stdout.splitlines()
    sha2path = {}
    for line in listing:
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[0] not in sha2path:
            sha2path[parts[0]] = parts[1]
    if not sha2path:
        return
    payload = "\n".join(sha2path) + "\n"

    chk = sh("cat-file", "--batch-check", input=payload)
    blobs = []
    for line in chk.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        try:
            size = int(parts[2])
        except ValueError:
            continue
        if size <= MAX_BYTES:
            blobs.append(parts[0])
    if not blobs:
        return

    payload = ("\n".join(blobs) + "\n").encode()
    # sha 列表走临时文件而不是管道:几百个 sha 就会超过管道缓冲,
    # 主线程"先写完再读"会和 git 互相等对方,直接死锁(实测 638 个对象必现)。
    with tempfile.TemporaryFile() as sha_file:
        sha_file.write(payload)
        sha_file.seek(0)
        proc = subprocess.Popen(["git", "cat-file", "--batch"],
                                stdin=sha_file, stdout=subprocess.PIPE)
        stdout = proc.stdout
        if stdout is None:
            raise ScanError("git cat-file 的管道没建起来")
        try:
            while True:
                header = stdout.readline()
                if not header:
                    break
                parts = header.decode("utf-8", "replace").split()
                if len(parts) != 3:
                    raise ScanError(f"cat-file 输出看不懂:{header[:80]!r}")
                sha, kind, size_text = parts
                try:
                    size = int(size_text)
                except ValueError:
                    raise ScanError(f"cat-file 长度字段不是数字:{size_text!r}") from None
                data = stdout.read(size)
                if len(data) != size:
                    raise ScanError(f"cat-file 读到一半断了(sha {sha[:7]})")
                stdout.read(1)                    # 每个对象后面跟着一个换行
                if kind != "blob":
                    continue
                yield (f"{sha2path.get(sha, sha[:8])} @ 历史 {sha[:7]}", data)
        finally:
            stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()


# ---- 仓库级检查 --------------------------------------------------------------
def check_repo_level(hits):
    ig = Path(".gitignore")
    text = ig.read_text(encoding="utf-8", errors="ignore") if ig.exists() else ""
    for pat in ("*-wal", "*-shm", "*-journal"):
        if pat not in text:
            hits.append(("低", "忽略规则", f".gitignore 缺 {pat}", ".gitignore", 0,
                         "SQLite 附属文件可能含完整数据页"))

    tracked = sh("ls-files", "-z").stdout.split("\0")
    for p in tracked:
        if not p:
            continue
        if re.search(r"\.(?:db-wal|db-shm|db-journal|sqlite3-wal)$", p):
            hits.append(("高", "误入库", "SQLite 附属文件已入库", p, 0, ""))
    for p in tracked:
        if not p:
            continue
        fp = Path(p)
        if fp.is_file() and fp.stat().st_size > 1_500_000:
            mb = fp.stat().st_size / 1024 / 1024
            hits.append(("低", "体积", "大文件入库", p, 0, f"{mb:.1f} MB"))


def main() -> int:
    ap = argparse.ArgumentParser(description="开源 / 提交前敏感信息自检")
    ap.add_argument("--staged", action="store_true", help="只查暂存内容(pre-commit)")
    ap.add_argument("--history", action="store_true", help="追加全历史扫描")
    ap.add_argument("--all", action="store_true", help="工作区 + 历史")
    ap.add_argument("--strict", action="store_true", help="中危也算失败")
    args = ap.parse_args()

    try:
        sh("rev-parse", "--is-inside-work-tree")
    except ScanError:
        print("不在 git 仓库里", file=sys.stderr)
        return 2

    bans, allows = load_local_rules()
    hits = []

    if args.staged:
        sources = [("暂存", iter_staged)]
    else:
        sources = [("工作区", iter_worktree)]
        if args.history or args.all:
            sources.append(("历史", iter_history))

    try:
        for label, gen in sources:
            n = 0
            for path, data in gen():
                scan_bytes(path, data, bans, allows, hits)
                n += 1
            print(f"已扫描 {label}:{n} 个文件")
        check_repo_level(hits)
    except ScanError as exc:
        print(f"\n✗ 扫描失败:{exc}", file=sys.stderr)
        print("  门禁失败关闭:这次没扫完,不能当作通过。", file=sys.stderr)
        return 3

    if not hits:
        print("\n✓ 未发现敏感内容")
        return 0

    hits.sort(key=lambda h: (LEVEL_ORDER.get(h[0], 9), h[1], h[3], h[4]))
    print()
    cur = None
    for level, cat, name, path, line, frag in hits:
        if level != cur:
            print(f"\n【{level}危】")
            cur = level
        loc = f"{path}:{line}" if line else path
        print(f"  · [{cat}] {name}  {loc}" + (f"  {frag}" if frag else ""))

    cnt = {lv: sum(1 for h in hits if h[0] == lv) for lv in ("高", "中", "低")}
    print(f"\n汇总:高危 {cnt['高']} / 中危 {cnt['中']} / 低危 {cnt['低']}")

    if cnt["高"] or (args.strict and cnt["中"]):
        print("→ 有需处理项")
        return 1
    print("→ 无高危项")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
