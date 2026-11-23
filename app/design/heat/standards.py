"""GB/T 28712.2 标准系列表的导入、校验、版本切换与缺表降级(L2 的入口数据)。

边界(方案 §8)
------------
* 标准原文的数值表受著作权保护:**代码与仓库都不带数据**,只带表头模板;
  用户从合法渠道取得标准后自己整理成 CSV 导入。本模块只负责"读进来 + 挡住坏表"。
* 版本化目录 + 原子指针,与 `app/design/pipe/data.py` 同一套:
    `data/preview/hx-std/docx/`            老位置(手工放文件时的兜底)
    `data/preview/hx-std/versions/<时间戳>/` 每次导入一个完整目录
    `data/preview/hx-std/active.txt`       当前生效的是哪个版本
  导入时先在 versions/ 下拼一份**完整**目录、试加载通过,再改指针;失败就删掉
  staging 或回退指针,不会出现"新的系列表配旧的程数组合"。
* 缺表降级必须显式:状态 `missing` / `invalid` / `partial` 时 L2 不给标准行,
  调用方据此打 `std_skipped=true`,只出 `A_req` 与经验推荐范围。**L1 永不受影响**。
* 本模块可以读盘(`data/` 与 `samples/`),但不 import `app.aspen`,也不写库 ——
  计算层保持离线可测。

校验口径(方案 §8.3)
------------------
1. 表头齐全(缺列直接拒)、UTF-8、逗号分隔(兼容 Excel 导出的 ; / Tab);
2. 数值范围:DN 150~2000、管外径 10~57、壁厚 1~5、管长 1000~9000、
   管心距 ∈ [1.20, 1.70]×管外径、管程数 ∈ {1,2,4,6}、管数 1~5000、面积 > 0;
3. 几何自洽:按标准 §5.7 的 A1 口径
   `A1 = n·π·d_o·(L − 2δ − 2L1)·10⁻⁶`(δ = 管板厚 50 / 75 mm,L1 = 伸出 3 mm),
   与表里 `area_m2` 的相对差 ≤ 3%;
   **不能用 n·π·d_o·L** —— 短管小直径时端部修正能占到 6% 以上,真表会被误判;
4. 唯一键 `(DN, od, pitch, passes, L)` 不得重复;
5. 完整性:有效行 ≥ 20、DN 档 ≥ 3、管径档 ≥ 2,否则不能算 `ok`;
6. 同一 `(DN, od, pitch, passes)` 下面积随管长递增 —— 违反给 warning,不拒。
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from app import paths

# ---------------------------------------------------------------- 文件规格

SERIES_FILE = "hx-28712-2.csv"
PASSES_FILE = "hx-pass-combinations.csv"

#: 导入白名单。`columns` 是**必填**列;`optional` 是可空列。
STD_FILES: tuple[dict, ...] = (
    {
        "name": SERIES_FILE,
        "label": "标准系列行(GB/T 28712.2)",
        "from": "GB/T 28712.2-2023 表 5(φ19)/ 表 7(φ25)",
        "columns": [
            "DN",
            "shell_id_mm",
            "tube_od_mm",
            "tube_wall_mm",
            "tube_length_mm",
            "tube_pitch_mm",
            "tube_passes",
            "tube_count",
            "area_m2",
            "arrangement",
        ],
        "optional": ["shell_od_mm", "pn_bar", "standard", "page", "note"],
        "need": 20,
    },
    {
        "name": PASSES_FILE,
        "label": "壳程/管程组合(可选)",
        "from": "方案 §8.2 内置 4 行;导入后以文件为准",
        "columns": ["shell_passes", "tube_passes", "need_f_correction", "allowed"],
        "optional": ["note"],
        "need": 2,
    },
)

#: 只含表头 + 2 行**故意不自洽**的示例(面积写 999),随仓库分发。
TEMPLATE_DIR = paths.resource_root() / "samples" / "hx-standards-template"

#: 数据目录(全部在 `data/` 下,已被 .gitignore 覆盖)
_ROOT = paths.data_dir("preview", "hx-std")
_LEGACY_DIR = _ROOT / "docx"
_VERSIONS_DIR = _ROOT / "versions"
_POINTER_PATH = _ROOT / "active.txt"

_LOCK = threading.RLock()

#: 完整性门槛(§8.3.5)
MIN_ROWS = 20
MIN_DN_TIERS = 3
MIN_OD_TIERS = 2
#: 单文件上限。标准表两张加起来不到 200 KB,8 MB 足够。
MAX_FILE_BYTES = 8 * 1024 * 1024

#: 数值范围(§8.3.2)
_RANGE = {
    "DN": (150.0, 2000.0),
    "tube_od_mm": (10.0, 57.0),
    "tube_wall_mm": (1.0, 5.0),
    "tube_length_mm": (1000.0, 9000.0),
    "tube_count": (1.0, 5000.0),
}
_PITCH_RATIO = (1.20, 1.70)
_ALLOWED_PASSES = (1, 2, 4, 6)
_ARRANGEMENTS = ("triangular", "rotated_triangular", "square", "rotated_square")
#: 标准 §5.7 的面积口径参数:管板厚 δ、换热管伸出管板长度 L1
_TUBE_SHEET_MM = {True: 50.0, False: 75.0}  # DN<=2400 → 50;DN>2400 → 75
_TUBE_EXTEND_MM = 3.0
_AREA_TOL = 0.03
_DELIMITERS = (",", ";", "\t")
_MISSING_TOKENS = {"", "-", "—", "–", "--", "－"}

STATE_TEXT = {
    "ok": "已加载标准系列表",
    "partial": "标准表可用,但部分行被拒或有告警",
    "missing": "未加载标准系列表",
    "invalid": "标准表读不进来(格式/表头问题)",
}


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class SeriesRow:
    """一条标准系列行(单位 mm / m² / bar)。"""

    dn_mm: int
    shell_id_mm: float
    tube_od_mm: float
    tube_wall_mm: float
    tube_length_mm: int
    tube_pitch_mm: float
    tube_passes: int
    tube_count: int
    area_m2: float
    arrangement: str = "triangular"
    shell_od_mm: float | None = None
    pn_bar: float | None = None
    standard: str = ""
    page: str = ""
    note: str = ""

    @property
    def key(self) -> str:
        head = f"{self.dn_mm}|{self.tube_od_mm:g}|{self.tube_pitch_mm:g}"
        return f"{head}|{self.tube_passes}|{self.tube_length_mm}"

    @property
    def tube_id_mm(self) -> float:
        return self.tube_od_mm - 2 * self.tube_wall_mm

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "dn_mm": self.dn_mm,
            "shell_id_mm": self.shell_id_mm,
            "shell_od_mm": self.shell_od_mm,
            "tube_od_mm": self.tube_od_mm,
            "tube_wall_mm": self.tube_wall_mm,
            "tube_id_mm": round(self.tube_id_mm, 3),
            "tube_length_mm": self.tube_length_mm,
            "tube_pitch_mm": self.tube_pitch_mm,
            "tube_passes": self.tube_passes,
            "tube_count": self.tube_count,
            "area_m2": self.area_m2,
            "arrangement": self.arrangement,
            "pn_bar": self.pn_bar,
            "standard": self.standard,
            "page": self.page,
            "note": self.note,
        }


@dataclass(frozen=True)
class PassCombo:
    shell_passes: int
    tube_passes: int
    need_f_correction: int
    allowed: int
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "shell_passes": self.shell_passes,
            "tube_passes": self.tube_passes,
            "need_f_correction": self.need_f_correction,
            "allowed": self.allowed,
            "note": self.note,
        }


@dataclass(frozen=True)
class Snapshot:
    """计算侧只认这个快照,不零散地读模块变量。"""

    rows: tuple[SeriesRow, ...] = ()
    passes: tuple[PassCombo, ...] = ()
    state: str = "missing"
    reason: str = ""
    source: str = ""
    version: str = ""
    standards: tuple[str, ...] = ()
    detail: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """能不能拿来做标准行枚举(§8.5:missing / invalid 时只算 A_req)。"""
        return self.state in ("ok", "partial") and bool(self.rows)

    @property
    def skipped(self) -> bool:
        return not self.usable


# ---------------------------------------------------------------- 读 CSV


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _csv_rows(text) -> list[dict]:
    """读 CSV:兼容 BOM、Excel 的 ; / Tab、「sep=;」首行、表头前后空格。"""
    raw = str(text or "").lstrip("\ufeff")
    lines = raw.splitlines()
    delimiter = ","
    if lines:
        head = lines[0].strip().lower()
        if head.startswith("sep="):
            candidate = lines[0].split("=", 1)[1].strip()[:1]
            if candidate in _DELIMITERS:
                delimiter = candidate
            lines = lines[1:]
        else:
            for cand in _DELIMITERS:
                if cand in lines[0]:
                    delimiter = cand
                    break
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    out = []
    for row in reader:
        clean = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
        if any(clean.values()):
            out.append(clean)
    return out


def _num(raw) -> float | None:
    text = str(raw or "").strip()
    if text in _MISSING_TOKENS:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _int(raw) -> int | None:
    value = _num(raw)
    if value is None or abs(value - round(value)) > 1e-6:
        return None
    return int(round(value))


def _expected_area_m2(row: SeriesRow) -> float:
    """标准 §5.7 的计算换热面积 A1(不是 n·π·d_o·L)。"""
    sheet = _TUBE_SHEET_MM[row.dn_mm <= 2400]
    leff = row.tube_length_mm - 2 * sheet - 2 * _TUBE_EXTEND_MM
    if leff <= 0:
        return 0.0
    return row.tube_count * math.pi * row.tube_od_mm * leff * 1e-6


def _series_from_row(raw: dict, index: int) -> tuple[SeriesRow | None, list[str]]:
    """一行字典 → SeriesRow;不合法返回 (None, 错误列表)。"""
    errors: list[str] = []
    values = {}
    for col in STD_FILES[0]["columns"]:
        if col == "arrangement":
            continue
        values[col] = _num(raw.get(col))
        if values[col] is None:
            errors.append(f"第 {index} 行:{col} 读不出({str(raw.get(col)).strip()!r})")
    arrangement = str(raw.get("arrangement") or "").strip().lower() or "triangular"
    if arrangement not in _ARRANGEMENTS:
        errors.append(f"第 {index} 行:arrangement={arrangement!r} 不在 {'/'.join(_ARRANGEMENTS)}")
    if errors:
        return None, errors

    dn = values["DN"]
    if not _RANGE["DN"][0] <= dn <= _RANGE["DN"][1]:
        errors.append(f"第 {index} 行:DN {dn:g} 超出 150~2000,疑似串列")
    for key in ("tube_od_mm", "tube_wall_mm", "tube_length_mm", "tube_count"):
        lo, hi = _RANGE[key]
        if not lo <= values[key] <= hi:
            errors.append(f"第 {index} 行:{key} {values[key]:g} 超出 {lo:g}~{hi:g}")
    if values["shell_id_mm"] <= 0:
        errors.append(f"第 {index} 行:shell_id_mm 必须为正")
    pitch = values["tube_pitch_mm"]
    od = values["tube_od_mm"]
    if not _PITCH_RATIO[0] * od <= pitch <= _PITCH_RATIO[1] * od:
        lo, hi = _PITCH_RATIO
        errors.append(f"第 {index} 行:管心距 {pitch:g} 不在 [{lo:g}, {hi:g}]×外径({od:g})")
    passes = _int(raw.get("tube_passes"))
    if passes not in _ALLOWED_PASSES:
        errors.append(f"第 {index} 行:管程数 {raw.get('tube_passes')!r} 不在 1/2/4/6")
    if values["area_m2"] <= 0:
        errors.append(f"第 {index} 行:area_m2 必须为正")
    if errors:
        return None, errors

    row = SeriesRow(
        dn_mm=int(round(dn)),
        shell_id_mm=values["shell_id_mm"],
        tube_od_mm=od,
        tube_wall_mm=values["tube_wall_mm"],
        tube_length_mm=int(round(values["tube_length_mm"])),
        tube_pitch_mm=pitch,
        tube_passes=int(passes),
        tube_count=int(round(values["tube_count"])),
        area_m2=values["area_m2"],
        arrangement=arrangement,
        shell_od_mm=_num(raw.get("shell_od_mm")),
        pn_bar=_num(raw.get("pn_bar")),
        standard=str(raw.get("standard") or "").strip(),
        page=str(raw.get("page") or "").strip(),
        note=str(raw.get("note") or "").strip(),
    )
    expected = _expected_area_m2(row)
    if expected <= 0:
        errors.append(
            f"第 {index} 行:管长 {row.tube_length_mm} mm 扣掉管板与伸出长度后没有有效长度"
        )
        return None, errors
    if abs(row.area_m2 - expected) / row.area_m2 > _AREA_TOL:
        errors.append(
            f"第 {index} 行:面积 {row.area_m2:g} m² 与几何算出的 {expected:.3g} m² 差 "
            f"{abs(row.area_m2 - expected) / row.area_m2 * 100:.1f}%(>3%),疑似串列"
        )
        return None, errors
    return row, []


def parse_series(text) -> tuple[list[SeriesRow], list[str], list[str], dict]:
    """校验并解析系列表文本 → (行, 错误, 告警, 统计)。"""
    if not isinstance(text, str) or not text.strip():
        return [], ["文件是空的"], [], {}
    if "\ufffd" in text:
        return [], ["里面有乱码:多半不是 UTF-8。用 Excel「另存为 → CSV UTF-8」再试"], [], {}
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        return [], [f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB,不像标准表"], [], {}
    raw_rows = _csv_rows(text)
    if not raw_rows:
        return [], ["只有表头、没有数据行"], [], {}
    headers = list(raw_rows[0].keys())
    spec = STD_FILES[0]
    missing = [c for c in spec["columns"] if c not in headers]
    if missing:
        return [], [f"缺列 {', '.join(missing)};现在的表头是:{', '.join(headers[:10])}"], [], {}

    rows: list[SeriesRow] = []
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_rows, start=2):
        row, errs = _series_from_row(raw, i)
        if errs:
            errors.extend(errs)
            continue
        if row.key in seen:
            errors.append(f"第 {i} 行:唯一键 {row.key} 重复(DN/外径/管心距/管程/管长 不能重)")
            continue
        seen.add(row.key)
        rows.append(row)

    # 软告警:同一 (DN, od, pitch, passes) 下面积应随管长递增(可能是分段表,不拒)
    by_geom: dict[tuple, list[SeriesRow]] = {}
    for row in rows:
        by_geom.setdefault(
            (row.dn_mm, row.tube_od_mm, row.tube_pitch_mm, row.tube_passes), []
        ).append(row)
    for geom, group in by_geom.items():
        ordered = sorted(group, key=lambda r: r.tube_length_mm)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if b.area_m2 < a.area_m2 - 1e-9:
                warnings.append(
                    f"DN{geom[0]} φ{geom[1]:g} {geom[3]} 管程:管长 "
                    f"{a.tube_length_mm}→{b.tube_length_mm} mm,"
                    f"面积 {a.area_m2:g}→{b.area_m2:g} 反而下降,建议复核"
                )
                break

    stats = {
        "rows": len(raw_rows),
        "valid": len(rows),
        "rejected": len(raw_rows) - len(rows),
        "dn_tiers": len({r.dn_mm for r in rows}),
        "od_tiers": len({r.tube_od_mm for r in rows}),
        "lengths": sorted({r.tube_length_mm for r in rows}),
        "standards": sorted({r.standard for r in rows if r.standard}),
    }
    return rows, errors[:15], warnings[:10], stats


def parse_passes(text) -> tuple[list[PassCombo], list[str], dict]:
    """校验程数组合表(可选文件)。"""
    if not isinstance(text, str) or not text.strip():
        return [], ["文件是空的"], {}
    raw_rows = _csv_rows(text)
    if not raw_rows:
        return [], ["只有表头、没有数据行"], {}
    spec = STD_FILES[1]
    headers = list(raw_rows[0].keys())
    missing = [c for c in spec["columns"] if c not in headers]
    if missing:
        return [], [f"缺列 {', '.join(missing)};现在的表头是:{', '.join(headers[:8])}"], {}

    rows: list[PassCombo] = []
    errors: list[str] = []
    seen: set[tuple[int, int]] = set()
    for i, raw in enumerate(raw_rows, start=2):
        shell = _int(raw.get("shell_passes"))
        tubes = _int(raw.get("tube_passes"))
        need_f = _int(raw.get("need_f_correction"))
        allowed = _int(raw.get("allowed"))
        if shell not in (1, 2):
            errors.append(f"第 {i} 行:shell_passes 只能 1 或 2")
            continue
        if tubes not in _ALLOWED_PASSES:
            errors.append(f"第 {i} 行:tube_passes 不在 1/2/4/6")
            continue
        if need_f not in (0, 1) or allowed not in (0, 1):
            errors.append(f"第 {i} 行:need_f_correction / allowed 只能是 0 或 1")
            continue
        if (shell, tubes) in seen:
            errors.append(f"第 {i} 行:组合 {shell}/{tubes} 重复")
            continue
        seen.add((shell, tubes))
        rows.append(
            PassCombo(
                shell_passes=shell,
                tube_passes=tubes,
                need_f_correction=need_f,
                allowed=allowed,
                note=str(raw.get("note") or "").strip(),
            )
        )
    return rows, errors[:10], {"rows": len(raw_rows), "valid": len(rows)}


# ---------------------------------------------------------------- 目录与指针


def _read_pointer() -> str:
    try:
        return _POINTER_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_pointer(name: str) -> None:
    """原子切换版本:写临时文件再 os.replace(同目录、同盘)。"""
    _ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _POINTER_PATH.with_name(_POINTER_PATH.name + ".tmp")
    tmp.write_text(name + "\n", encoding="utf-8")
    os.replace(tmp, _POINTER_PATH)


def resolve_active_dir() -> Path:
    """当前生效目录:有指针用版本目录,否则退回 docx/ 老位置。"""
    name = _read_pointer()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return _LEGACY_DIR
    candidate = _VERSIONS_DIR / name
    return candidate if candidate.is_dir() else _LEGACY_DIR


def _dir_label(base: Path) -> str:
    root = paths.app_root()
    try:
        return base.relative_to(root).as_posix()
    except ValueError:
        return str(base)


def _state_of(rows: list[SeriesRow], errors: list[str], series_text: str) -> tuple[str, str]:
    """按有效行 / DN 档 / 管径档判状态(§8.3.5、§8.5)。"""
    if not series_text:
        return "missing", f"未找到 {SERIES_FILE}"
    if not rows:
        return "invalid", "; ".join(errors[:3]) or "一行也没读出来"
    dn = len({r.dn_mm for r in rows})
    od = len({r.tube_od_mm for r in rows})
    if len(rows) < MIN_ROWS:
        return "missing", f"有效行只有 {len(rows)} 行(至少 {MIN_ROWS} 行),按缺表处理"
    if od < MIN_OD_TIERS:
        return "partial", f"只有 {od} 种管径(建议 ≥{MIN_OD_TIERS});只给了这一种管径的行"
    if dn < MIN_DN_TIERS:
        return "partial", f"只有 {dn} 档 DN(建议 ≥{MIN_DN_TIERS})"
    if errors:
        return "partial", f"{len(errors)} 行被拒,其余 {len(rows)} 行可用"
    return "ok", ""


def load(dir_: Path | str | None = None) -> Snapshot:
    """读一个目录下的两份 CSV,产出快照(不做缓存)。"""
    base = Path(dir_) if dir_ is not None else resolve_active_dir()
    series_path = base / SERIES_FILE
    series_text = _read_text(series_path) if series_path.is_file() else ""

    rows: list[SeriesRow] = []
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict = {}
    if series_text:
        rows, errors, warnings, stats = parse_series(series_text)

    passes: list[PassCombo] = []
    pass_errors: list[str] = []
    pass_stats: dict = {}
    pass_path = base / PASSES_FILE
    pass_text = _read_text(pass_path) if pass_path.is_file() else ""
    if pass_text:
        passes, pass_errors, pass_stats = parse_passes(pass_text)
        if pass_errors:
            warnings.extend(f"{PASSES_FILE}:{e}" for e in pass_errors)

    state, reason = _state_of(rows, errors, series_text)
    # 文件里的组合优先;文件坏了就回退内置(不让一个坏文件把选型打挂)
    details = tuple(passes) if passes else ()
    return Snapshot(
        rows=tuple(rows) if state in ("ok", "partial") else (),
        passes=details,
        state=state,
        reason=reason,
        source=_dir_label(base),
        version=_read_pointer() or ("legacy" if base == _LEGACY_DIR else ""),
        standards=tuple(stats.get("standards") or []),
        detail={
            "series": {**stats, "errors": errors[:10], "warnings": warnings[:10]},
            "passes": {**pass_stats, "errors": pass_errors[:5], "from_file": bool(passes)},
        },
    )


_CURRENT: Snapshot | None = None


def snapshot() -> Snapshot:
    """当前生效的快照(进程内缓存;首次调用时才读盘,导入/重载后才变)。"""
    global _CURRENT
    with _LOCK:
        if _CURRENT is None:
            _CURRENT = load()
        return _CURRENT


def reload(dir_: Path | str | None = None) -> Snapshot:
    """重新读并一次性替换快照。读坏了保留旧快照(不把页面打空)。"""
    global _CURRENT
    snap = load(dir_)
    with _LOCK:
        _CURRENT = snap
    return snap


def standard_state() -> dict:
    """下发给页面的状态块(§4.3 的 `std_state`)。"""
    snap = snapshot()
    return {
        "state": snap.state,
        "reason": snap.reason or STATE_TEXT.get(snap.state, ""),
        "label": STATE_TEXT.get(snap.state, snap.state),
        "rows": len(snap.rows),
        "skipped": snap.skipped,
        "source": snap.source,
        "version": snap.version,
        "standards": list(snap.standards),
    }


# ---------------------------------------------------------------- 状态与模板


def standards_status(dir_: Path | str | None = None) -> dict:
    """两份 CSV 有没有、多少行、什么时候放进来的(只看文件,不看加载结果)。"""
    base = Path(dir_) if dir_ is not None else resolve_active_dir()
    files = []
    for spec in STD_FILES:
        path = base / spec["name"]
        item = {
            "name": spec["name"],
            "label": spec["label"],
            "from": spec["from"],
            "columns": list(spec["columns"]),
            "optional": list(spec.get("optional") or []),
            "exists": False,
            "rows": 0,
            "bytes": 0,
            "mtime": None,
        }
        if path.is_file():
            st = path.stat()
            item.update(
                {
                    "exists": True,
                    "rows": len(_csv_rows(_read_text(path))),
                    "bytes": st.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                }
            )
        files.append(item)
    snap = snapshot()
    return {
        "dir": _dir_label(base),
        "files": files,
        "state": standard_state(),
        "loaded_rows": len(snap.rows),
        "version": snap.version,
    }


def _norm_text(text) -> str:
    raw = str(text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in raw.split("\n")).strip()


def template_texts() -> dict[str, str]:
    """两份模板的归一化文本;读不到的份不出现(不回退、不造)。"""
    out: dict[str, str] = {}
    for spec in STD_FILES:
        path = TEMPLATE_DIR / spec["name"]
        try:
            out[spec["name"]] = _norm_text(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
    return out


def is_template_text(text) -> bool:
    """是不是原封不动的示例模板(模板数值是编的,不能当数据导入)。"""
    norm = _norm_text(text)
    if not norm:
        return False
    return any(norm == t for t in template_texts().values())


def template_preview() -> dict:
    """提示词里用的示例:表头 + 示例行 + 导入门槛。"""
    files = []
    for spec in STD_FILES:
        path = TEMPLATE_DIR / spec["name"]
        rows = _csv_rows(_read_text(path))
        headers = list(rows[0].keys()) if rows else list(spec["columns"])
        files.append(
            {
                "name": spec["name"],
                "label": spec["label"],
                "from": spec["from"],
                "columns": list(spec["columns"]),
                "need": spec["need"],
                "headers": headers,
                "rows": [[str(r.get(h) or "") for h in headers] for r in rows],
            }
        )
    return {
        "dir": "samples/hx-standards-template",
        "exists": all((TEMPLATE_DIR / s["name"]).is_file() for s in STD_FILES),
        "files": files,
        "note": (
            "示例数值为虚构,仅示列名与写法;面积一列是故意写错的 999,不能原样导入。"
            "系列表至少 20 个有效行、≥3 档 DN、≥2 种管径才算 ok;"
            "模板原样导入会被拒绝。"
        ),
    }


def template_zip() -> bytes:
    """把模板目录打成 zip(表头 + 2 行示例 + README)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for spec in STD_FILES:
            path = TEMPLATE_DIR / spec["name"]
            if path.is_file():
                zf.writestr(spec["name"], path.read_bytes())
        note = TEMPLATE_DIR / "README.md"
        if note.is_file():
            zf.writestr("README.md", note.read_bytes())
    return buf.getvalue()


# ---------------------------------------------------------------- 导入


def _ground(status: dict) -> dict:
    """给导入结果补 warnings 位置(与管道页同一形状:错误/告警分开出)。"""
    status.setdefault("warnings", [])
    return status


def import_standards(
    payload, *, target_dir: Path | str | None = None, reload_now: bool = True
) -> tuple[bool, list[str], dict]:
    """把页面选中的 CSV 写进标准表目录并切换版本。

    `payload` 形如 `{"files": {"hx-28712-2.csv": "<CSV 文本>"}}`;
    返回 `(是否写入, 错误列表, 当前文件状态)`。任一文件不合格就整单不写。
    `target_dir` / `reload_now` 只给自测用:导入临时目录、不碰运行中的快照。
    """
    data = payload if isinstance(payload, dict) else {}
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        return False, ["没有收到文件:先选要导入的 CSV 再点导入"], standards_status()

    errors: list[str] = []
    warnings: list[str] = []
    accepted: dict[str, str] = {}
    for name, text in files.items():
        spec = next((s for s in STD_FILES if s["name"] == str(name)), None)
        if spec is None:
            errors.append(
                f"不认识的文件名 {name!r};只收 " + "、".join(s["name"] for s in STD_FILES)
            )
            continue
        if is_template_text(text):
            errors.append(
                f"{name}:这是随项目分发的格式模板(面积 999 是编的),不是标准原文 —— "
                "按模板列名填入真实数据后再导入"
            )
            continue
        if spec["name"] == SERIES_FILE:
            rows, errs, warns, _stats = parse_series(text)
            if errs:
                errors.extend(f"{name}:{e}" for e in errs)
                continue
            if len(rows) < MIN_ROWS:
                errors.append(
                    f"{name}:有效行只有 {len(rows)} 行(至少 {MIN_ROWS} 行);"
                    "少于一页的表不足以做标准规格枚举"
                )
                continue
            warnings.extend(f"{name}:{w}" for w in warns)
        else:
            _rows, errs, _stats = parse_passes(text)
            if errs:
                errors.extend(f"{name}:{e}" for e in errs)
                continue
        accepted[spec["name"]] = text

    if errors:
        return False, errors, _ground(standards_status())

    if target_dir is not None:
        ok, errs, status = _import_into_dir(Path(target_dir), accepted, reload_now=reload_now)
        status["warnings"] = warnings
        return ok, errs, status

    # 正式路径:先在 versions/ 下拼一份**完整**目录,试加载通过再原子切指针
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = _VERSIONS_DIR / stamp
    seq = 1
    while staging.exists():
        seq += 1
        staging = _VERSIONS_DIR / f"{stamp}-{seq}"
    previous = _read_pointer()
    try:
        staging.mkdir(parents=True)
        _fill_staging(staging, resolve_active_dir(), accepted)
        check_errors = _check_staging(staging, accepted)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return False, [f"写入失败:{exc}"], _ground(standards_status())
    if check_errors:
        shutil.rmtree(staging, ignore_errors=True)
        return False, check_errors, _ground(standards_status())

    try:
        _write_pointer(staging.name)
        if reload_now:
            snap = reload()
            if snap.state in ("invalid", "missing"):
                raise RuntimeError(snap.reason or "导入后仍是缺表状态")
    except Exception as exc:
        try:
            if previous:
                _write_pointer(previous)
            else:
                _POINTER_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        if reload_now:
            reload()
        shutil.rmtree(staging, ignore_errors=True)
        return False, [f"导入后加载失败,已回退:{exc}"], _ground(standards_status())

    status = standards_status()
    status["warnings"] = warnings
    return True, [], status


def _fill_staging(staging: Path, base: Path, accepted: dict[str, str]) -> None:
    """拼出"导入后长什么样":给了新内容的用新的,没给的从当前版本复制。"""
    for spec in STD_FILES:
        name = spec["name"]
        if name in accepted:
            (staging / name).write_text(accepted[name].lstrip("\ufeff"), encoding="utf-8-sig")
        elif (base / name).is_file():
            shutil.copy2(base / name, staging / name)


def _check_staging(staging: Path, accepted: dict[str, str]) -> list[str]:
    """照真实逻辑把 staging 试加载一遍;返回错误列表(空 = 通过)。"""
    errors: list[str] = []
    series_text = _read_text(staging / SERIES_FILE)
    rows, errs, _warns, _stats = parse_series(series_text)
    if SERIES_FILE in accepted and (not rows or errs):
        errors.append("系列表试加载失败:" + "; ".join(errs[:3] or ["一行也没读出来"]))
    if SERIES_FILE in accepted and len(rows) < MIN_ROWS:
        errors.append(f"系列表只有 {len(rows)} 行有效数据(至少 {MIN_ROWS} 行)")
    pass_text = _read_text(staging / PASSES_FILE)
    if pass_text:
        _rows, perr, _pstats = parse_passes(pass_text)
        if perr:
            errors.append("程数组合表试加载失败:" + "; ".join(perr[:3]))
    return errors


def _import_into_dir(
    base: Path, accepted: dict[str, str], *, reload_now: bool
) -> tuple[bool, list[str], dict]:
    """自测路径:直接写进指定目录(不建版本、不动指针)。"""
    staging = base.parent / f".hx-std-{os.getpid()}-{time.time_ns()}"
    try:
        base.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        try:
            _fill_staging(staging, base, accepted)
            errors = _check_staging(staging, accepted)
            if errors:
                return False, errors, standards_status(base)
            for name in accepted:
                (staging / name).replace(base / name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except OSError as exc:
        return False, [f"写入失败:{exc}"], standards_status(base)
    if reload_now:
        reload(base)
    return True, [], standards_status(base)


# ---------------------------------------------------------------- 选取与降级


def match_rows(
    dn_range: tuple[float, float] | None = None,
    tube_od_mm: float | None = None,
    tube_passes: int | None = None,
    tube_length_mm: int | None = None,
    snap: Snapshot | None = None,
) -> list[SeriesRow]:
    """按条件筛标准行;条件为 None 表示不限。"""
    source = snap or snapshot()
    out = []
    for row in source.rows:
        if dn_range and not dn_range[0] <= row.shell_id_mm <= dn_range[1]:
            continue
        if tube_od_mm is not None and abs(row.tube_od_mm - tube_od_mm) > 1e-9:
            continue
        if tube_passes is not None and row.tube_passes != tube_passes:
            continue
        if tube_length_mm is not None and row.tube_length_mm != tube_length_mm:
            continue
        out.append(row)
    return out


def find_row(key: str, snap: Snapshot | None = None) -> SeriesRow | None:
    """按 `std_row_key` 找回一条标准行(人工覆盖/导出回读用)。"""
    source = snap or snapshot()
    return next((r for r in source.rows if r.key == key), None)


def estimate_ranges(area_req_m2: float | None, snap: Snapshot | None = None) -> dict:
    """缺表时给"经验推荐范围"(§8.5)。**不是标准值,不进编码、不进台账。**

    返回:
        {"skipped": True, "note": "...", "cases": [{od, length, count_min, count_max}],
         "shell_mm": {...}}
    管数区间 = [ceil(A/(π·d_o·L_max)), ceil(A/(π·d_o·L_min))],d_o 取 19 / 25 各算一遍。
    """
    from . import data as hx_data

    if area_req_m2 is None or area_req_m2 <= 0:
        return {"skipped": True, "note": "没有需求面积,给不出推荐范围", "cases": [], "shell_mm": {}}
    lengths = hx_data.ESTIMATE_LENGTHS_MM
    cases = []
    for od in hx_data.ESTIMATE_TUBE_ODS_MM:
        lo = math.ceil(area_req_m2 / (math.pi * (od / 1000.0) * (max(lengths) / 1000.0)))
        hi = math.ceil(area_req_m2 / (math.pi * (od / 1000.0) * (min(lengths) / 1000.0)))
        cases.append(
            {
                "tube_od_mm": od,
                "lengths_mm": list(lengths),
                "count_min": max(lo, 1),
                "count_max": max(hi, 1),
            }
        )
    # 壳体直径:按 Kern 管束直径关联式估(只用于"大概多大")
    shells = {}
    for case in cases:
        n = (case["count_min"] + case["count_max"]) / 2
        shells[f"{case['tube_od_mm']:g}"] = round(bundle_diameter_mm(n, case["tube_od_mm"]), 0)
    return {
        "skipped": True,
        "note": "未加载标准系列表,跳过标准规格圆整;以下为理论需求面积与经验推荐范围",
        "cases": cases,
        "shell_mm": shells,
    }


#: Kern 管束直径关联式常数 (K1, n1);只取正三角形/正方形、1~4 管程
_KERN_BUNDLE = {
    ("triangular", 1): (0.319, 2.142),
    ("triangular", 2): (0.249, 2.207),
    ("triangular", 4): (0.175, 2.285),
    ("square", 1): (0.215, 2.207),
    ("square", 2): (0.156, 2.291),
    ("square", 4): (0.040, 2.617),
}


def bundle_diameter_mm(
    tube_count: float, tube_od_mm: float, arrangement: str = "triangular", passes: int = 1
) -> float:
    """Kern 管束外径估算 D_otl = d_o·(N/K1)^(1/n1)。**经验估算,非标准值**。"""
    k1, n1 = _KERN_BUNDLE.get((arrangement, passes), _KERN_BUNDLE[("triangular", 1)])
    if tube_count <= 0 or tube_od_mm <= 0:
        return 0.0
    return tube_od_mm * (tube_count / k1) ** (1.0 / n1)


def dump_snapshot(snap: Snapshot | None = None) -> dict:
    """调试/自测用:把快照摊平成 JSON 友好的 dict。"""
    source = snap or snapshot()
    return {
        "state": source.state,
        "reason": source.reason,
        "source": source.source,
        "version": source.version,
        "standards": list(source.standards),
        "rows": [r.as_dict() for r in source.rows],
        "passes": [p.as_dict() for p in source.passes],
        "detail": json.loads(json.dumps(source.detail, ensure_ascii=False)),
    }


def with_rows(snap: Snapshot, rows: list[SeriesRow]) -> Snapshot:
    """自测里造一个"已有行"的快照,不碰磁盘。"""
    return replace(snap, rows=tuple(rows))
