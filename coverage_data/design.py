"""抽样设计基准估计与整组重抽样。

- 基准（完全随机不回应，MAR）：组内回应者使用率按设计权重估计，未回应者
  假定与同组可比回应者倾向相同（ρ=1）。
- 抽样波动只由整组重抽样量化：同一人及其抽样组（cluster）永远一起出现，
  重抽样不拆开抽样组；重复联系不增加人数。
- 重抽样子样本若某组完全没有回应支撑，该次重抽样在该组标记识别不足，
  不填入半数使用或极端权重。
"""
from dataclasses import dataclass, field
import random

from .calibration import STATUS_USED, STATUS_NOT_USED, STATUS_CONFLICT


@dataclass
class GroupSample:
    key: tuple
    area: str
    age: str
    observations: list
    clusters: set = field(default_factory=set)
    # 全组设计权重合计（HT 总体规模估计的组分量）
    total_weight: float = 0.0
    # 回应者内部：使用 / 明确未使用 / 其他
    respondent_weight: float = 0.0
    respondent_used_weight: float = 0.0
    respondent_notused_weight: float = 0.0
    # 全组内确定状态与未定状态的权重
    definite_used_weight: float = 0.0
    definite_notused_weight: float = 0.0
    conflict_weight: float = 0.0
    unknown_weight: float = 0.0

    @property
    def has_respondent_support(self) -> bool:
        """是否存在可比较的回应者支撑（至少一名带使用答案的回应者）。"""
        return self.respondent_weight > 0

    def mar_rate(self):
        """组内 MAR 使用率；无回应支撑时返回 None，不做默认值。"""
        if not self.has_respondent_support:
            return None
        return self.respondent_used_weight / self.respondent_weight


def build_groups(observations) -> dict:
    groups = {}
    for obs in observations:
        g = groups.setdefault(obs.group_key, GroupSample(
            key=obs.group_key, area=obs.area, age=obs.age, observations=[]))
        g.observations.append(obs)
        g.clusters.add(obs.cluster)
        w = obs.weight
        g.total_weight += w
        if obs.contact.responded and obs.contact.survey_used in (True, False):
            g.respondent_weight += w
            if obs.contact.survey_used is True:
                g.respondent_used_weight += w
            else:
                g.respondent_notused_weight += w
        if obs.status == STATUS_USED:
            g.definite_used_weight += w
        elif obs.status == STATUS_NOT_USED:
            g.definite_notused_weight += w
        elif obs.status == STATUS_CONFLICT:
            g.conflict_weight += w
        else:
            g.unknown_weight += w
    return dict(sorted(groups.items()))


def _replicate_groups(observations, cluster_draws):
    """按集群抽取结果构造带重复次数的组结构。"""
    multiplicity = {}
    for cluster in cluster_draws:
        multiplicity[cluster] = multiplicity.get(cluster, 0) + 1

    groups = {}
    total_w = 0.0
    for obs in observations:
        m = multiplicity.get(obs.cluster, 0)
        if m == 0:
            continue
        w = obs.weight * m
        total_w += w
        g = groups.setdefault(obs.group_key, GroupSample(
            key=obs.group_key, area=obs.area, age=obs.age, observations=[]))
        g.clusters.add(obs.cluster)
        g.total_weight += w
        if obs.contact.responded and obs.contact.survey_used in (True, False):
            g.respondent_weight += w
            if obs.contact.survey_used is True:
                g.respondent_used_weight += w
            else:
                g.respondent_notused_weight += w
    return groups, total_w


def mar_overall_rate(groups) -> tuple:
    """MAR 总体率。

    返回 (率或None, 识别不足的组键列表)。存在识别不足组时率为 None，
    调用方应改走敏感性模块的证据界路径，而不是在此填充默认使用率。
    """
    underidentified = [g.key for g in groups.values() if not g.has_respondent_support]
    if underidentified:
        return None, underidentified
    users = sum(g.mar_rate() * g.total_weight for g in groups.values())
    total = sum(g.total_weight for g in groups.values())
    return users / total, []


def mar_point_estimate(groups):
    """仅由可识别组构成的 MAR 点估计，附权重覆盖说明。

    识别不足组不参与点估计，也不按半数使用填充；返回其权重占比，
    让发布者看见点估计遗漏了多大比例的总体。
    """
    covered_weight = sum(g.total_weight for g in groups.values()
                         if g.has_respondent_support)
    total_weight = sum(g.total_weight for g in groups.values())
    users = sum((g.mar_rate() or 0.0) * g.total_weight for g in groups.values())
    under = [f'{a}|{ag}' for (a, ag), g in groups.items()
             if not g.has_respondent_support]
    return {
        'rate_on_covered_weight': users / covered_weight if covered_weight else None,
        'covered_weight_share': covered_weight / total_weight if total_weight else None,
        'excluded_underidentified_groups': under,
        'note': '点估计只覆盖有回应支撑的权重；识别不足组不填充，其影响见敏感性证据界',
    }


def cluster_bootstrap(observations, calibration, all_group_keys=None) -> dict:
    """整组重抽样分布，只描述抽样波动，不掺入未回应假设变化。

    all_group_keys 为完整名单中的全部年龄地区组。重抽样若漏掉某个组，
    该组同样视为无回应支撑（没有任何观察可推断），整次重抽样弃用。
    """
    clusters = sorted({obs.cluster for obs in observations})
    all_group_keys = set(all_group_keys or ())
    rng = random.Random(calibration.bootstrap_seed)
    rep_records = []
    group_rates = {}
    overall_rates = []
    under_rep_count = 0

    for _ in range(calibration.bootstrap_reps):
        draws = [rng.choice(clusters) for _ in range(len(clusters))]
        groups, total_w = _replicate_groups(observations, draws)
        if total_w == 0:
            continue
        present_keys = set(groups)
        under_keys = set(
            f'{a}|{ag}' for (a, ag), g in groups.items()
            if not g.has_respondent_support)
        # 重抽样中整组缺失同样计入识别不足
        missing = {f'{a}|{ag}' for (a, ag) in all_group_keys
                   if (a, ag) not in present_keys}
        under_keys |= missing
        under_keys = tuple(sorted(under_keys))
        rep_records.append({'underidentified_groups': list(under_keys)})
        if under_keys:
            under_rep_count += 1
            continue
        for key, g in groups.items():
            group_rates.setdefault(key, []).append(g.mar_rate())
        overall = sum(g.mar_rate() * g.total_weight for g in groups.values())
        overall_rates.append(overall / total_w)

    return {
        'method': '整组等概率有放回重抽样，同一抽样组不拆开',
        'clusters': clusters,
        'replicates': calibration.bootstrap_reps,
        'seed': calibration.bootstrap_seed,
        'overall_mar_quantiles': _quantiles(overall_rates),
        'group_mar_quantiles': {
            f'{a}|{ag}': _quantiles(vals) for (a, ag), vals in sorted(group_rates.items())
        },
        'valid_replicates': len(overall_rates),
        'underidentified_replicate_share': (
            under_rep_count / calibration.bootstrap_reps if calibration.bootstrap_reps else 0.0),
        'note': ('整组重抽样只量化抽样波动；任一组在该次重抽样中无回应支撑即整次弃用，'
                 '不填充默认使用率、不拼部分组。若总体分位数为 null，说明所有重抽样都'
                 '至少含一个识别不足组，抽样波动无法在 MAR 点估计层面单独识别，'
                 '其不确定性已并入未回应敏感性证据界。'),
    }


def _quantiles(values, probs=(0.025, 0.5, 0.975)):
    if not values:
        return None
    vals = sorted(values)
    out = {}
    for p in probs:
        rank = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
        out[str(p)] = vals[rank]
    return out
