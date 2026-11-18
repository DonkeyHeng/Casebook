"""管路阻力计算:流速 / Re / λ / Σhf —— 纯函数,不碰 Aspen,可直接进自测。

口径来自两份设计算例(docs/pump-selection-plan.md §3.1):
    Σhf = (λ·L/d + Σζ) · u²/(2g)
     u  = V / (π d² / 4)
     Re = d·u·ρ / μ

三条硬约定
----------
* **不做缺省猜测**:输入缺一个就返回 None 并说明缺什么,不拿 0 顶替;
* **λ 用哪个公式要显式给**,并把结果一起返回,方便人工复核 —— 两份算例本身
  一个查 Moody(0.021)、一个用顾毓珍公式(0.028),说明这不是唯一解;
* L(管长)与 Σζ(管件)永远由人给 —— 模型里没有布置信息(实测:56 台泵的模型
  里 0 个 Pipe 块)。
"""

from __future__ import annotations

import math

G = 9.81

# 无缝钢管的绝对粗糙度常用值(mm)。不同手册 0.03–0.1 都有人用,所以这里只是默认值,
# 调用方应允许覆盖并把它显示出来。
EPS_DEFAULT_MM = 0.05

# λ 的可选口径
METHOD_COLEBROOK = "colebrook"  # 工程通用,隐式,需迭代
METHOD_GU = "gu"  # 顾毓珍(光滑管湍流):λ = 0.01227 + 0.7543/Re^0.38
METHOD_MANUAL = "manual"  # 人直接给 λ

LAMINAR_LIMIT = 2300.0


def velocity_ms(vol_m3h, id_mm):
    """管内流速 m/s;参数不全返回 None。"""
    if not vol_m3h or not id_mm or vol_m3h <= 0 or id_mm <= 0:
        return None
    area_m2 = math.pi * (id_mm / 1000.0) ** 2 / 4.0
    return (vol_m3h / 3600.0) / area_m2


def reynolds(d_mm, u_ms, rho_kgm3, mu_cp):
    """雷诺数(无量纲);参数不全返回 None。μ 用 cP。"""
    if not d_mm or not u_ms or not rho_kgm3 or not mu_cp:
        return None
    if d_mm <= 0 or u_ms <= 0 or rho_kgm3 <= 0 or mu_cp <= 0:
        return None
    mu_pa_s = mu_cp / 1000.0
    return (d_mm / 1000.0) * u_ms * rho_kgm3 / mu_pa_s


def colebrook(re, eps_mm, d_mm, iterations: int = 60, tol: float = 1e-10):
    """Colebrook-White 隐式式,固定点迭代求解 λ;不收敛返回 None。"""
    if not re or not eps_mm or not d_mm or re <= 0 or d_mm <= 0:
        return None
    rel = eps_mm / d_mm
    lam = 0.02
    for _ in range(iterations):
        nxt = 1.0 / (-2.0 * math.log10(rel / 3.7 + 2.51 / (re * math.sqrt(lam)))) ** 2
        if not math.isfinite(nxt) or nxt <= 0:
            return None
        if abs(nxt - lam) < tol:
            return nxt
        lam = nxt
    return lam if math.isfinite(lam) else None


def gu_yuzhen(re):
    """顾毓珍光滑管公式(3×10³ < Re < 3×10⁶);范围外返回 None。"""
    if not re or re <= 0:
        return None
    if not 3e3 <= re <= 3e6:
        return None
    return 0.01227 + 0.7543 / (re**0.38)


def friction_factor(re, eps_mm, d_mm, method: str = METHOD_COLEBROOK):
    """λ。层流(Re<2300)一律 64/Re,不受 method 影响。"""
    if not re or re <= 0:
        return None
    if re < LAMINAR_LIMIT:
        return 64.0 / re
    if method == METHOD_GU:
        return gu_yuzhen(re)
    if method == METHOD_COLEBROOK:
        return colebrook(re, eps_mm or EPS_DEFAULT_MM, d_mm)
    return None


def head_loss_m(l_m, id_mm, zeta_sum, lam, u_ms):
    """Σhf(米)。任何一项缺失都返回 None。"""
    if lam is None or u_ms is None or id_mm is None or l_m is None:
        return None
    if id_mm <= 0 or l_m < 0 or u_ms < 0:
        return None
    zeta = float(zeta_sum or 0.0)
    return (lam * l_m / (id_mm / 1000.0) + zeta) * u_ms**2 / (2.0 * G)


def eps_over_d(eps_mm, id_mm):
    if not eps_mm or not id_mm:
        return None
    return eps_mm / id_mm


def solve(
    vol_m3h,
    rho_kgm3,
    mu_cp,
    id_mm,
    l_m,
    zeta_sum,
    eps_mm=None,
    method: str = METHOD_COLEBROOK,
    lam_manual=None,
):
    """一次算完,返回 {u_ms, re, lam, eps_d, hf_m, method, missing}。

    `missing` 列出缺的输入(中文),页面直接显示 —— 不返回半真半假的结果。
    """
    eps = eps_mm if eps_mm not in (None, 0) else EPS_DEFAULT_MM
    missing = []
    if not vol_m3h:
        missing.append("体积流量")
    if not rho_kgm3:
        missing.append("密度")
    if not mu_cp:
        missing.append("粘度")
    if not id_mm:
        missing.append("管内径")
    if l_m in (None, ""):
        missing.append("管长 L")
    if zeta_sum is None:
        missing.append("管件系数 Σζ")

    u = velocity_ms(vol_m3h, id_mm)
    re = reynolds(id_mm, u, rho_kgm3, mu_cp) if u else None
    if method == METHOD_MANUAL:
        lam = lam_manual
    else:
        lam = friction_factor(re, eps, id_mm, method) if re else None
    hf = head_loss_m(l_m, id_mm, zeta_sum, lam, u) if (u and lam) else None
    return {
        "u_ms": u,
        "re": re,
        "lam": lam,
        "eps_d": eps_over_d(eps, id_mm),
        "eps_mm": eps,
        "hf_m": hf,
        "method": method,
        "missing": missing,
    }
