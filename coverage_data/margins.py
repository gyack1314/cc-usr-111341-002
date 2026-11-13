"""总体边际人数区间的登记与初步校验。

margins 是总体人数闭区间，不是调查样本数。除年龄地区组区间外，允许登记
汇总边际（age 或 area 取 ``*``，两者皆 ``*`` 为总体），用于联合约束，
防止各组各取一个最坏端点拼成不可能的人群。
"""
from dataclasses import dataclass

from .io import DataError


@dataclass(frozen=True)
class Margin:
    key: tuple            # (area, age)，含 '*' 表示汇总边际
    lower: float
    upper: float
    margin_id: str

    @property
    def is_group(self):
        return '*' not in self.key


def parse_margins(rows: list[dict]):
    margins = []
    seen = set()
    for row in rows:
        lower, upper = row['lower'], row['upper']
        if not isinstance(lower, (int, float)) or not isinstance(upper, (int, float)):
            raise DataError(f"边际{row['id']}的界值不是数值")
        if lower < 0 or upper < lower:
            raise DataError(f"边际{row['id']}的区间无效")
        key = (row['area'], row['age'])
        if key in seen:
            raise DataError(f"边际{row['id']}的年龄地区组合重复")
        seen.add(key)
        margins.append(Margin(key=key, lower=float(lower), upper=float(upper),
                              margin_id=row['id']))
    return margins


def group_margin_map(margins):
    return {m.key: m for m in margins if m.is_group}


def aggregate_margins(margins):
    return [m for m in margins if not m.is_group]
