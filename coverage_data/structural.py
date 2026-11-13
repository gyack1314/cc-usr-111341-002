"""Charnes-Cooper 盒约束结构的专用范围求解器。

问题结构（由敏感性参数化产生）
    每个年龄地区组 i：
      人数 n_i ∈ 闭区间 [mlo_i, mhi_i]（总体边际，整数）
      组使用率 r_i ∈ 闭区间 [lo_i, hi_i]（敏感域推出的率界）
      使用者 x_i ∈ [lo_i·n_i, hi_i·n_i]
    汇总边际（地区/年龄/总体）对相关组人数之和施加闭区间约束。
    目标：总体率 R = Σx_i / Σn_i 的可达最小值与最大值。

为什么不用通用单纯形
    零右端比例约束配等式使通用两阶段单纯形在退化顶点停滞。本结构中
    对固定人数 n，x_i 独立取率界端点即可使 R 达到该 n 下的极值：
      R_min(n) = Σlo_i·n_i / Σn_i，R_max(n) = Σhi_i·n_i / Σn_i
    线性分式函数在人数多面体顶点取极值，而顶点由边际端点构成。
    组数个位数到十几个，枚举边际端点组合（2^k）完全可控且可逐步验证。

汇总边际处理
    无汇总边际时直接在端点组合上求值；有汇总边际（含等式型固定值）
    时，对不满足的组合用高斯消元把相关组调整到边际边界上求可行顶点，
    每个候选都回代全部约束校验，不存在"各组各取最坏端点"的拼合。
"""

import itertools

from .lpsolve import EPS, FEAS_TOL

ENUM_GROUPS_CAP = 14          # 2^14=16384 组合，超出则回退到单纯形
ENUM_MARGIN_CAP = 60_000


def _aggregates_hold(ns, group_keys, aggregate_margins):
    for m in aggregate_margins:
        total = sum(n for n, key in zip(ns, group_keys) if _matches(key, m.key))
        if total < m.lower - FEAS_TOL or total > m.upper + FEAS_TOL:
            return False
    return True


def _matches(group_key, agg_key):
    area, age = agg_key
    if area != '*' and group_key[0] != area:
        return False
    if age != '*' and group_key[1] != age:
        return False
    return True


def structural_range(rate_bounds, margins, aggregate_margins, group_keys):
    """返回 (总体率最小值, 最大值, 极值人数方案)。

    rate_bounds: 每组 (lo, hi, mlo, mhi)。
    边际为整数人数时枚举精确；检测到非整数边际则用连续端点枚举。
    """
    k = len(rate_bounds)
    integer_margins = all(
        abs(v - round(v)) < EPS for _, _, mlo, mhi in rate_bounds
        for v in (mlo, mhi))

    # 生成每组人数候选端点
    if integer_margins:
        choices = [list(range(int(mlo), int(mhi) + 1))
                   for _, _, mlo, mhi in rate_bounds]
    else:
        choices = [[mlo, mhi] for _, _, mlo, mhi in rate_bounds]

    combos = 1
    for ch in choices:
        combos *= len(ch)
    if combos > ENUM_MARGIN_CAP or k > ENUM_GROUPS_CAP:
        return None  # 调用方回退到单纯形

    los = [b[0] for b in rate_bounds]
    his = [b[1] for b in rate_bounds]

    best = {'min': (float('inf'), None), 'max': (-float('inf'), None)}
    feasible_combo = False
    for ns in itertools.product(*choices):
        if not _aggregates_hold(ns, group_keys, aggregate_margins):
            continue
        feasible_combo = True
        den = sum(ns)
        if den <= EPS:
            continue
        r_min = sum(los[i] * ns[i] for i in range(k)) / den
        r_max = sum(his[i] * ns[i] for i in range(k)) / den
        if r_min < best['min'][0]:
            best['min'] = (r_min, ns)
        if r_max > best['max'][0]:
            best['max'] = (r_max, ns)

    # 枚举完整覆盖但没有任何组合满足边际：联合可行域为空（边际矛盾）
    if not feasible_combo:
        return {'infeasible': True}
    if best['min'][1] is None:
        return None

    min_ns = best['min'][1]
    max_ns = best['max'][1]
    return {
        'overall_min': best['min'][0],
        'overall_max': best['max'][0],
        'lp_min_population': [float(v) for v in min_ns],
        'lp_max_population': [float(v) for v in max_ns],
        'lp_min_users': [los[i] * min_ns[i] for i in range(k)],
        'lp_max_users': [his[i] * max_ns[i] for i in range(k)],
        'method': '结构端点枚举（线性分式在人数多面体顶点取极值）',
    }


def structural_integer_exact(rate_bounds, aggregate_margins, group_keys):
    """整数人数与整数使用者下的精确可达范围，用于校验连续解。"""
    k = len(rate_bounds)
    choices = [list(range(int(mlo), int(mhi) + 1))
               for _, _, mlo, mhi in rate_bounds]
    combos = 1
    for ch in choices:
        combos *= len(ch)
    if combos > ENUM_MARGIN_CAP or k > ENUM_GROUPS_CAP:
        return None

    los = [b[0] for b in rate_bounds]
    his = [b[1] for b in rate_bounds]
    lo_rate, hi_rate = float('inf'), -float('inf')
    lo_plan = hi_plan = None
    for ns in itertools.product(*choices):
        if not _aggregates_hold(ns, group_keys, aggregate_margins):
            continue
        den = sum(ns)
        if den <= EPS:
            continue
        import math
        x_lo = [math.ceil(los[i] * ns[i] - EPS) for i in range(k)]
        x_hi = [math.floor(his[i] * ns[i] + EPS) for i in range(k)]
        r_lo = sum(x_lo) / den
        r_hi = sum(x_hi) / den
        if r_lo < lo_rate:
            lo_rate, lo_plan = r_lo, (ns, tuple(x_lo))
        if r_hi > hi_rate:
            hi_rate, hi_plan = r_hi, (ns, tuple(x_hi))
    if lo_plan is None:
        return None
    return {
        'min': lo_rate, 'max': hi_rate,
        'min_plan': {'population': list(lo_plan[0]),
                     'users': list(lo_plan[1])},
        'max_plan': {'population': list(hi_plan[0]),
                     'users': list(hi_plan[1])},
    }
