"""分析口径测试：状态构造、识别不足、联合范围、边际矛盾与追访排序。

不依赖 data/ 下的不可信资料包，全部用内联合成表，明确每个口径的预期。
"""
import unittest

from coverage_data.calibration import (
    Calibration,
    STATUS_USED,
    STATUS_NOT_USED,
    STATUS_UNKNOWN,
    STATUS_CONFLICT,
)
from coverage_data.observe import build_observations
from coverage_data.margins import parse_margins, group_margin_map
from coverage_data.design import (
    build_groups, mar_overall_rate, cluster_bootstrap)
from coverage_data.sensitivity import (
    build_group_rate_infos,
    joint_coverage_range,
    scenario_infos,
    tipping_analysis,
)
from coverage_data.followup import build_followup_plan


def make_calibration(**overrides):
    base = dict(
        version='test-v1', period=1,
        service_definition='测试冻结口径', target_rate=0.8,
        rho_domain=(0.5, 2.0), rho_group_overrides={},
        bootstrap_reps=200, bootstrap_seed=42,
        admin_absence_is_nonuse=False,
    )
    base.update(overrides)
    return Calibration(**base)


def make_tables(people, contacts, usage=None, margins=None):
    """people: list of (id, area, age, cluster, pi, consented)。"""
    sample = [{'id': p[0], 'area': p[1], 'age': p[2], 'cluster': p[3],
               'inclusion_probability': p[4], 'consented': p[5]} for p in people]
    contact_rows = [{'id': f'k{i}', 'person': c[0], 'attempt': 1,
                     'responded': c[1], 'used': c[2]}
                    for i, c in enumerate(contacts)]
    usage_rows = [{'id': f'x{i}', 'person': u[0], 'success': u[1],
                   'period': u[2]} for i, u in enumerate(usage or [])]
    margin_rows = [{'id': f'm{i}', 'area': m[0], 'age': m[1],
                    'lower': m[2], 'upper': m[3]}
                   for i, m in enumerate(margins or [])]
    return {'sample': sample, 'contacts': contact_rows,
            'usage': usage_rows, 'margins': margin_rows}


class ObservationTests(unittest.TestCase):
    def test_admin_success_is_positive_evidence(self):
        tables = make_tables(
            people=[('p1', 'A', 'old', 'c1', 0.5, True)],
            contacts=[('p1', False, None)],
            usage=[('p1', True, 1)],
            margins=[('A', 'old', 10, 10)])
        obs = build_observations(tables, make_calibration())[0]
        self.assertEqual(obs.status, STATUS_USED)
        self.assertTrue(obs.admin_visible)

    def test_admin_absence_is_not_nonuse(self):
        # 未回应 + 已授权但无记录：不能判未使用，必须是无法确认
        tables = make_tables(
            people=[('p1', 'A', 'old', 'c1', 0.5, True)],
            contacts=[('p1', False, None)], usage=[],
            margins=[('A', 'old', 10, 10)])
        obs = build_observations(tables, make_calibration())[0]
        self.assertEqual(obs.status, STATUS_UNKNOWN)

    def test_unconsented_has_authorization_gap_and_unknown(self):
        tables = make_tables(
            people=[('p1', 'A', 'old', 'c1', 0.5, False)],
            contacts=[('p1', False, None)], usage=[],
            margins=[('A', 'old', 10, 10)])
        obs = build_observations(tables, make_calibration())[0]
        self.assertFalse(obs.admin_visible)
        self.assertEqual(obs.status, STATUS_UNKNOWN)
        self.assertIn('授权缺口', obs.audit_row()['evidence']['note'])

    def test_survey_negative_without_admin_still_negative(self):
        tables = make_tables(
            people=[('p1', 'A', 'old', 'c1', 0.5, False)],
            contacts=[('p1', True, False)], usage=[],
            margins=[('A', 'old', 10, 10)])
        obs = build_observations(tables, make_calibration())[0]
        self.assertEqual(obs.status, STATUS_NOT_USED)

    def test_conflicting_repeated_responses_kept_separate(self):
        tables = make_tables(
            people=[('p1', 'A', 'old', 'c1', 0.5, True)],
            contacts=[('p1', True, True), ('p1', True, False)],
            usage=[], margins=[('A', 'old', 10, 10)])
        obs = build_observations(tables, make_calibration())[0]
        self.assertEqual(obs.status, STATUS_CONFLICT)
        self.assertEqual(obs.contact.attempt_count, 2)

    def test_repeated_contacts_do_not_add_persons(self):
        tables = make_tables(
            people=[('p1', 'A', 'old', 'c1', 0.5, True)],
            contacts=[('p1', False, None), ('p1', False, None),
                      ('p1', True, True)],
            usage=[], margins=[('A', 'old', 10, 10)])
        observations = build_observations(tables, make_calibration())
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].contact.attempt_count, 3)
        self.assertTrue(observations[0].contact.responded)


class IdentificationTests(unittest.TestCase):
    def _build(self, calibration=None):
        cal = calibration or make_calibration()
        # 两组：A|old 完全无回应（识别不足）；A|young 有回应支撑
        tables = make_tables(
            people=[
                ('p1', 'A', 'old', 'c1', 0.5, False),
                ('p2', 'A', 'young', 'c2', 0.5, True),
                ('p3', 'A', 'young', 'c2', 0.5, True),
            ],
            contacts=[('p1', False, None),
                      ('p2', True, True), ('p3', True, False)],
            usage=[('p2', True, 1)],
            margins=[('A', 'old', 20, 25), ('A', 'young', 30, 30)])
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, missing = build_group_rate_infos(groups, group_margin_map(margins), cal)
        self.assertEqual(missing, [])
        return observations, margins, groups, infos

    def test_group_without_respondents_is_underidentified(self):
        _, _, groups, infos = self._build()
        old = next(i for i in infos if i.key == ('A', 'old'))
        self.assertFalse(old.identifiable)
        # 证据界不默认半数使用：无确定证据时为 [0,1]
        self.assertEqual((old.rate_lower, old.rate_upper), (0.0, 1.0))
        self.assertTrue(old.underidentified_reason)

    def test_mar_rate_refused_when_underidentified(self):
        _, _, groups, _ = self._build()
        rate, under = mar_overall_rate(groups)
        self.assertIsNone(rate)
        self.assertEqual(under, [('A', 'old')])

    def test_joint_range_uses_evidence_bounds_not_half_prior(self):
        _, margins, _, infos = self._build()
        result = joint_coverage_range(infos, margins)
        self.assertTrue(result['feasible'])
        # old 组证据界 [0,1]，young 组率 0.5；总体范围必须反映这种宽度
        self.assertLess(result['overall_min'], result['overall_max'])
        self.assertIn('A|old', result['underidentified_groups'])

    def test_cluster_bootstrap_keeps_clusters_intact(self):
        observations, _, groups, _ = self._build()
        boot = cluster_bootstrap(
            observations, make_calibration(),
            all_group_keys=set(groups.keys()))
        self.assertEqual(boot['clusters'], ['c1', 'c2'])
        # A|old 在名单中就无回应支撑，任何重抽样都识别不足，
        # 总体 MAR 分位数必须为 null，而不是用部分组拼出精确数字
        self.assertEqual(boot['underidentified_replicate_share'], 1.0)
        self.assertIsNone(boot['overall_mar_quantiles'])
        self.assertEqual(boot['valid_replicates'], 0)

    def test_bootstrap_quantifies_variation_when_all_groups_supported(self):
        # 两组都有回应者且分属不同抽样组：同时抽中两个集群的重抽样
        # 可给出 MAR 分位数（集群数少，部分重抽样仍会因漏组被弃用）
        cal = make_calibration()
        tables = make_tables(
            people=[('p1', 'A', 'young', 'c1', 0.5, True),
                    ('p2', 'A', 'young', 'c1', 0.5, True),
                    ('p3', 'B', 'old', 'c2', 0.25, True),
                    ('p4', 'B', 'old', 'c2', 0.25, True)],
            contacts=[('p1', True, True), ('p2', True, False),
                      ('p3', True, False), ('p4', True, False)],
            usage=[('p1', True, 1)],
            margins=[('A', 'young', 30, 30), ('B', 'old', 20, 20)])
        observations = build_observations(tables, cal)
        groups = build_groups(observations)
        boot = cluster_bootstrap(
            observations, cal, all_group_keys=set(groups.keys()))
        self.assertGreater(boot['valid_replicates'], 0)
        self.assertIsNotNone(boot['overall_mar_quantiles'])
        # 有效重抽样的分位数落在 [0,1]
        q = boot['overall_mar_quantiles']
        self.assertTrue(0.0 <= q['0.025'] <= q['0.975'] <= 1.0)


class JointRangeTests(unittest.TestCase):
    def _two_group_tables(self):
        # 两组都有回应支撑，率分别为 1.0 与 0.0，边际为区间
        return make_tables(
            people=[
                ('p1', 'A', 'young', 'c1', 0.5, True),
                ('p2', 'B', 'old', 'c2', 0.25, True),
            ],
            contacts=[('p1', True, True), ('p2', True, False)],
            usage=[('p1', True, 1)],
            margins=[('A', 'young', 30, 30), ('B', 'old', 15, 20)])

    def test_joint_range_tighter_than_independent_endpoints(self):
        cal = make_calibration()
        tables = self._two_group_tables()
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        result = joint_coverage_range(infos, margins)
        self.assertTrue(result['feasible'])
        # 该数据下联合优化与朴素端点可能重合，只断言 LP 结果在朴素界内
        self.assertLessEqual(result['overall_min'] - 1e-12,
                             result['naive_independent_endpoint_min'] + 1e-9)
        self.assertGreaterEqual(result['overall_max'] + 1e-12,
                                result['naive_independent_endpoint_max'] - 1e-9)

    def test_margin_conflict_distinguished_from_underidentification(self):
        cal = make_calibration()
        tables = self._two_group_tables()
        # 追加一个不可能满足的汇总边际：总体要求 1000 人，组边际合计最多 50
        tables['margins'].append({'id': 'm9', 'area': '*', 'age': '*',
                                  'lower': 1000, 'upper': 2000})
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        result = joint_coverage_range(infos, margins)
        self.assertFalse(result['feasible'])
        self.assertTrue(result['margin_conflict'])
        # 这是边际矛盾，不是识别不足（两组都有回应支撑）
        self.assertEqual(result['underidentified_groups'], [])

    def test_aggregate_margin_blocks_impossible_combination(self):
        cal = make_calibration()
        tables = self._two_group_tables()
        # 总体人数固定为 45：组1=30 固定，组2 只能取 15，不能取上界 20
        tables['margins'].append({'id': 'm9', 'area': '*', 'age': '*',
                                  'lower': 45, 'upper': 45})
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        result = joint_coverage_range(infos, margins, try_integer=False)
        self.assertTrue(result['feasible'])
        # LP 解出的组2 人数必须是 15 而非 20
        self.assertAlmostEqual(result['lp_min_population'][1], 15.0)


class TippingAndFollowupTests(unittest.TestCase):
    def test_tipping_reports_critical_rho_or_domain_limits(self):
        cal = make_calibration()
        tables = make_tables(
            people=[
                ('p1', 'A', 'old', 'c1', 0.5, True),
                ('p2', 'A', 'young', 'c2', 0.5, True),
            ],
            contacts=[('p1', True, True), ('p2', True, True)],
            usage=[], margins=[('A', 'old', 20, 20), ('A', 'young', 20, 20)])
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        tipping = tipping_analysis(infos, margins, 0.8)
        self.assertTrue(tipping['feasible'])
        self.assertEqual(len(tipping['findings']), 2)

    def test_followup_ranks_by_uncertainty_reduction_not_ease(self):
        cal = make_calibration()
        # u_easy：已联系多次但权重小；u_hard：只联系一次但权重大、识别不足组
        tables = make_tables(
            people=[
                ('u_easy', 'A', 'young', 'c1', 0.5, True),
                ('u_hard', 'A', 'old', 'c2', 0.1, False),
                ('p3', 'A', 'young', 'c1', 0.5, True),
            ],
            contacts=[('u_easy', False, None), ('u_easy', False, None),
                      ('u_hard', False, None),
                      ('p3', True, True)],
            usage=[],
            margins=[('A', 'young', 30, 30), ('A', 'old', 20, 25)])
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        plan = build_followup_plan(observations, infos, margins)
        self.assertTrue(plan['feasible'])
        ranked = [c['person_id'] for c in plan['candidates']]
        # 高权重的 u_hard 必须排在前面，尽管它联系次数更少、授权缺口更大
        self.assertEqual(ranked[0], 'u_hard')
        self.assertIn('u_easy', ranked)
        for c in plan['candidates']:
            self.assertIn('联系次数不参与排序', c['ranking_note'])

    def test_rho_scenario_moves_group_rate_for_identifiable_group(self):
        # 回应率0.5且有未回应者：ρ=1 给 MAR 率0.5，ρ=0.5 压低到0.375
        cal = make_calibration(rho_domain=(0.5, 2.0))
        tables = make_tables(
            people=[('r1', 'A', 'old', 'c1', 0.5, True),
                    ('r2', 'A', 'old', 'c1', 0.5, True),
                    ('u1', 'A', 'old', 'c1', 0.5, True),
                    ('u2', 'A', 'old', 'c1', 0.5, True)],
            contacts=[('r1', True, True), ('r2', True, False),
                      ('u1', False, None), ('u2', False, None)],
            usage=[('r1', True, 1)], margins=[('A', 'old', 20, 20)])
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        key = ('A', 'old')
        mar = scenario_infos(infos, {key: 1.0})[0]
        low = scenario_infos(infos, {key: 0.5})[0]
        self.assertAlmostEqual(mar.rate_lower, 0.5)
        self.assertAlmostEqual(low.rate_lower, 0.375)

    def test_sampling_and_nonresponse_ranges_are_separate(self):
        # bootstrap 只描述抽样波动；敏感性范围只描述未回应假设，两条路径独立
        cal = make_calibration()
        tables = make_tables(
            people=[('p1', 'A', 'young', 'c1', 0.5, True),
                    ('p2', 'A', 'young', 'c1', 0.5, True),
                    ('p3', 'B', 'old', 'c2', 0.25, True)],
            contacts=[('p1', True, True), ('p2', True, False),
                      ('p3', False, None)],
            usage=[('p1', True, 1)],
            margins=[('A', 'young', 30, 30), ('B', 'old', 20, 25)])
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        boot = cluster_bootstrap(observations, cal)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        joint = joint_coverage_range(infos, margins)
        # 重抽样分布只含 MAR 分位数，结构独立于敏感域
        self.assertIn('overall_mar_quantiles', boot)
        # 敏感性范围由边际与ρ域决定，不含重抽样随机性
        self.assertTrue(joint['feasible'])
        self.assertIn('solver_method', joint)

    def test_followup_checkpoints_contract_range(self):
        cal = make_calibration()
        tables = make_tables(
            people=[('p1', 'A', 'old', 'c1', 0.5, False),
                    ('p2', 'A', 'young', 'c2', 0.5, True)],
            contacts=[('p1', False, None), ('p2', True, True)],
            usage=[], margins=[('A', 'old', 20, 25), ('A', 'young', 30, 30)])
        observations = build_observations(tables, cal)
        margins = parse_margins(tables['margins'])
        groups = build_groups(observations)
        infos, _ = build_group_rate_infos(groups, group_margin_map(margins), cal)
        plan = build_followup_plan(observations, infos, margins)
        self.assertTrue(plan['checkpoints'])
        widths = [c['projected_width'] for c in plan['checkpoints']]
        self.assertEqual(widths, sorted(widths, reverse=True))


if __name__ == '__main__':
    unittest.main()
