from django.urls import path

from . import views

app_name = 'board'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # 工段台账
    path('manage/sections/', views.section_list, name='section_list'),
    path('manage/sections/new/', views.section_form, name='section_new'),
    path('manage/sections/<int:pk>/edit/', views.section_form, name='section_edit'),

    # 能源类型台账
    path('manage/energies/', views.energy_list, name='energy_list'),
    path('manage/energies/new/', views.energy_form, name='energy_new'),
    path('manage/energies/<int:pk>/edit/', views.energy_form, name='energy_edit'),

    # 能耗数据录入 / 采集
    path('manage/readings/', views.reading_list, name='reading_list'),
    path('manage/readings/new/', views.reading_form, name='reading_new'),
    path('manage/readings/<int:pk>/edit/', views.reading_form, name='reading_edit'),
    path('manage/readings/batch/', views.reading_batch, name='reading_batch'),

    # 同比环比 + 工段能耗排名分析
    path('manage/analysis/', views.analysis, name='analysis'),

    # 单位产品能耗 / 能效分析
    path('manage/efficiency/', views.efficiency, name='efficiency'),

    # 超标预警
    path('manage/alerts/', views.alerts, name='alerts'),
]
