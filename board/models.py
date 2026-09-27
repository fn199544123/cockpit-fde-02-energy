"""车间能耗监控看板 —— 核心数据模型

面向工厂能耗管理:分工段、分能源类型采集能耗读数,对照定额做超标预警。
所有演示数据高度脱敏,严禁真实企业/人名/地名。
"""

from django.db import models


class Section(models.Model):
    """工段 —— 能耗采集与考核的基本单元(如「一工段 / 焦化车间」)。"""

    code = models.CharField('工段编号', max_length=32, unique=True)
    name = models.CharField('工段名称', max_length=64)
    order = models.IntegerField('展示排序', default=0)
    active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '工段'
        verbose_name_plural = '工段'
        ordering = ['order', 'code']

    def __str__(self):
        return f'{self.name}({self.code})'


class EnergyType(models.Model):
    """能源类型 —— 电 / 煤 / 蒸汽,各自计量单位不同。"""

    ELECTRICITY = 'electricity'
    COAL = 'coal'
    STEAM = 'steam'
    KIND_CHOICES = [
        (ELECTRICITY, '电'),
        (COAL, '煤'),
        (STEAM, '蒸汽'),
    ]

    kind = models.CharField('能源类型', max_length=32, choices=KIND_CHOICES, unique=True)
    name = models.CharField('名称', max_length=32)
    unit = models.CharField('计量单位', max_length=16)  # 电:kWh 煤:t 蒸汽:t
    # 折标准煤系数(tce/单位),用于统一口径汇总全厂总能耗
    coal_equiv = models.DecimalField('折标煤系数', max_digits=10, decimal_places=6, default=0)
    color = models.CharField('图表颜色', max_length=16, default='#38bdf8')
    order = models.IntegerField('展示排序', default=0)

    class Meta:
        verbose_name = '能源类型'
        verbose_name_plural = '能源类型'
        ordering = ['order', 'kind']

    def __str__(self):
        return f'{self.name}({self.unit})'


class Reading(models.Model):
    """能耗读数 —— 某工段某能源在某时刻的用量采集点。"""

    section = models.ForeignKey(Section, verbose_name='工段', on_delete=models.CASCADE,
                                related_name='readings')
    energy_type = models.ForeignKey(EnergyType, verbose_name='能源类型', on_delete=models.CASCADE,
                                    related_name='readings')
    ts = models.DateTimeField('采集时间', db_index=True)
    amount = models.DecimalField('用量', max_digits=14, decimal_places=3, default=0)
    # 该时段对应产量(用于单位产品能耗/能效分析),可为空
    output = models.DecimalField('产量', max_digits=14, decimal_places=3, null=True, blank=True)

    class Meta:
        verbose_name = '能耗读数'
        verbose_name_plural = '能耗读数'
        ordering = ['-ts']
        indexes = [
            models.Index(fields=['section', 'energy_type', 'ts']),
        ]

    def __str__(self):
        return f'{self.section.name}/{self.energy_type.name} {self.ts:%Y-%m-%d %H:%M} = {self.amount}'


class Quota(models.Model):
    """能耗定额 —— 工段×能源的周期性用量上限,用于超标预警对照。"""

    PERIOD_DAY = 'day'
    PERIOD_MONTH = 'month'
    PERIOD_CHOICES = [
        (PERIOD_DAY, '日'),
        (PERIOD_MONTH, '月'),
    ]

    section = models.ForeignKey(Section, verbose_name='工段', on_delete=models.CASCADE,
                               related_name='quotas')
    energy_type = models.ForeignKey(EnergyType, verbose_name='能源类型', on_delete=models.CASCADE,
                                    related_name='quotas')
    period = models.CharField('考核周期', max_length=16, choices=PERIOD_CHOICES, default=PERIOD_DAY)
    limit_amount = models.DecimalField('周期定额', max_digits=14, decimal_places=3, default=0)
    # 预警阈值系数:实际/定额 达到该比例即预警(默认 0.9)
    warn_ratio = models.DecimalField('预警阈值比例', max_digits=4, decimal_places=2, default=0.90)
    effective_from = models.DateField('生效日期', null=True, blank=True)

    class Meta:
        verbose_name = '能耗定额'
        verbose_name_plural = '能耗定额'
        ordering = ['section', 'energy_type', 'period']
        constraints = [
            models.UniqueConstraint(
                fields=['section', 'energy_type', 'period'],
                name='uniq_quota_section_energy_period',
            ),
        ]

    def __str__(self):
        return f'{self.section.name}/{self.energy_type.name} {self.get_period_display()}定额 {self.limit_amount}'
