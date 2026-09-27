from django.contrib import admin

from .models import Section, EnergyType, Reading, Quota


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'order', 'active')
    list_filter = ('active',)
    search_fields = ('code', 'name')


@admin.register(EnergyType)
class EnergyTypeAdmin(admin.ModelAdmin):
    list_display = ('kind', 'name', 'unit', 'coal_equiv', 'order')


@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ('ts', 'section', 'energy_type', 'amount', 'output')
    list_filter = ('section', 'energy_type')
    date_hierarchy = 'ts'


@admin.register(Quota)
class QuotaAdmin(admin.ModelAdmin):
    list_display = ('section', 'energy_type', 'period', 'limit_amount', 'warn_ratio')
    list_filter = ('period', 'section', 'energy_type')
