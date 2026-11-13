"""非随机未回应的敏感性参数化与联合可行范围求解。

参数化（可解释）
    对每个年龄地区组，设回应者使用率为 r_i（设计权重），未回应者相对
    可比回应者的使用倾向为 ρ_i，组内未回应者使用率裁剪到 [0,1]：
        u_i = clip(ρ_i · r_i, 0, 1)
    ρ_i 在冻结敏感域 [ρ_lo, ρ_hi] 内变化；ρ=1 即完全随机不回应（MAR）。
    组率上、下界由确定状态权重与未定状态权重按 ρ 端点组合得到。

识别不足
    组内没有任何带使用答案的回应者时，r_i 不存在，ρ 参数化失去支撑：
    不默认半数使用、不制造极端权重，改用仅由确定状态决定的证据界
    （Manski 型界），并在结果中标记“识别不足”。

联合约束
    各组人数 n_i 受边际闭区间约束，汇总边际（地区/年龄/总体）同时成立；
    目标为总体率 Σx_i / Σn_i 的可达范围，经 Charnes-Cooper 变换
    （t=1/Σn, y=n·t, z=x·t）转为线性规划，杜绝各组各取最坏端点
    拼成不可能的人群。边际矛盾（可行域为空）与缺少样本支撑分别报告。
"""
from dataclasses import dataclass, field

from .calibration import Calibration
from .lpsolve import solve, LPInfeasible, LPUnbounded
from .structural import structural_range, structural_integer_exact


@dataclass
class GroupRateInfo:
    key: tuple
    margin_lower: float
    margin_upper: float
    identifiable: bool
    respondent_rate: object            # 无支撑时为 None
    rho_domain: tuple
    rate_lower: float                  # 组率可达下界
    rate_upper: float                  # 组率可达上界
    evidence_lower: float              # 仅确定状态给出的证据界
    evidence_upper: float
    weights: dict
    underidentified_reason: str = ''
    notes: list = field(default_factory=list)

    def label(self):
        return f'{self.key[0]}|{self.key[1]}'


def _clip01(v):
    return min(1.0, max(0.0, v))


def _rate_bounds_from_weights(total, settled_used, settled_notused, r, rho_lo, rho_hi):
    """由权重结构与ρ敏感域计算组率上、下界。"""
    flexible = total - settled_used - settled_notused
    lo = _clip01((settled_used + flexible * _clip01(rho_lo * r)) / total)
    hi = _clip01((settled_used + flexible * _clip01(rho_hi * r)) / total)
    return lo, hi


def build_group_rate_infos(groups, margin_map, calibration: Calibration):
    infos = []
    missing_margins = []
    for key, g in groups.items():
        if key not in margin_map:
            missing_margins.append(key)
            continue
        margin = margin_map[key]
        total = g.total_weight
        evidence_lo = g.definite_used_weight / total
        evidence_hi = _clip01(1.0 - g.definite_notused_weight / total)
        rho_lo, rho_hi = calibration.domain_for(*key)
        weights = {
            'total': total,
            'respondent': g.respondent_weight,
            'respondent_used': g.respondent_used_weight,
            'definite_used': g.definite_used_weight,
            'definite_notused': g.definite_notused_weight,
            'conflict': g.conflict_weight,
            'unknown': g.unknown_weight,
        }

        if not g.has_respondent_support:
            reason = '该组没有任何带使用答案的回应者，ρ参数化无可比支撑'
            infos.append(GroupRateInfo(
                key=key, margin_lower=margin.lower, margin_upper=margin.upper,
                identifiable=False, respondent_rate=None,
                rho_domain=(rho_lo, rho_hi),
                rate_lower=evidence_lo, rate_upper=evidence_hi,
                evidence_lower=evidence_lo, evidence_upper=evidence_hi,
                weights=weights, underidentified_reason=reason,
                notes=['识别不足：仅报告证据界，不使用ρ调整与默认使用率']))
            continue

        r = g.mar_rate()
        # 组率 = 确定使用权重 + 未定状态权重 × clip(ρ r)，全部除以总权重；
        # 未定状态 = 总权重 - 确定使用 - 确定未使用（冲突与无法确认均在内）
        lo, hi = _rate_bounds_from_weights(
            total, g.definite_used_weight, g.definite_notused_weight,
            r, rho_lo, rho_hi)
        infos.append(GroupRateInfo(
            key=key, margin_lower=margin.lower, margin_upper=margin.upper,
            identifiable=True, respondent_rate=r,
            rho_domain=(rho_lo, rho_hi),
            rate_lower=lo, rate_upper=hi,
            evidence_lower=evidence_lo, evidence_upper=evidence_hi,
            weights=weights,
            notes=[f'回应者使用率r={r:.6f}，敏感域ρ∈[{rho_lo},{rho_hi}]']))
    return infos, missing_margins


def _lp_overall(rate_bounds, margins, aggregate_margins, sense):
    """Charnes-Cooper 变换后的总体率极值 LP。

    变量顺序：每组 y_i, z_i，最后 t。约束 Σy=1。
    """
    k = len(rate_bounds)
    n_vars = 2 * k + 1

    def yi(i):
        return 2 * i

    def zi(i):
        return 2 * i + 1

    t_idx = 2 * k

    a_ub, b_ub = [], []
    for i, (lo, hi, mlo, mhi) in enumerate(rate_bounds):
        # mlo·t ≤ y_i ≤ mhi·t
        a_ub.append([0.0] * n_vars); a_ub[-1][yi(i)] = 1.0; a_ub[-1][t_idx] = -mhi
        b_ub.append(0.0)
        a_ub.append([0.0] * n_vars); a_ub[-1][yi(i)] = -1.0; a_ub[-1][t_idx] = mlo
        b_ub.append(0.0)
        # lo·y_i ≤ z_i ≤ hi·y_i
        a_ub.append([0.0] * n_vars); a_ub[-1][zi(i)] = 1.0; a_ub[-1][yi(i)] = -hi
        b_ub.append(0.0)
        a_ub.append([0.0] * n_vars); a_ub[-1][zi(i)] = -1.0; a_ub[-1][yi(i)] = lo
        b_ub.append(0.0)
        # 0 ≤ z_i ≤ y_i（z_i - y_i ≤ 0）
        a_ub.append([0.0] * n_vars); a_ub[-1][zi(i)] = 1.0; a_ub[-1][yi(i)] = -1.0
        b_ub.append(0.0)

    for m in aggregate_margins:
        idxs = [i for i, info in enumerate(rate_bounds)
                if _info_matches(info, m.key)]
        if not idxs:
            raise LPInfeasible(f'汇总边际{m.key}在样本中没有对应组')
        row_hi = [0.0] * n_vars
        row_lo = [0.0] * n_vars
        for i in idxs:
            row_hi[yi(i)] = 1.0
            row_lo[yi(i)] = -1.0
        row_hi[t_idx] = -m.upper
        row_lo[t_idx] = m.lower
        a_ub.append(row_hi); b_ub.append(0.0)
        a_ub.append(row_lo); b_ub.append(0.0)

    a_eq = [[0.0] * n_vars]
    for i in range(k):
        a_eq[0][yi(i)] = 1.0
    b_eq = [1.0]

    c = [0.0] * n_vars
    for i in range(k):
        c[zi(i)] = 1.0
    value, x = solve(c, a_eq=a_eq, b_eq=b_eq, a_ub=a_ub, b_ub=b_ub, sense=sense)
    t = x[t_idx]
    n_solution = [x[yi(i)] / t for i in range(k)]
    user_solution = [x[zi(i)] / t for i in range(k)]
    return value, n_solution, user_solution


def _info_matches(info, agg_key):
    area, age = agg_key
    if area != '*' and info.key[0] != area:
        return False
    if age != '*' and info.key[1] != age:
        return False
    return True


def _naive_endpoint_range(infos):
    """每组各取最坏端点的拼法，用于与联合范围对照。"""
    min_num = sum(i.rate_lower * i.margin_lower for i in infos)
    min_den = sum(i.margin_upper for i in infos)
    max_num = sum(i.rate_upper * i.margin_upper for i in infos)
    max_den = sum(i.margin_lower for i in infos)
    return min_num / min_den, max_num / max_den


def joint_coverage_range(infos, margins, try_integer=True):
    """总体覆盖率联合可达范围，区分边际矛盾与识别不足。

    优先用结构端点枚举（对本问题的盒约束+线性分式结构可靠且无退化
    死锁）；组数或组合规模超限时回退到 Charnes-Cooper 单纯形。
    两条路径都在同一联合边际域内求解，不做各组独立最坏端点拼合。
    """
    rate_bounds = [(i.rate_lower, i.rate_upper, i.margin_lower, i.margin_upper)
                   for i in infos]
    group_keys = [i.key for i in infos]
    aggregates = [m for m in margins if not m.is_group]
    result = {
        'feasible': True,
        'margin_conflict': False,
        'underidentified_groups': [i.label() for i in infos if not i.identifiable],
    }

    structural = structural_range(rate_bounds, margins, aggregates, group_keys)
    if structural is not None:
        if structural.get('infeasible'):
            return {**result, 'feasible': False, 'margin_conflict': True,
                    'conflict_reason': '枚举全部边际端点组合，无一满足汇总边际：联合可行域为空'}
        return _finish_range(result, structural, infos, aggregates, try_integer)

    try:
        rmin, n_min, x_min = _lp_overall(rate_bounds, margins, aggregates, 'min')
        rmax, n_max, x_max = _lp_overall(rate_bounds, margins, aggregates, 'max')
    except LPInfeasible as exc:
        return {**result, 'feasible': False, 'margin_conflict': True,
                'conflict_reason': str(exc)}
    except LPUnbounded as exc:
        return {**result, 'feasible': False, 'margin_conflict': True,
                'conflict_reason': f'参数化缺界：{exc}'}

    structural = {
        'overall_min': rmin, 'overall_max': rmax,
        'lp_min_population': n_min, 'lp_max_population': n_max,
        'lp_min_users': x_min, 'lp_max_users': x_max,
        'method': 'Charnes-Cooper 单纯形（规模超枚举上限回退）',
    }
    return _finish_range(result, structural, infos, aggregates, try_integer)


def _finish_range(result, structural, infos, aggregates, try_integer):
    rmin, rmax = structural['overall_min'], structural['overall_max']
    naive_min, naive_max = _naive_endpoint_range(infos)
    result.update({
        'overall_min': rmin,
        'overall_max': rmax,
        'solver_method': structural['method'],
        'lp_min_population': [round(v, 6) for v in structural['lp_min_population']],
        'lp_min_users': [round(v, 6) for v in structural['lp_min_users']],
        'lp_max_population': [round(v, 6) for v in structural['lp_max_population']],
        'lp_max_users': [round(v, 6) for v in structural['lp_max_users']],
        'naive_independent_endpoint_min': naive_min,
        'naive_independent_endpoint_max': naive_max,
        'joint_tighter_than_naive': (rmin > naive_min + 1e-9) or
                                    (rmax < naive_max - 1e-9),
    })
    if try_integer:
        integer = structural_integer_exact(
            [(i.rate_lower, i.rate_upper, i.margin_lower, i.margin_upper)
             for i in infos], aggregates, [i.key for i in infos])
        if integer is not None:
            result['integer_exact'] = integer
    return result


def scenario_infos(infos, rho_by_group):
    """给定一组 ρ 情景（MAR 或特定组集中不回应），重算组率界。

    识别不足组保持证据界，不参与 ρ 调整。
    """
    out = []
    for info in infos:
        if not info.identifiable:
            out.append(info)
            continue
        rho = rho_by_group.get(info.key, 1.0)
        r = info.respondent_rate
        total = info.weights['total']
        rate, _ = _rate_bounds_from_weights(
            total, info.weights['definite_used'],
            info.weights['definite_notused'], r, rho, rho)
        out.append(GroupRateInfo(
            key=info.key, margin_lower=info.margin_lower,
            margin_upper=info.margin_upper, identifiable=True,
            respondent_rate=r, rho_domain=(rho, rho),
            rate_lower=rate, rate_upper=rate,
            evidence_lower=info.evidence_lower, evidence_upper=info.evidence_upper,
            weights=info.weights,
            notes=[f'情景固定ρ={rho}（MAR基准ρ=1）']))
    return out


def tipping_analysis(infos, margins, target_rate):
    """逐组寻找足以翻转达标判断的未回应倾向。

    从 MAR（各组 ρ=1）出发，每次只让一个组的 ρ 在其敏感域内移动，
    其余组保持 1，对总体率极值做二分搜索，记录临界 ρ 与可达性。
    """
    findings = []
    base = scenario_infos(infos, {i.key: 1.0 for i in infos})
    base_range = joint_coverage_range(base, margins, try_integer=False)
    if not base_range['feasible']:
        return {'feasible': False, 'findings': []}

    for info in infos:
        if not info.identifiable:
            findings.append({
                'group': info.label(), 'identifiable': False,
                'note': '识别不足组无法定义临界ρ，其证据界已直接决定翻转空间'})
            continue
        rho_lo, rho_hi = info.rho_domain
        entry = {'group': info.label(), 'identifiable': True,
                 'rho_domain': [rho_lo, rho_hi]}
        # 向下压（未回应者更不可能使用）：找使总体最大值恰好低于目标的ρ
        def overall_max_at(rho):
            scen = scenario_infos(infos, {info.key: rho})
            res = joint_coverage_range(scen, margins, try_integer=False)
            return res['overall_max'] if res['feasible'] else None

        max_at_lo = overall_max_at(rho_lo)
        max_at_hi = overall_max_at(rho_hi)
        if max_at_lo is None:
            entry['direction_down'] = '边际矛盾，无法评估'
        elif max_at_hi < target_rate and max_at_lo < target_rate:
            entry['direction_down'] = '全敏感域内均不达标'
        elif max_at_lo >= target_rate:
            entry['direction_down'] = '即使ρ取敏感域下界仍达标，单组下压不足以翻转'
        else:
            lo, hi = rho_lo, rho_hi
            for _ in range(40):
                mid = (lo + hi) / 2
                if overall_max_at(mid) >= target_rate:
                    lo = mid
                else:
                    hi = mid
            entry['direction_down'] = {
                'critical_rho': round((lo + hi) / 2, 6),
                'interpretation': f'该组ρ低于该临界值时，即使其他组保持MAR，总体也无法达标'}
        findings.append(entry)
    return {'feasible': True, 'mar_overall_range': {
                'min': base_range['overall_min'], 'max': base_range['overall_max']},
            'findings': findings}
