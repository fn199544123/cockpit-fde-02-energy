"""灌入高度脱敏的演示数据(工段 / 能源类型 / 定额 / 近 62 天 + 去年同期读数)。

用法(项目根目录):
    .venv/bin/python scripts/seed_demo.py

铁律:严禁真实企业/人名/地名,一律用「某企业 / 一工段 / 焦化车间」等占位、编号用虚构值。
可重复执行(先清空 board 数据再灌),便于反复演示。

数据规模(让看板与各页面饱满好看):
  - 8 个工段,横跨「焦化车间」「回收车间」两个虚构车间;
  - 3 类能源(电 / 煤 / 蒸汽),各带折标煤系数与图表色;
  - 日 + 月 两种考核定额(8×3×2 = 48 项),预警比例经工段错开,
    使超标预警页出现「超标 / 预警 / 正常」的丰富分布;
  - 逐时读数覆盖「当期近 62 天」与「去年同期 62 天」两个窗口,
    当期温和上升、去年基数偏低,使同比 / 环比(含月至今对齐)均有真实对照。
"""

import os
import sys
import random
from datetime import timedelta
from decimal import Decimal

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'energy.settings')
django.setup()

from django.utils import timezone  # noqa: E402

from board.models import Section, EnergyType, Reading, Quota  # noqa: E402


# 脱敏工段(某企业下两个虚构车间的工段;编号一律虚构)
# (编号, 名称, 排序)
SECTIONS = [
    ('DS-01', '一工段·备煤(焦化车间)', 1),
    ('DS-02', '二工段·炼焦(焦化车间)', 2),
    ('DS-03', '三工段·熄焦(焦化车间)', 3),
    ('DS-04', '四工段·化产(焦化车间)', 4),
    ('DS-05', '五工段·公辅(焦化车间)', 5),
    ('HS-01', '一工段·脱硫(回收车间)', 6),
    ('HS-02', '二工段·蒸氨(回收车间)', 7),
    ('HS-03', '三工段·粗苯(回收车间)', 8),
]

# 能源类型:类型 / 名称 / 单位 / 折标煤系数(tce/单位) / 颜色 / 排序
ENERGY_TYPES = [
    (EnergyType.ELECTRICITY, '电', 'kWh', Decimal('0.0001229'), '#38bdf8', 1),
    (EnergyType.COAL, '煤', 't', Decimal('0.7143'), '#f59e0b', 2),
    (EnergyType.STEAM, '蒸汽', 't', Decimal('0.0929'), '#34d399', 3),
]

# 各能源在单个工段的日基准用量(虚构量级),用于生成读数与定额
DAILY_BASE = {
    EnergyType.ELECTRICITY: 12000,   # kWh/日
    EnergyType.COAL: 180,            # t/日
    EnergyType.STEAM: 90,            # t/日
}


def reset():
    Reading.objects.all().delete()
    Quota.objects.all().delete()
    Section.objects.all().delete()
    EnergyType.objects.all().delete()


def seed():
    reset()

    ets = {}
    for kind, name, unit, ce, color, order in ENERGY_TYPES:
        ets[kind] = EnergyType.objects.create(
            kind=kind, name=name, unit=unit, coal_equiv=ce, color=color, order=order,
        )

    sections = [Section.objects.create(code=c, name=n, order=o) for c, n, o in SECTIONS]

    now = timezone.localtime().replace(minute=0, second=0, microsecond=0)
    today = now.date()
    cur_hour = now.hour
    # 定额自本月 1 号生效
    eff_from = today.replace(day=1)

    # 定额:日 + 月两套。
    # 实际日均 ≈ base*sec_factor(噪声均值≈1.05、班次均值≈1.0 相抵),
    # 定额留边越紧则越易超标——按工段错开边际系数,制造丰富的超标/预警/正常分布。
    day_margins = [0.98, 1.02, 1.06, 1.10, 1.14, 1.00, 1.05, 1.12]
    for idx, sec in enumerate(sections):
        sec_factor = 0.7 + idx * 0.15
        day_margin = day_margins[idx % len(day_margins)]
        for kind, et in ets.items():
            day_base = DAILY_BASE[kind] * sec_factor
            day_limit = Decimal(str(round(day_base * day_margin, 3)))
            Quota.objects.create(
                section=sec, energy_type=et, period=Quota.PERIOD_DAY,
                limit_amount=day_limit, warn_ratio=Decimal('0.90'),
                effective_from=eff_from,
            )
            # 月定额:按 ~30.5 天折算,边际略宽于日定额
            month_limit = Decimal(str(round(day_base * 30.5 * (day_margin + 0.03), 3)))
            Quota.objects.create(
                section=sec, energy_type=et, period=Quota.PERIOD_MONTH,
                limit_amount=month_limit, warn_ratio=Decimal('0.90'),
                effective_from=eff_from,
            )

    # 逐时读数:同时生成「当期近 62 天」与「去年同期 62 天」两个窗口,
    # 以便同比(去年同期)/ 环比(上一周期)分析都有真实对照数据。
    DAYS = 62  # 覆盖上一整月,月「同比/环比·月至今对齐」也有对照数据
    rng = random.Random(20260923)  # 固定种子,演示可复现

    def _shift_year(d, delta):
        """安全地把日期平移 delta 年(闰日 2-29 回退到 2-28)。"""
        try:
            return d.replace(year=d.year + delta)
        except ValueError:
            return d.replace(year=d.year + delta, day=28)

    def _aware(day, hour):
        return timezone.make_aware(
            timezone.datetime.combine(day, timezone.datetime.min.time())
        ) + timedelta(hours=hour)

    readings = []
    for idx, sec in enumerate(sections):
        sec_factor = 0.7 + idx * 0.15
        for kind, et in ets.items():
            hourly_base = DAILY_BASE[kind] * sec_factor / 24.0
            for offset in range(DAYS):
                cur_day = today - timedelta(days=offset)
                ly_day = _shift_year(cur_day, -1)
                # 越靠近今天量级略高,制造温和上升趋势 -> 环比可见波动
                trend = 1.0 - offset * 0.0035
                last_hour = cur_hour if offset == 0 else 23
                for hour in range(0, last_hour + 1):
                    # 昼高夜低的班次波动 + 随机噪声
                    shift = 1.15 if 8 <= hour < 20 else 0.85
                    # 当期
                    noise = rng.uniform(0.82, 1.28)
                    amount = Decimal(str(round(hourly_base * shift * trend * noise, 3)))
                    output = Decimal(str(round(hourly_base * shift * trend * rng.uniform(0.9, 1.1) / 3.0, 3)))
                    readings.append(Reading(
                        section=sec, energy_type=et, ts=_aware(cur_day, hour),
                        amount=amount, output=output,
                    ))
                    # 去年同期:整体基数低约 12%,让同比呈上升
                    ly_noise = rng.uniform(0.82, 1.28)
                    ly_amount = Decimal(str(round(hourly_base * shift * 0.88 * ly_noise, 3)))
                    ly_output = Decimal(str(round(hourly_base * shift * 0.88 * rng.uniform(0.9, 1.1) / 3.0, 3)))
                    readings.append(Reading(
                        section=sec, energy_type=et, ts=_aware(ly_day, hour),
                        amount=ly_amount, output=ly_output,
                    ))

    Reading.objects.bulk_create(readings, batch_size=1000)

    print(f'工段 {Section.objects.count()} 个')
    print(f'能源类型 {EnergyType.objects.count()} 类')
    print(f'定额 {Quota.objects.count()} 项(日+月)')
    print(f'读数 {Reading.objects.count()} 条')


if __name__ == '__main__':
    seed()
    print('演示数据灌入完成。')
