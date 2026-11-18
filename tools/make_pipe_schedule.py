"""生成《管道选型一览表》(.docx),并按需打印校核审计 —— 不用 Aspen、不用起服务。

    python -m tools.make_pipe_schedule --case 55
    python -m tools.make_pipe_schedule --case 55 --audit
    python -m tools.make_pipe_schedule --case 55 --out data/preview/xxx.docx

为什么独立成一个脚本
--------------------
网页「管道」页导出走的是同一套 `pipe_schedule.build_pipe_docx`,本脚本只是从命令行
复现那条路径:读库 → 选型 → 套版渲染。口径只有一份(`app/design/pipe/calc.py`),
改规则两边一起变,不存在"脚本算出一套、界面算出另一套"。

`--audit` 把 2026-11-18 那批工程校核逐项打出来(真空 V1E、外压失稳、高压升 R1E、
非优先 DN、汽蚀限速、分层流、零流量、非标编号、保温 -H),便于人眼复核或贴进记录。
标准表没录全时明确报 `TablesNotReady`,不造一张空表。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.design import pipe as pipe_calc
from app.store import db


def _audit(res: dict, case: dict | None = None) -> list[str]:
    """把选型结果按 §17/§17.8 的校核项分组打印,只陈述事实。"""
    rows = res["rows"]
    out: list[str] = []
    s = res["summary"]

    def names(pred) -> str:
        hit = [str(r["stream"]) for r in rows if pred(r)]
        return "、".join(hit) if hit else "(无)"

    if case is not None:
        sev = str(case.get("severity") or "").strip().lower()
        if sev in ("warning", "error"):
            out.append(
                f"[收敛状态] severity={sev} —— 表头已加风险提示:"
                "物性数据仅供参考,正式选型前需消除收敛告警"
            )
    out.append(
        f"流股 {s['streams']} 条:选型 {s['selected']},未选型 {s['missing']}"
        f"(其中零流量 {s['zero_flow']});告警行 {s['warned']}。"
    )
    out.append("")
    out.append(
        "[真空 V1E + 外压失稳] 绝压 < 95.0 kPa(表压 < −6.3 kPa)共 "
        f"{s['vacuum']} 条: {names(lambda r: r['vacuum'])}"
    )
    out.append(
        "[高真空 ≤10 kPa] 共 "
        f"{s['deep_vacuum']} 条: {names(lambda r: r['deep_vacuum'])}"
        " —— 严控吸入压降防闪蒸,全焊透/高真空法兰防漏气"
    )
    deep_hot = [r for r in rows if r.get("deep_hot_vacuum")]
    if deep_hot:
        out.append(
            "[高温深真空 ≤10 kPa 且 ≥250 ℃] "
            + "、".join(f"{r['stream']}({r['operating_temp_c']:g}℃)" for r in deep_hot)
            + " —— 弹性模量折减,严格核算外压失稳支撑跨距"
        )
    vac_large = [r for r in rows if r["vacuum"] and r["dn"] and r["dn"] >= 150]
    for r in vac_large:
        out.append(
            f"    DN{r['dn']} {r['stream']}: 外压筛查 t_min="
            f"{(r['external_wall_mm'] or 0):.2f} mm,实选 {r['wall_mm']:g} mm"
            " —— 需按实际支撑跨距复核"
        )
    hot_vac = [r for r in rows if r.get("hot_vacuum") and (r.get("dn") or 0) >= 400]
    if hot_vac:
        out.append(
            "[高温大口径真空] ≥250 ℃ 且 DN≥400: "
            + "、".join(f"{r['stream']}(DN{r['dn']},{r['wall_mm']:g}mm)" for r in hot_vac)
            + " —— 壁厚下限 10 mm,高温外压失稳复核"
        )
    hot_small = [r for r in rows if r.get("hot_vacuum") and (r.get("dn") or 0) < 400]
    if hot_small:
        out.append(
            "[高温真空(小口径,不下限)] "
            + "、".join(f"{r['stream']}(DN{r['dn']})" for r in hot_small)
            + " —— 已提示按实际壁温做外压复核"
        )

    stiff = [r for r in rows if r.get("vacuum_stiffener")]
    if stiff:
        out.append(
            "[DN≥700 真空焊接管] "
            + "、".join(f"{r['stream']}(DN{r['dn']})" for r in stiff)
            + " —— 需设外压加强圈,严禁全真空抽吸"
        )
    out.append("")
    out.append(
        "[高压不锈钢升 R1E] 4.7–5.6 MPa 共 "
        f"{s['upgraded']} 条: {names(lambda r: r['hp_ss_upgraded'])}"
    )
    for r in rows:
        if r["hp_ss_upgraded"]:
            out.append(
                f"    {r['stream']}: DN{r['dn']} 壁厚 {r['wall_mm']:g} mm "
                f"(公式 t_req={r['t_required_mm']:.2f} mm)→ {r['grade_code']}"
            )
    r1e = [
        r for r in rows if not r["hp_ss_upgraded"] and str(r["grade_code"] or "").startswith("R1E")
    ]
    if r1e:
        out.append(
            "[自然 R1E(按同一公式算壁厚)] "
            + "、".join(f"{r['stream']}(DN{r['dn']},{r['wall_mm']:g}mm)" for r in r1e)
        )
    out.append("")
    out.append(f"[两相分层 / 波状流风险] DN≥800 两相: {names(lambda r: r['stratified_risk'])}")
    out.append(
        "[汽蚀限速 0.8 m/s] 液相 80–105 ℃ / 95–115 kPa 吸入侧(排除泵出口与升压线): "
        f"{names(lambda r: r['cavitation_limited'])}"
    )
    tol = [r for r in rows if r.get("oversize_tolerated")]
    if tol:
        out.append(
            "[DN1000 +5% 容差(996–1045 mm)] "
            + "、".join(
                f"{r['stream']}(内径 {r['inner_d_mm']:.1f}mm,"
                f"超 {pipe_calc.dn1000_oversize_pct(r['inner_d_mm']):.1f}%,"
                f"DN{r['dn']}×{r['wall_mm']:g}mm)"
                for r in tol
            )
            + " —— 建议复核压降"
        )
    out.append(
        "[深冷 ≤−20 ℃] "
        f"{s['low_temp']} 条: {names(lambda r: r['low_temp'])}"
        " —— 复核焊缝低温韧性及低温螺栓选型"
    )
    out.append(
        "[高温 ≥300 ℃] "
        f"{s['high_temp']} 条: {names(lambda r: r['high_temp'])}"
        " —— 需做柔性应力分析(热膨胀补偿)"
    )
    out.append(f"[非标准流水号] {names(lambda r: r['nonstandard_no'])} —— 保留原名,不强行重映射")
    if s.get("excluded_nonmaterial"):
        out.append(
            f"[非物流已剔除] {s['excluded_nonmaterial']} 条热量流 / 功流,不进主表也不进未选型清单"
        )
    out.append("[零流量 / 备用] " + ("、".join(str(m["stream"]) for m in res["flagged"]) or "(无)"))
    out.append(
        "[保温 -H] 设计温度 = 操作 +20 ℃,操作 ≥ 45 ℃(设计 ≥ 65 ℃)加 -H;"
        "操作 40–45 ℃ 边界流股: "
        + names(lambda r: r["design_temp_c"] is not None and 60 <= r["design_temp_c"] < 65)
        + "(均不加 -H,防常温管线误判)"
    )
    if res["missing"]:
        gas_cnt = sum(1 for m in res["missing"] if m.get("phase") == "gas")
        two_cnt = sum(1 for m in res["missing"] if m.get("phase") == "two")
        out.append(
            f"[未选型分类] 气相 {gas_cnt} 条(可按低压风管专项设计)、"
            f"两相 {two_cnt} 条(需多管并联),见表后清单"
        )
    out.append("")
    v = pipe_calc.validate(rows)
    out.append(f"[校验] error={v['summary']['error']} warn={v['summary']['warn']}")
    for f in v["findings"]:
        if f["level"] == "error":
            out.append(f"    ERROR {f['stream']} {f['code']}: {f['message']}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", type=int, default=55, help="工况 id(默认 55)")
    ap.add_argument("--db", default="data/cases.db", help="工况库路径")
    ap.add_argument("--out", default=None, help="输出 .docx 路径(默认 data/preview/)")
    ap.add_argument("--audit", action="store_true", help="打印 §17 校核审计")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"未找到工况库:{db_path}", file=sys.stderr)
        return 2
    conn = db.open_db(str(db_path))
    try:
        case = db.get_case(conn, args.case)
        if case is None:
            print(f"库里没有工况 {args.case}", file=sys.stderr)
            return 2

        if args.audit:
            from app.export import pipe_schedule  # noqa: F401 —— 保证渲染依赖可用

            streams = [dict(s) for s in db.read_streams(conn, args.case)]
            blocks = [dict(b) for b in db.read_blocks(conn, args.case)]
            conns = [dict(c) for c in db.read_connections(conn, args.case)]
            try:
                res = pipe_calc.build(streams, blocks, conns)
            except pipe_calc.TablesNotReady as exc:
                print(f"标准表未录入,无法选型:{exc}", file=sys.stderr)
                return 3
            for line in _audit(res, dict(case)):
                print(line)

        from app.export import docx_kit as kit
        from app.export.pipe_schedule import build_pipe_docx

        kit.activate("pipe")
        payload, filename = build_pipe_docx(conn, args.case)
    except pipe_calc.TablesNotReady as exc:
        print(f"标准表未录入,无法出表:{exc}", file=sys.stderr)
        return 3
    finally:
        conn.close()

    out = Path(args.out) if args.out else (Path("data/preview") / filename)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    print(f"已生成《管道选型一览表》: {out} ({len(payload)} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
