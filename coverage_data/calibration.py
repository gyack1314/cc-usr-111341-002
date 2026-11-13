"""冻结口径与敏感性参数登记。

所有口径在分析开始前冻结为一个带版本与摘要的不可变对象，报告必须回带
该对象的版本与摘要，保证结论可以追溯到具体口径。口径变化必须升版本并
重新登记，而不是静默修改默认值。
"""
from dataclasses import dataclass, field
import hashlib
import json

# 组键为 (地区, 年龄)
GroupKey = tuple[str, str]


@dataclass(frozen=True)
class Calibration:
    """本期分析的冻结口径。

    version                口径版本号
    period                 行政记录纳入的冻结目标时期
    service_definition     服务定义（本期至少成功使用一次）
    target_rate            达标阈值
    rho_domain             未回应者相对可比回应者使用倾向的默认敏感域(ρ下限,ρ上限)
    rho_group_overrides    个别年龄地区组的敏感域覆盖
    bootstrap_reps         集群重抽样次数
    bootstrap_seed         重抽样固定随机种子
    admin_absence_is_nonuse 行政无记录是否视为未使用；冻结为False，遵守
                           “没有记录不代表未使用”，只有成功记录构成正面证据
    """

    version: str
    period: int
    service_definition: str
    target_rate: float
    rho_domain: tuple[float, float]
    rho_group_overrides: dict[GroupKey, tuple[float, float]] = field(default_factory=dict)
    bootstrap_reps: int = 1000
    bootstrap_seed: int = 20261114
    admin_absence_is_nonuse: bool = False

    def domain_for(self, area: str, age: str) -> tuple[float, float]:
        return self.rho_group_overrides.get((area, age), self.rho_domain)

    def as_registry(self) -> dict:
        return {
            'version': self.version,
            'period': self.period,
            'service_definition': self.service_definition,
            'target_rate': self.target_rate,
            'rho_domain': list(self.rho_domain),
            'rho_group_overrides': {
                f'{area}|{age}': list(dom)
                for (area, age), dom in sorted(self.rho_group_overrides.items())
            },
            'bootstrap_reps': self.bootstrap_reps,
            'bootstrap_seed': self.bootstrap_seed,
            'admin_absence_is_nonuse': self.admin_absence_is_nonuse,
        }

    def digest(self) -> str:
        blob = json.dumps(self.as_registry(), ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(blob).hexdigest()[:16]


# 本期冻结口径。任何修改都应升 version，而不是覆盖原值。
DEFAULT_CALIBRATION = Calibration(
    version='calib-2026-11-v1',
    period=1,
    service_definition='冻结口径v1：目标时期内至少成功使用一次数字服务',
    target_rate=0.80,
    rho_domain=(0.5, 2.0),
    rho_group_overrides={},
    bootstrap_reps=1000,
    bootstrap_seed=20261114,
    admin_absence_is_nonuse=False,
)

# 个体冻结状态
STATUS_USED = '已观察使用'
STATUS_NOT_USED = '已观察未使用'
STATUS_CONFLICT = '证据冲突'
STATUS_UNKNOWN = '无法确认'

DEFINITE_STATUSES = (STATUS_USED, STATUS_NOT_USED)
