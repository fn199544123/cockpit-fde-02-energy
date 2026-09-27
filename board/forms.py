"""台账管理表单 —— 工段 / 能源类型的新增与编辑。

统一给控件挂上 .field-input class,配合深色大屏管理页样式。
所有校验错误走 Django 表单机制,在模板里逐字段渲染。
"""

from django import forms

from .models import Section, EnergyType, Reading


class _StyledModelForm(forms.ModelForm):
    """给所有可见控件统一挂样式 class,复选框单独处理。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'field-check')
            else:
                css = widget.attrs.get('class', '')
                widget.attrs['class'] = (css + ' field-input').strip()


class SectionForm(_StyledModelForm):
    class Meta:
        model = Section
        fields = ['code', 'name', 'order', 'active']
        widgets = {
            'code': forms.TextInput(attrs={'placeholder': '如 GD-01(虚构编号)'}),
            'name': forms.TextInput(attrs={'placeholder': '如 一工段 / 焦化车间'}),
            'order': forms.NumberInput(attrs={'min': 0}),
        }

    def clean_code(self):
        code = (self.cleaned_data.get('code') or '').strip()
        qs = Section.objects.filter(code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('该工段编号已存在,请换一个。')
        return code


class EnergyTypeForm(_StyledModelForm):
    class Meta:
        model = EnergyType
        fields = ['kind', 'name', 'unit', 'coal_equiv', 'color', 'order']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': '如 电 / 煤 / 蒸汽'}),
            'unit': forms.TextInput(attrs={'placeholder': '如 kWh / t'}),
            'coal_equiv': forms.NumberInput(attrs={'step': '0.000001', 'min': 0}),
            'color': forms.TextInput(attrs={'placeholder': '#38bdf8'}),
            'order': forms.NumberInput(attrs={'min': 0}),
        }

    def clean_kind(self):
        kind = self.cleaned_data.get('kind')
        qs = EnergyType.objects.filter(kind=kind)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('该能源类型已存在,同一类型只能建一条。')
        return kind


class ReadingForm(_StyledModelForm):
    """单条能耗读数录入 —— 工段 × 能源 × 时刻 的用量(可带产量)。"""

    class Meta:
        model = Reading
        fields = ['section', 'energy_type', 'ts', 'amount', 'output']
        widgets = {
            'ts': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'amount': forms.NumberInput(attrs={'step': '0.001', 'min': 0, 'placeholder': '本时段用量'}),
            'output': forms.NumberInput(attrs={'step': '0.001', 'min': 0, 'placeholder': '本时段产量(可空)'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # datetime-local 控件需要 ISO 格式,补上输入格式白名单
        self.fields['ts'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S']
        self.fields['section'].queryset = Section.objects.filter(active=True)
        self.fields['section'].empty_label = '— 选择工段 —'
        self.fields['energy_type'].empty_label = '— 选择能源 —'
        self.fields['amount'].help_text = '该工段该能源在此时刻的用量(单位随能源而定)'
        self.fields['output'].help_text = '对应产量,用于单位产品能耗分析,可留空'

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount < 0:
            raise forms.ValidationError('用量不能为负数。')
        return amount
