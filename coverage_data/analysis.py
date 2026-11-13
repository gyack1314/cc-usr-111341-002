"""分析编排：把冻结口径、个体状态、设计基准、敏感性联合范围、
翻转判断与追访安排串成一份可追溯的报告。

报告中抽样波动范围与未回应假设范围分开陈列；任何一组识别不足或边际
矛盾都不会被默认值掩盖，发布者先看到结论依赖的假设再决定能否宣称达标。
"""
from .calibration import DEFAULT_CALIBRATION, STATUS_UNKNOWN, STATUS_CONFLICT
from .observe import build_observations
from .margins import parse_margins, group_margin_map
from .design import (
    build_groups,
    mar_overall_rate,
    mar_point_estimate,
    cluster_bootstrap,
)
from .sensitivity import (
    build_group_rate_infos,
    joint_coverage_range,
    scenario_infos,
    tipping_analysis,
)
from .followup import build_followup_plan


def _verdict(joint, target_rate):
    """达标判断。识别不足不阻止判断，但会在文案中注明证据强度。"""
    if not joint['feasible']:
        return '无法判定：边际矛盾（联合可行域为空），先修正口径'
    under = joint['underidentified_groups']
    tag = f'（含识别不足组 {",".join(under)}，结论仅由证据界支撑）' if under else ''
    if joint['overall_min'] >= target_rate:
        return f'可宣称达标：全敏感域下界{joint["overall_min"]:.4f}≥目标{target_rate}{tag}'
    if joint['overall_max'] < target_rate:
        return f'应宣称不达标：全敏感域上界{joint["overall_max"]:.4f}<目标{target_rate}{tag}'
    return (f'不能宣称达标：联合范围[{joint["overall_min"]:.4f}, '
            f'{joint["overall_max"]:.4f}]跨越目标线，结论取决于未回应假设{tag}')


def _scenario_report(name, infos, margins, target_rate, rho_map):
    scen = scenario_infos(infos, rho_map)
    joint = joint_coverage_range(scen, margins, try_integer=False)
    out = {'scenario': name, 'feasible': joint['feasible'],
           'group_rates': {i.label(): {'rate_lower': i.rate_lower,
                                       'rate_upper': i.rate_upper,
                                       'identifiable': i.identifiable}
                           for i in scen}}
    if joint['feasible']:
        out['overall_range'] = {'min': joint['overall_min'],
                                'max': joint['overall_max']}
        out['verdict_vs_target'] = _verdict(joint, target_rate)
    else:
        out['conflict_reason'] = joint.get('conflict_reason')
    return out


def run_analysis(tables, manifest_path=None, calibration=DEFAULT_CALIBRATION):
    observations = build_observations(tables, calibration)
    margins = parse_margins(tables['margins'])
    margin_map = group_margin_map(margins)
    groups = build_groups(observations)
    infos, missing_margins = build_group_rate_infos(groups, margin_map, calibration)
    if missing_margins:
        labels = [f'{a}|{ag}' for a, ag in missing_margins]
        raise ValueError(f'以下年龄地区组缺少总体边际区间，无法构造联合范围：{labels}')

    # 基准：完全随机不回应
    mar_rate, under_mar = mar_overall_rate(groups)
    mar_point = mar_point_estimate(groups)
    bootstrap = cluster_bootstrap(
        observations, calibration,
        all_group_keys=set(groups.keys()))

    # 未回应敏感性联合范围（敏感域内同时变化）
    joint = joint_coverage_range(infos, margins)

    # 对照情景：完全随机不回应（MAR）与老年组集中不回应
    mar_scenario = _scenario_report(
        'mar', infos, margins, calibration.target_rate,
        {i.key: 1.0 for i in infos})
    elderly_scenario = _scenario_report(
        'elderly_concentrated_nonresponse', infos, margins,
        calibration.target_rate,
        {i.key: 0.5 for i in infos if i.key[1] == 'old'})

    # 翻转判断
    tipping = tipping_analysis(infos, margins, calibration.target_rate)

    # 追访安排
    followup = build_followup_plan(observations, infos, margins)

    # 授权缺口与冲突清单
    authorization_gaps = [o.audit_row() for o in observations if not o.admin_visible]
    conflicts = [o.audit_row() for o in observations
                 if o.status in (STATUS_CONFLICT, STATUS_UNKNOWN)]

    group_section = []
    for info in infos:
        g = groups[info.key]
        group_section.append({
            'group': info.label(),
            'clusters': sorted(g.clusters),
            'sample_persons': len(g.observations),
            'respondent_support': info.identifiable,
            'respondent_rate_mar': info.respondent_rate,
            'bootstrap_mar_quantiles': bootstrap['group_mar_quantiles'].get(
                info.label()),
            'evidence_bounds': {'lower': info.evidence_lower,
                                'upper': info.evidence_upper},
            'sensitivity_rate_range': {'lower': info.rate_lower,
                                       'upper': info.rate_upper},
            'population_margin': {'lower': info.margin_lower,
                                  'upper': info.margin_upper},
            'underidentified_reason': info.underidentified_reason or None,
            'notes': info.notes,
        })

    report = {
        'calibration': {**calibration.as_registry(), 'digest': calibration.digest()},
        'manifest': manifest_path,
        'decomposition_note': (
            'bootstrap_mar_* 仅量化抽样波动（固定MAR假设、整组重抽样）；'
            'sensitivity_* 仅量化未回应假设（敏感域联合LP）；二者来源不同，分开陈列'),
        'mar_baseline': {
            'overall_rate': mar_rate,
            'underidentified_groups': [f'{a}|{ag}' for a, ag in under_mar],
            'point_estimate': mar_point,
            'bootstrap': bootstrap,
        },
        'nonresponse_sensitivity': {
            'joint_overall_range': (
                {'min': joint['overall_min'], 'max': joint['overall_max']}
                if joint['feasible'] else None),
            'feasible': joint['feasible'],
            'margin_conflict': joint['margin_conflict'],
            'conflict_reason': joint.get('conflict_reason'),
            'underidentified_groups': joint['underidentified_groups'],
            'lp_min_plan': {
                'population': joint.get('lp_min_population'),
                'users': joint.get('lp_min_users')} if joint['feasible'] else None,
            'lp_max_plan': {
                'population': joint.get('lp_max_population'),
                'users': joint.get('lp_max_users')} if joint['feasible'] else None,
            'naive_independent_endpoints': (
                {'min': joint.get('naive_independent_endpoint_min'),
                 'max': joint.get('naive_independent_endpoint_max'),
                 'joint_is_stricter': joint.get('joint_tighter_than_naive')}
                if joint['feasible'] else None),
            'integer_exact': joint.get('integer_exact'),
            'solver_method': joint.get('solver_method'),
            'verdict': _verdict(joint, calibration.target_rate),
        },
        'scenarios': [mar_scenario, elderly_scenario],
        'tipping_points': tipping,
        'groups': group_section,
        'followup_plan': followup,
        'authorization_gaps': authorization_gaps,
        'unsettled_individuals': conflicts,
        'person_audit': [o.audit_row() for o in observations],
    }
    return report
