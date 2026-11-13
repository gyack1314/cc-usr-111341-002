"""下一轮追访安排：按减少判断不确定性排序，不按联系难易排序。

追访价值定义为“追访成功后联合范围的预期收缩量”。对每个仍有未定状态
个体的年龄地区组，模拟解决一名未定个体（按设计权重折算）后，在使用/
未使用两个分支下分别重算联合可达范围，按组内 MAR 使用率（无支撑时
取证据界中点仅用于分支加权，不写入估计）合成期望收缩：

    收缩 = 当前总体范围宽度 − E[追访后范围宽度]
    个体价值 = 组收缩 × 该个体权重占组内未定权重的比例

联系次数、授权缺口只用于展示与执行提示，不进入排序打分。预算按名单
中固定的个人累加（重复联系不增加人数），并给出各预算检查点上的
收缩后范围，供发布者安排追访规模。
"""
from dataclasses import dataclass

from .calibration import (
    STATUS_CONFLICT,
    STATUS_UNKNOWN,
)
from .sensitivity import (
    GroupRateInfo,
    joint_coverage_range,
    _rate_bounds_from_weights,
    _clip01,
)

UNSETTLED = (STATUS_CONFLICT, STATUS_UNKNOWN)


@dataclass
class FollowupCandidate:
    person_id: str
    group: tuple
    weight: float
    status: str
    attempts: int
    authorization_gap: bool
    group_expected_contract: float
    value: float

    def audit_row(self):
        return {
            'person_id': self.person_id,
            'group': f'{self.group[0]}|{self.group[1]}',
            'design_weight': self.weight,
            'current_status': self.status,
            'prior_attempts': self.attempts,
            'authorization_gap': self.authorization_gap,
            'group_expected_contract': self.group_expected_contract,
            'followup_value': self.value,
            'ranking_note': '仅按范围收缩打分；联系次数不参与排序',
        }


def _width(result):
    if not result['feasible']:
        return 0.0
    return result['overall_max'] - result['overall_min']


def _simulate_person_resolved(info, obs_weight, branch):
    """模拟组内一个权重为 obs_weight 的未定个体被确定为 used/not_used。"""
    w = info.weights
    total = w['total']
    if branch == 1.0:
        settled_used = w['definite_used'] + obs_weight
        settled_notused = w['definite_notused']
    else:
        settled_used = w['definite_used']
        settled_notused = w['definite_notused'] + obs_weight
    if info.identifiable:
        lo, hi = _rate_bounds_from_weights(
            total, settled_used, settled_notused,
            info.respondent_rate, *info.rho_domain)
    else:
        lo = settled_used / total
        hi = _clip01(1.0 - settled_notused / total)
    return GroupRateInfo(
        key=info.key, margin_lower=info.margin_lower,
        margin_upper=info.margin_upper, identifiable=info.identifiable,
        respondent_rate=info.respondent_rate, rho_domain=info.rho_domain,
        rate_lower=lo, rate_upper=hi,
        evidence_lower=lo, evidence_upper=hi, weights=w,
        underidentified_reason=info.underidentified_reason,
        notes=list(info.notes))


def person_followup_value(obs, info, infos, margins, base_width):
    """解决某个具体个体后的联合范围预期收缩量。

    按该个体的完整设计权重模拟 used/not_used 两个分支，分支概率取组内
    MAR 使用率（识别不足组用证据界中点，仅用于分支加权、不写入估计）。
    联系难易与历史联系次数不参与此计算。
    """
    p_used = info.respondent_rate if info.identifiable else \
        (info.evidence_lower + info.evidence_upper) / 2
    expected_width = 0.0
    for branch, prob in ((1.0, p_used), (0.0, 1.0 - p_used)):
        simulated = [_simulate_person_resolved(g, obs.weight, branch)
                     if g.key == info.key else g for g in infos]
        res = joint_coverage_range(simulated, margins, try_integer=False)
        expected_width += prob * (_width(res) if res['feasible'] else base_width)
    return base_width - expected_width


def group_followup_values(infos, margins):
    """基线联合范围与各组未定权重，供逐人价值计算复用。"""
    baseline = joint_coverage_range(infos, margins, try_integer=False)
    if not baseline['feasible']:
        return {}, None
    return baseline, baseline


def build_followup_plan(observations, infos, margins, budget_checkpoints=(1, 2, 3)):
    group_result, baseline = group_followup_values(infos, margins)
    if baseline is None:
        return {'feasible': False, 'candidates': [],
                'reason': '边际矛盾时追访无法收缩范围，先处理口径冲突'}
    base_width = _width(baseline)

    info_by_key = {i.key: i for i in infos}
    candidates = []
    for obs in observations:
        if obs.status not in UNSETTLED:
            continue
        info = info_by_key[obs.group_key]
        unsettled_weight = info.weights['conflict'] + info.weights['unknown']
        value = person_followup_value(obs, info, infos, margins, base_width)
        candidates.append(FollowupCandidate(
            person_id=obs.id, group=obs.group_key, weight=obs.weight,
            status=obs.status, attempts=obs.contact.attempt_count,
            authorization_gap=not obs.admin_visible,
            group_expected_contract=value,
            value=value))

    candidates.sort(key=lambda c: c.value, reverse=True)
    # 预算检查点不超过实际候选人数，避免重复名单
    feasible_budgets = sorted({min(b, len(candidates))
                               for b in budget_checkpoints if candidates})
    checkpoints = []
    for budget in feasible_budgets:
        chosen = candidates[:budget]
        if not chosen:
            continue
        simulated = list(infos)
        # 在每个组内累计被选中解决的权重，重算一次组界
        resolved = {}
        for c in chosen:
            cur = resolved.setdefault(c.group, [0.0, 0.0])
            # 检查点按期望收缩展示：两分支各报一个范围端点场景
            cur[0] += c.weight
        scen_infos = []
        for info in simulated:
            used_w = resolved.get(info.key, [0.0])[0]
            if used_w <= 0:
                scen_infos.append(info)
                continue
            w = info.weights
            total = w['total']
            if info.identifiable:
                lo, _ = _rate_bounds_from_weights(
                    total, w['definite_used'] + used_w,
                    w['definite_notused'], info.respondent_rate, *info.rho_domain)
                _, hi = _rate_bounds_from_weights(
                    total, w['definite_used'],
                    w['definite_notused'] + used_w, info.respondent_rate, *info.rho_domain)
            else:
                lo = (w['definite_used'] + used_w) / total
                hi = _clip01(1.0 - w['definite_notused'] / total)
            scen_infos.append(GroupRateInfo(
                key=info.key, margin_lower=info.margin_lower,
                margin_upper=info.margin_upper, identifiable=info.identifiable,
                respondent_rate=info.respondent_rate, rho_domain=info.rho_domain,
                rate_lower=lo, rate_upper=hi, evidence_lower=lo,
                evidence_upper=hi, weights=w,
                underidentified_reason=info.underidentified_reason,
                notes=list(info.notes)))
        after = joint_coverage_range(scen_infos, margins, try_integer=False)
        checkpoints.append({
            'budget_persons': budget,
            'chosen_ids': [c.person_id for c in chosen],
            'projected_range': (
                {'min': after['overall_min'], 'max': after['overall_max']}
                if after['feasible'] else {'feasible': False}),
            'projected_width': _width(after) if after['feasible'] else None,
            'baseline_width': _width(baseline),
        })

    return {
        'feasible': True,
        'baseline_range': {'min': baseline['overall_min'],
                           'max': baseline['overall_max']},
        'ranking_rule': '追访价值=联合可达范围的预期收缩×个体未定权重占比；联系难易不入模',
        'candidates': [c.audit_row() for c in candidates],
        'checkpoints': checkpoints,
    }
