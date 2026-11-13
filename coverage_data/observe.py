"""冻结口径下的个体可观察状态构造与逐人可追问记录。

原则：
- 名单在调查前固定，重复联系不增加人数，只保留联系历史。
- 问卷证据与行政证据独立保留；行政记录仅对授权且身份可核对的居民可见，
  没有记录不代表未使用，成功记录才构成正面证据。
- 冲突与无法确认的个体保留独立状态，不做半数使用假设、不静默合并。
"""
from dataclasses import dataclass, field

from .calibration import (
    Calibration,
    STATUS_USED,
    STATUS_NOT_USED,
    STATUS_CONFLICT,
    STATUS_UNKNOWN,
)


class ObservationError(ValueError):
    pass


@dataclass
class ContactHistory:
    person: str
    attempts: list[dict] = field(default_factory=list)
    responded: bool = False
    survey_used: object = None          # True / False / None
    attempt_count: int = 0

    def add(self, row):
        if row['person'] != self.person:
            raise ObservationError('联系记录与人员不匹配')
        self.attempts.append({'attempt': row['attempt'], 'responded': row['responded'],
                              'used': row['used']})
        self.attempt_count += 1
        if row['responded']:
            self.responded = True
            # 多次回应若口径不一致，作为冲突保留而不是覆盖
            if self.survey_used is not None and self.survey_used != row['used']:
                self.survey_used = 'conflict'
            else:
                self.survey_used = row['used']


@dataclass
class PersonObservation:
    id: str
    area: str
    age: str
    cluster: str
    inclusion_probability: float
    consented: bool
    weight: float                       # 1/入样概率，设计权重
    contact: ContactHistory
    admin_visible: bool                 # 授权且身份可核对
    admin_records: list[dict]
    status: str
    evidence: dict
    margin_note: str = ''

    @property
    def group_key(self):
        return (self.area, self.age)

    def audit_row(self) -> dict:
        """逐人可追问记录：状态、联系历史与权重贡献。"""
        return {
            'id': self.id,
            'area': self.area,
            'age': self.age,
            'cluster': self.cluster,
            'inclusion_probability': self.inclusion_probability,
            'design_weight': self.weight,
            'consented': self.consented,
            'admin_visible': self.admin_visible,
            'status': self.status,
            'evidence': self.evidence,
            'contact_history': self.contact.attempts,
            'responded': self.contact.responded,
            'margin_note': self.margin_note,
        }


def _resolve_status(survey_used, admin_success_seen, admin_failure_seen,
                    visible_admin, calibration):
    """冻结状态机。返回 (状态, 证据说明)。

    - 问卷 used=true 与行政成功任一成立即正面；两者都成立且口径冲突时标记冲突。
    - 已观察未使用要求同时满足：问卷明确 used=false，且行政可见但无成功
      （冻结口径下行政无记录不等于未使用，单独行政无记录不给负面结论）。
    - 其余情形为无法确认。
    """
    survey_positive = survey_used is True
    survey_negative = survey_used is False
    survey_conflict = survey_used == 'conflict'

    if survey_conflict:
        return STATUS_CONFLICT, {'reason': '多次回应口径不一致，保留独立冲突状态'}

    if survey_positive and admin_success_seen:
        return STATUS_USED, {'sources': ['survey', 'admin_success']}
    if survey_positive:
        note = '问卷报告使用；行政侧无可见成功记录（定义或授权差异），以问卷为准'
        return STATUS_USED, {'sources': ['survey'], 'note': note}
    if admin_success_seen:
        return STATUS_USED, {'sources': ['admin_success'],
                             'note': '行政成功记录为正面证据'}

    if survey_negative and visible_admin and not admin_success_seen:
        if calibration.admin_absence_is_nonuse:
            return STATUS_NOT_USED, {'sources': ['survey', 'admin_absence']}
        return STATUS_NOT_USED, {
            'sources': ['survey'],
            'note': '问卷明确未使用；行政无成功记录不单独构成证据',
        }
    if survey_negative:
        # 问卷负面但行政不可核对：仍采信问卷负面，但记录授权缺口
        return STATUS_NOT_USED, {
            'sources': ['survey'],
            'note': '问卷明确未使用；行政记录不可见，授权缺口保留说明',
            'authorization_gap': True,
        }

    notes = []
    if not visible_admin:
        notes.append('行政记录不可见（未授权或身份不可核对）')
    elif not admin_failure_seen:
        notes.append('行政无记录不代表未使用')
    return STATUS_UNKNOWN, {'sources': [], 'note': '；'.join(notes)}


def build_observations(tables: dict, calibration: Calibration) -> list[PersonObservation]:
    """按名单构造逐人观察；名单固定，顺序以 sample 表为准。"""
    contacts_by_person: dict[str, ContactHistory] = {}
    for row in tables['contacts']:
        hist = contacts_by_person.setdefault(row['person'], ContactHistory(row['person']))
        hist.add(row)

    admin_by_person: dict[str, list[dict]] = {}
    for row in tables['usage']:
        admin_by_person.setdefault(row['person'], []).append(row)

    observations = []
    for row in tables['sample']:
        pid = row['id']
        if not 0 < row['inclusion_probability'] <= 1:
            raise ObservationError(f'{pid}的入样概率不在(0,1]')
        contact = contacts_by_person.get(pid, ContactHistory(pid))
        visible_admin = bool(row['consented'])
        admin_records = [r for r in admin_by_person.get(pid, [])
                         if r['period'] == calibration.period]
        if not visible_admin and admin_records:
            raise ObservationError(f'{pid}未授权却出现可见行政记录，身份口径矛盾')
        admin_success = any(r['success'] for r in admin_records)
        admin_failure = any(not r['success'] for r in admin_records)

        status, evidence = _resolve_status(
            contact.survey_used, admin_success, admin_failure,
            visible_admin, calibration,
        )
        if not visible_admin:
            evidence = dict(evidence)
            evidence.setdefault('note', '')
            evidence['note'] = (evidence.get('note', '') +
                                '；授权缺口：行政侧不可核对').strip('；')

        observations.append(PersonObservation(
            id=pid, area=row['area'], age=row['age'], cluster=row['cluster'],
            inclusion_probability=row['inclusion_probability'],
            consented=row['consented'],
            weight=1.0 / row['inclusion_probability'],
            contact=contact, admin_visible=visible_admin,
            admin_records=admin_records, status=status, evidence=evidence,
        ))
    return observations
