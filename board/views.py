import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import SectionForm, EnergyTypeForm, ReadingForm
from .models import Section, EnergyType, Reading, Quota


TREND_DAYS = 14  # 大屏能耗趋势折线回看天数


def dashboard(request):
    """大屏看板入口页 —— 全厂能耗可视化闭环:

    全厂总能耗(折标煤)+ 环比 → 各工段能耗排名 → 能源结构占比(环形)
    → 近 14 日能耗趋势(折线)→ 超标 / 预警红灯墙。数据全部来自真实读数库。
    """
    ref = _latest_data_date()
    ev = _evaluate_quotas(ref)
    alert_rows = [r for r in ev['rows'] if r['status'] != 'normal']

    energies = list(EnergyType.objects.all())
    sections = list(Section.objects.filter(active=True))
    ce = {et.pk: Decimal(et.coal_equiv) for et in energies}

    cur = _agg_period(ref, ref + timedelta(days=1)) if ref else {}
    prev = _agg_period(ref - timedelta(days=1), ref) if ref else {}

    def tce_of(bucket, sec_id=None, et_id=None):
        """在某周期聚合桶上求折标煤合计(可按工段 / 能源过滤)。"""
        t = Decimal(0)
        for (s, e), amt in bucket.items():
            if sec_id is not None and s != sec_id:
                continue
            if et_id is not None and e != et_id:
                continue
            t += amt * ce.get(e, Decimal(0))
        return t

    total_tce = tce_of(cur)
    prev_tce = tce_of(prev)

    # ---- 能源结构占比(环形图 conic-gradient) ----
    struct = []
    for et in energies:
        struct.append({'et': et, 'tce': tce_of(cur, et_id=et.pk)})
    stot = sum((s['tce'] for s in struct), Decimal(0)) or Decimal(1)
    acc, stops = 0.0, []
    for s in struct:
        pct = float(s['tce'] / stot * 100)
        start, acc = acc, acc + pct
        stops.append(f'{s["et"].color} {start:.3f}% {acc:.3f}%')
        s['pct'] = pct
        s['pct_disp'] = f'{pct:.1f}'
        s['tce_disp'] = _fnum(s['tce'])
    donut_gradient = ', '.join(stops) if struct else 'var(--line) 0% 100%'

    # ---- 各工段能耗排名(当日折标煤) ----
    sec_rows = []
    for sec in sections:
        sec_rows.append({'section': sec, 'tce': tce_of(cur, sec_id=sec.pk)})
    sec_rows.sort(key=lambda r: r['tce'], reverse=True)
    max_sec = max((r['tce'] for r in sec_rows), default=Decimal(0)) or Decimal(1)
    for i, r in enumerate(sec_rows, 1):
        r['rank'] = i
        r['bar'] = float(r['tce'] / max_sec * 100)
        r['tce_disp'] = _fnum(r['tce'])
        r['share_disp'] = f"{float(r['tce'] / (total_tce or Decimal(1)) * 100):.1f}"

    # ---- 近 N 日能耗趋势(SVG 折线,坐标在服务端算好) ----
    trend = []
    if ref:
        for i in range(TREND_DAYS - 1, -1, -1):
            d = ref - timedelta(days=i)
            trend.append({'date': d, 'tce': tce_of(_agg_period(d, d + timedelta(days=1)))})
    trend_svg = _build_trend_svg(trend)

    # ---- 供前端手写 SVG 直接绘制的原始序列(经 json_script 注入,不连任何 CDN) ----
    chart_json = {
        'unit': 'tce',
        # 各工段能耗对比(柱状):按当日折标煤降序,含名次
        'sections': [
            {'name': r['section'].name, 'tce': round(float(r['tce']), 1),
             'rank': r['rank'], 'share': float(r['share_disp'])}
            for r in sec_rows
        ],
        # 电 / 煤 / 蒸汽 能源结构占比(环图)
        'struct': [
            {'name': s['et'].name, 'color': s['et'].color,
             'tce': round(float(s['tce']), 1), 'pct': round(s['pct'], 1)}
            for s in struct if s['tce'] > 0
        ],
        # 近 N 日能耗趋势(平滑曲线)
        'trend': [
            {'d': f'{t["date"]:%m-%d}', 'v': round(float(t['tce']), 1)}
            for t in trend
        ],
    }

    ctx = {
        'section_count': len(sections),
        'energy_types': energies,
        'reading_count': Reading.objects.count(),
        'quota_count': Quota.objects.count(),
        'ref': f'{ref:%Y-%m-%d}' if ref else '—',
        # 全厂总能耗
        'total_tce_disp': _fnum(total_tce, 1),
        'mom_d': _delta(_pct(total_tce, prev_tce)),
        'prev_tce_disp': _fnum(prev_tce, 1),
        # 结构 / 排名 / 趋势
        'struct': struct,
        'donut_gradient': donut_gradient,
        'struct_total_disp': _fnum(stot if struct else Decimal(0), 1),
        'sec_rows': sec_rows,
        'trend': trend_svg,
        # 超标预警
        'over_count': ev['over_count'],
        'warn_count': ev['warn_count'],
        'normal_count': ev['normal_count'],
        'quota_total': ev['total'],
        'alert_rows': alert_rows[:6],   # 大屏红灯墙展示前 6 条
        'alert_more': max(len(alert_rows) - 6, 0),
        # 手写 SVG 图表原始数据
        'chart_json': chart_json,
    }
    return render(request, 'board/dashboard.html', ctx)


def _build_trend_svg(trend):
    """把每日折标煤序列换算成 SVG 折线 / 面积坐标 + 网格,供大屏无 JS 直接绘制。"""
    W, H = 1000.0, 240.0
    pad_l, pad_r, pad_t, pad_b = 12.0, 12.0, 18.0, 26.0
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    n = len(trend)
    vals = [float(t['tce']) for t in trend]
    vmax = max(vals) if vals else 1.0
    vmax = vmax if vmax > 0 else 1.0

    pts = []
    for idx, t in enumerate(trend):
        x = pad_l + (plot_w * (idx / (n - 1)) if n > 1 else plot_w / 2)
        y = pad_t + plot_h * (1 - float(t['tce']) / vmax)
        t['x'], t['y'] = round(x, 1), round(y, 1)
        t['date_disp'] = f'{t["date"]:%m-%d}'
        t['tce_disp'] = _fnum(t['tce'], 0)
        pts.append(f'{x:.1f},{y:.1f}')

    line_points = ' '.join(pts)
    area = ''
    if pts:
        area = (f'{pad_l:.1f},{H - pad_b:.1f} ' + line_points +
                f' {(pad_l + plot_w):.1f},{H - pad_b:.1f}')

    # 3 条水平网格 + 数值刻度
    grids = []
    for frac in (0.0, 0.5, 1.0):
        y = pad_t + plot_h * frac
        grids.append({'y': round(y, 1), 'val': _fnum(vmax * (1 - frac), 0)})

    peak = max(trend, key=lambda t: t['tce']) if trend else None

    return {
        'w': W, 'h': H,
        'baseline': round(H - pad_b, 1),
        'x0': pad_l, 'x1': round(pad_l + plot_w, 1),
        'points': line_points,
        'area': area,
        'nodes': trend,
        'grids': grids,
        'peak': peak,
        'has_data': bool(trend),
    }


# ---------------------------------------------------------------------------
# 超标预警(实际 vs 定额)
# ---------------------------------------------------------------------------

def alerts(request):
    """超标预警清单:各工段各能源实际用量对照定额,超标 / 预警自动标红。

    - 每条定额按其考核周期(日 / 月)在参考日所属周期内比对;
    - 进行中的周期(如今天)按已过时间对定额做「进度折算」(进度对标),避免半天 vs 全天失真;
    - 状态:实际/折算定额 ≥ 100% 超标;≥ 预警阈值(默认 90%)预警;否则正常。
    """
    ref = _parse_date(request.GET.get('ref') or '') or _latest_data_date() or timezone.localtime().date()
    show = request.GET.get('show') or 'alert'   # alert=仅告警 / all=全部
    if show not in ('alert', 'all'):
        show = 'alert'

    ev = _evaluate_quotas(ref)
    rows = ev['rows']
    if show == 'alert':
        display_rows = [r for r in rows if r['status'] != 'normal']
    else:
        display_rows = rows

    ctx = {
        'ref': f'{ref:%Y-%m-%d}',
        'today': f'{timezone.localtime().date():%Y-%m-%d}',
        'show': show,
        'rows': display_rows,
        'over_count': ev['over_count'],
        'warn_count': ev['warn_count'],
        'normal_count': ev['normal_count'],
        'quota_total': ev['total'],
        'alert_total': ev['over_count'] + ev['warn_count'],
    }
    return render(request, 'board/alerts.html', ctx)


def _latest_data_date():
    """最近有读数的日期(演示中今天可能只采到当前整点,取最近数据日保证页面有内容)。"""
    latest = Reading.objects.aggregate(m=Max('ts'))['m']
    if latest:
        return timezone.localtime(latest).date()
    return None


def _evaluate_quotas(ref_date):
    """把所有定额按各自周期在 ref_date 所属周期内与实际用量比对,返回排序后的告警行与计数。"""
    if ref_date is None:
        return {'rows': [], 'over_count': 0, 'warn_count': 0, 'normal_count': 0, 'total': 0}

    now = timezone.localtime()

    # 预先按周期(日 / 月)各聚合一次实际用量,避免逐条定额查库
    windows = {}
    for period in (Quota.PERIOD_DAY, Quota.PERIOD_MONTH):
        if period == Quota.PERIOD_MONTH:
            s = ref_date.replace(day=1)
            e = _add_months(s, 1)
        else:
            s = ref_date
            e = ref_date + timedelta(days=1)
        agg = _agg_period(s, e)
        start = timezone.make_aware(datetime.combine(s, datetime.min.time()))
        end_full = timezone.make_aware(datetime.combine(e, datetime.min.time()))
        if now >= end_full:            # 已结束的完整周期
            frac, in_progress = 1.0, False
        elif now <= start:             # 尚未开始(未来周期)
            frac, in_progress = 0.0, True
        else:                          # 进行中:按已过时间折算
            frac = (now - start).total_seconds() / (end_full - start).total_seconds()
            in_progress = True
        windows[period] = {'agg': agg, 'frac': frac, 'in_progress': in_progress, 'start': s}

    quotas = (Quota.objects.select_related('section', 'energy_type')
              .order_by('section__order', 'energy_type__order'))

    rows = []
    over_count = warn_count = normal_count = 0
    for q in quotas:
        w = windows.get(q.period)
        if w is None:
            continue
        actual = w['agg'].get((q.section_id, q.energy_type_id), Decimal(0))
        limit = Decimal(q.limit_amount or 0)
        warn_ratio = Decimal(q.warn_ratio or 0)
        frac = w['frac']
        # 进度折算定额:进行中的周期按已过时间比例缩放,完整周期用全额
        eff_limit = (limit * Decimal(str(frac))) if w['in_progress'] else limit

        if eff_limit > 0:
            ratio = float(actual) / float(eff_limit)
        else:
            ratio = None

        if ratio is None:
            status, cls = 'normal', 'ok'
        elif ratio >= 1.0:
            status, cls = 'over', 'danger'
            over_count += 1
        elif ratio >= float(warn_ratio):
            status, cls = 'warn', 'warn'
            warn_count += 1
        else:
            status, cls = 'normal', 'ok'
        if status == 'normal':
            normal_count += 1

        over_by = actual - eff_limit  # 超出折算定额的量(正=超)
        warn_line = eff_limit * warn_ratio

        rows.append({
            'quota': q,
            'section': q.section,
            'et': q.energy_type,
            'period_disp': q.get_period_display(),
            'in_progress': w['in_progress'],
            'frac_pct': round(frac * 100),
            'actual': actual,
            'actual_disp': _fnum(actual, 1),
            'limit': limit,
            'limit_disp': _fnum(limit, 1),
            'eff_limit_disp': _fnum(eff_limit, 1),
            'warn_line_disp': _fnum(warn_line, 1),
            'ratio': ratio,
            'ratio_pct': (f'{ratio * 100:.1f}' if ratio is not None else '—'),
            'ratio_bar': (min(ratio * 100, 130.0) if ratio is not None else 0),  # 进度条封顶 130%
            'over_by_disp': (_fnum(over_by, 1) if over_by > 0 else None),
            'status': status,
            'cls': cls,
        })

    # 排序:超标 > 预警 > 正常;组内按比例降序
    order = {'over': 0, 'warn': 1, 'normal': 2}
    rows.sort(key=lambda r: (order[r['status']], -(r['ratio'] or 0)))

    return {
        'rows': rows,
        'over_count': over_count,
        'warn_count': warn_count,
        'normal_count': normal_count,
        'total': len(rows),
    }


# ---------------------------------------------------------------------------
# 工段台账
# ---------------------------------------------------------------------------

def section_list(request):
    sections = (Section.objects.all()
                .annotate(reading_num=Count('readings', distinct=True),
                          quota_num=Count('quotas', distinct=True)))
    ctx = {
        'sections': sections,
        'active_num': sections.filter(active=True).count(),
        'total_num': sections.count(),
    }
    return render(request, 'board/section_list.html', ctx)


def section_form(request, pk=None):
    instance = get_object_or_404(Section, pk=pk) if pk else None
    if request.method == 'POST':
        form = SectionForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f'工段「{obj.name}」已{"更新" if instance else "新增"}。')
            return redirect('board:section_list')
    else:
        form = SectionForm(instance=instance)
    ctx = {
        'form': form,
        'instance': instance,
        'title': '编辑工段' if instance else '新增工段',
        'cancel_url': reverse('board:section_list'),
        'subtitle': '工段是能耗采集与考核的基本单元',
    }
    return render(request, 'board/section_form.html', ctx)


# ---------------------------------------------------------------------------
# 能源类型台账
# ---------------------------------------------------------------------------

def energy_list(request):
    energies = (EnergyType.objects.all()
                .annotate(reading_num=Count('readings', distinct=True),
                          quota_num=Count('quotas', distinct=True)))
    ctx = {
        'energies': energies,
        'total_num': energies.count(),
    }
    return render(request, 'board/energy_list.html', ctx)


def energy_form(request, pk=None):
    instance = get_object_or_404(EnergyType, pk=pk) if pk else None
    if request.method == 'POST':
        form = EnergyTypeForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f'能源类型「{obj.name}」已{"更新" if instance else "新增"}。')
            return redirect('board:energy_list')
    else:
        form = EnergyTypeForm(instance=instance)
    ctx = {
        'form': form,
        'instance': instance,
        'title': '编辑能源类型' if instance else '新增能源类型',
        'cancel_url': reverse('board:energy_list'),
        'subtitle': '电 / 煤 / 蒸汽,折标煤系数用于统一口径汇总',
    }
    return render(request, 'board/energy_form.html', ctx)


# ---------------------------------------------------------------------------
# 能耗数据录入 / 采集(Reading)
# ---------------------------------------------------------------------------

STEP_CHOICES = [1, 2, 3, 4, 6, 8, 12, 24]  # 批量录入时段步长(小时)


def reading_list(request):
    """能耗读数台账:按工段 / 能源 / 日期区间筛选,分页展示最近采集记录。"""
    qs = Reading.objects.select_related('section', 'energy_type')

    sec_id = request.GET.get('section') or ''
    et_id = request.GET.get('energy') or ''
    date_from = request.GET.get('from') or ''
    date_to = request.GET.get('to') or ''

    if sec_id:
        qs = qs.filter(section_id=sec_id)
    if et_id:
        qs = qs.filter(energy_type_id=et_id)
    if date_from:
        d = _parse_date(date_from)
        if d:
            qs = qs.filter(ts__gte=timezone.make_aware(datetime.combine(d, datetime.min.time())))
    if date_to:
        d = _parse_date(date_to)
        if d:
            end = timezone.make_aware(datetime.combine(d, datetime.min.time())) + timedelta(days=1)
            qs = qs.filter(ts__lt=end)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page') or 1)

    # 保留筛选条件用于分页链接
    params = request.GET.copy()
    params.pop('page', None)

    ctx = {
        'page_obj': page,
        'total_num': paginator.count,
        'sections': Section.objects.all(),
        'energies': EnergyType.objects.all(),
        'f_section': sec_id,
        'f_energy': et_id,
        'f_from': date_from,
        'f_to': date_to,
        'querystring': params.urlencode(),
    }
    return render(request, 'board/reading_list.html', ctx)


def reading_form(request, pk=None):
    """单条能耗读数录入 / 编辑。"""
    instance = get_object_or_404(Reading, pk=pk) if pk else None
    if request.method == 'POST':
        form = ReadingForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            messages.success(
                request,
                f'读数「{obj.section.name} / {obj.energy_type.name} '
                f'{timezone.localtime(obj.ts):%m-%d %H:%M}」已{"更新" if instance else "录入"}。')
            if not instance and '_addanother' in request.POST:
                return redirect('board:reading_new')
            return redirect('board:reading_list')
    else:
        initial = {}
        if not instance:
            # 默认填充为当前整点,方便快速录入
            now = timezone.localtime().replace(minute=0, second=0, microsecond=0)
            initial['ts'] = now.strftime('%Y-%m-%dT%H:%M')
        form = ReadingForm(instance=instance, initial=initial)
    ctx = {
        'form': form,
        'instance': instance,
        'title': '编辑读数' if instance else '单条录入读数',
        'cancel_url': reverse('board:reading_list'),
        'subtitle': '工段 × 能源 × 时刻 采集用量;批量录入请用「快速批量录入」',
    }
    return render(request, 'board/reading_form.html', ctx)


def reading_batch(request):
    """快速批量录入:选定工段 + 日期 + 时段步长,一张表格一次录入多时段 × 多能源。

    行 = 时段(按步长切分一天),列 = 各能源用量 + 该时段产量。
    已有读数会预填入表格,提交时按 (工段, 能源, 时刻) 做 update_or_create 更新或新增。
    """
    sections = Section.objects.filter(active=True)
    energies = list(EnergyType.objects.all())

    # 读取选择(GET 用于切换,POST 用于提交)
    src = request.POST if request.method == 'POST' else request.GET
    sec_id = src.get('section') or (str(sections.first().pk) if sections else '')
    date_str = src.get('date') or timezone.localtime().strftime('%Y-%m-%d')
    try:
        step = int(src.get('step') or 1)
    except (TypeError, ValueError):
        step = 1
    if step not in STEP_CHOICES:
        step = 1

    the_date = _parse_date(date_str) or timezone.localtime().date()
    section = sections.filter(pk=sec_id).first() if sec_id else None

    # 生成时段列表:0..24 按步长
    slots = list(range(0, 24, step))

    if request.method == 'POST' and section:
        saved, cleared = 0, 0
        for hour in slots:
            ts = timezone.make_aware(datetime.combine(the_date, datetime.min.time())) + timedelta(hours=hour)
            # 该时段产量(所有能源共享写入)
            out_val = _to_decimal(request.POST.get(f'out_{hour}'))
            for et in energies:
                raw = request.POST.get(f'amt_{et.pk}_{hour}')
                amt = _to_decimal(raw)
                exists = Reading.objects.filter(section=section, energy_type=et, ts=ts).first()
                if amt is None:
                    # 空值:若原有记录则视为删除该点
                    if exists:
                        exists.delete()
                        cleared += 1
                    continue
                Reading.objects.update_or_create(
                    section=section, energy_type=et, ts=ts,
                    defaults={'amount': amt, 'output': out_val},
                )
                saved += 1
        msg = f'{section.name} · {the_date:%Y-%m-%d} 批量录入完成:保存 {saved} 条'
        if cleared:
            msg += f',清除 {cleared} 条空值'
        messages.success(request, msg + '。')
        base = reverse('board:reading_batch')
        return redirect(f'{base}?section={section.pk}&date={the_date:%Y-%m-%d}&step={step}')

    # 预填:取该工段该日已有读数 -> {(et_id, hour): reading}
    grid = {}
    if section:
        day_start = timezone.make_aware(datetime.combine(the_date, datetime.min.time()))
        day_end = day_start + timedelta(days=1)
        existing = Reading.objects.filter(section=section, ts__gte=day_start, ts__lt=day_end)
        for r in existing:
            hour = timezone.localtime(r.ts).hour
            grid[(r.energy_type_id, hour)] = r

    # 组装表格行数据
    rows = []
    for hour in slots:
        cells = []
        out_display = ''
        for et in energies:
            r = grid.get((et.pk, hour))
            if r is not None:
                cells.append({'et': et, 'value': _fmt(r.amount)})
                if r.output is not None:
                    out_display = _fmt(r.output)
            else:
                cells.append({'et': et, 'value': ''})
        rows.append({
            'hour': hour,
            'label': f'{hour:02d}:00',
            'end_label': f'{min(hour + step, 24):02d}:00',
            'cells': cells,
            'out_value': out_display,
        })

    ctx = {
        'sections': sections,
        'energies': energies,
        'sec_id': str(section.pk) if section else '',
        'date_str': f'{the_date:%Y-%m-%d}',
        'step': step,
        'step_choices': STEP_CHOICES,
        'rows': rows,
        'filled_num': len(grid),
    }
    return render(request, 'board/reading_batch.html', ctx)


# ---------------------------------------------------------------------------
# 同比 / 环比 与 能耗排名分析
# ---------------------------------------------------------------------------

GRAN_CHOICES = [('day', '按日'), ('month', '按月')]


def analysis(request):
    """同比环比 + 工段能耗排名。

    - 当期:用户选定的日 / 月;
    - 环比:紧邻的上一同长度周期(上一日 / 上一月);
    - 同比:去年同期(去年同一日 / 去年同一月)。
    统一用折标煤(tce)口径跨能源汇总,分能源保留原始单位;工段按当期折标煤总量排名。
    """
    gran = request.GET.get('gran')
    if gran not in ('day', 'month'):
        gran = 'day'

    today = timezone.localtime().date()
    ref = _parse_date(request.GET.get('ref') or '') or today

    # 三个周期的 [start, end) 日期边界
    mtd_days = None  # 月粒度且为进行中的当月时,用「月至今」对齐三期,避免整月 vs 半月失真
    if gran == 'month':
        ms = ref.replace(day=1)
        if ms == today.replace(day=1):
            mtd_days = today.day  # 含今天
        pm = _add_months(ms, -1)
        ym = _add_months(ms, -12)
        cur_s, cur_e = ms, _clip_month(ms, mtd_days)
        prev_s, prev_e = pm, _clip_month(pm, mtd_days)
        yoy_s, yoy_e = ym, _clip_month(ym, mtd_days)
    else:
        cur_s, cur_e = _period_bounds(gran, ref)
        prev_s, prev_e = _period_bounds(gran, _shift_period(gran, ref, -1))
        yoy_s, yoy_e = _period_bounds(gran, _shift_year_date(ref, -1))

    cur = _agg_period(cur_s, cur_e)
    prev = _agg_period(prev_s, prev_e)
    yoy = _agg_period(yoy_s, yoy_e)

    energies = list(EnergyType.objects.all())
    sections = list(Section.objects.all())
    ce = {et.pk: Decimal(et.coal_equiv) for et in energies}

    def tce(bucket, sec_id=None, et_id=None):
        """在某周期聚合结果上求折标煤合计(可按工段 / 能源过滤)。"""
        total = Decimal(0)
        for (s, e), amt in bucket.items():
            if sec_id is not None and s != sec_id:
                continue
            if et_id is not None and e != et_id:
                continue
            total += amt * ce.get(e, Decimal(0))
        return total

    def native(bucket, et_id):
        total = Decimal(0)
        for (s, e), amt in bucket.items():
            if e == et_id:
                total += amt
        return total

    # 全厂总览(折标煤)
    overall = {
        'cur': tce(cur), 'prev': tce(prev), 'yoy': tce(yoy),
        'mom': _pct(tce(cur), tce(prev)),
        'yoy_pct': _pct(tce(cur), tce(yoy)),
    }

    # 分能源明细(原始单位 + 折标煤)
    energy_rows = []
    for et in energies:
        c = native(cur, et.pk)
        p = native(prev, et.pk)
        y = native(yoy, et.pk)
        energy_rows.append({
            'et': et,
            'cur': c, 'prev': p, 'yoy': y,
            'cur_tce': c * ce.get(et.pk, Decimal(0)),
            'mom': _pct(c, p),
            'yoy_pct': _pct(c, y),
        })

    # 分工段排名(按当期折标煤总量)
    sec_rows = []
    for sec in sections:
        c = tce(cur, sec_id=sec.pk)
        p = tce(prev, sec_id=sec.pk)
        y = tce(yoy, sec_id=sec.pk)
        # 分能源明细,便于展开查看结构
        by_energy = []
        for et in energies:
            by_energy.append({
                'et': et,
                'cur': native({k: v for k, v in cur.items() if k[0] == sec.pk}, et.pk),
            })
        sec_rows.append({
            'section': sec,
            'cur': c, 'prev': p, 'yoy': y,
            'mom': _pct(c, p),
            'yoy_pct': _pct(c, y),
            'by_energy': by_energy,
        })
    sec_rows.sort(key=lambda r: r['cur'], reverse=True)

    total_cur = overall['cur'] or Decimal(1)
    max_cur = max((r['cur'] for r in sec_rows), default=Decimal(0)) or Decimal(1)
    for i, r in enumerate(sec_rows, 1):
        r['rank'] = i
        r['share'] = (r['cur'] / total_cur * 100) if total_cur else Decimal(0)
        r['bar'] = (r['cur'] / max_cur * 100) if max_cur else Decimal(0)
        r['cur_disp'] = _fnum(r['cur'])
        r['share_disp'] = f"{r['share']:.1f}"
        r['bar_disp'] = f"{float(r['bar']):.1f}"
        r['mom_d'] = _delta(r['mom'])
        r['yoy_d'] = _delta(r['yoy_pct'])
        for be in r['by_energy']:
            be['cur_disp'] = _fnum(be['cur'])

    # 全厂总览展示字段
    overall.update({
        'cur_disp': _fnum(overall['cur']),
        'prev_disp': _fnum(overall['prev']),
        'yoy_disp': _fnum(overall['yoy']),
        'mom_d': _delta(overall['mom']),
        'yoy_d': _delta(overall['yoy_pct']),
    })
    # 分能源展示字段
    for er in energy_rows:
        er['cur_disp'] = _fnum(er['cur'])
        er['prev_disp'] = _fnum(er['prev'])
        er['yoy_disp'] = _fnum(er['yoy'])
        er['cur_tce_disp'] = _fnum(er['cur_tce'])
        er['mom_d'] = _delta(er['mom'])
        er['yoy_d'] = _delta(er['yoy_pct'])

    ctx = {
        'gran': gran,
        'gran_choices': GRAN_CHOICES,
        'ref': f'{ref:%Y-%m-%d}',
        'today': f'{today:%Y-%m-%d}',
        'period_labels': {
            'cur': _period_label(gran, cur_s) + (f'(至{mtd_days}日)' if mtd_days else ''),
            'prev': _period_label(gran, prev_s) + (f'(前{mtd_days}日)' if mtd_days else ''),
            'yoy': _period_label(gran, yoy_s) + (f'(前{mtd_days}日)' if mtd_days else ''),
        },
        'overall': overall,
        'energy_rows': energy_rows,
        'sec_rows': sec_rows,
    }
    return render(request, 'board/analysis.html', ctx)


# ---------------------------------------------------------------------------
# 单位产品能耗 / 能效分析
# ---------------------------------------------------------------------------

EFF_TREND_DAYS = 14      # 能效趋势(日粒度)回看天数
EFF_TREND_MONTHS = 12    # 能效趋势(月粒度)回看月数


def efficiency(request):
    """单位产品能耗 / 能效分析。

    以产量折算「单位产品综合能耗」(综合能耗 tce ÷ 合格产量,展示为 kgce/单位产品):
    - 全厂能效总览:综合能耗 / 合格产量 / 单位产品综合能耗 + 环比、同比(单耗上升=红,下降=绿);
    - 能效趋势:近 N 期单位产品综合能耗曲线 + 期间均值对标线;
    - 分工段单耗对标:各工段单位产品能耗排名(低者优),对标全厂均值(标杆=最优工段);
    - 分能源单位消耗:每单位产品消耗的电 / 煤 / 蒸汽(原始单位)。
    综合能耗跨能源按折标煤(tce)统一口径。
    """
    gran = request.GET.get('gran')
    if gran not in ('day', 'month'):
        gran = 'day'

    today = timezone.localtime().date()
    ref = _parse_date(request.GET.get('ref') or '') or today

    # 三期 [start,end) 边界(与 analysis 一致;月粒度当月做「月至今」对齐,避免整月 vs 半月失真)
    mtd_days = None
    if gran == 'month':
        ms = ref.replace(day=1)
        if ms == today.replace(day=1):
            mtd_days = today.day
        pm = _add_months(ms, -1)
        ym = _add_months(ms, -12)
        cur_s, cur_e = ms, _clip_month(ms, mtd_days)
        prev_s, prev_e = pm, _clip_month(pm, mtd_days)
        yoy_s, yoy_e = ym, _clip_month(ym, mtd_days)
    else:
        cur_s, cur_e = _period_bounds(gran, ref)
        prev_s, prev_e = _period_bounds(gran, _shift_period(gran, ref, -1))
        yoy_s, yoy_e = _period_bounds(gran, _shift_year_date(ref, -1))

    energies = list(EnergyType.objects.all())
    sections = list(Section.objects.all())
    ce = {et.pk: Decimal(et.coal_equiv) for et in energies}

    cur = _agg_period(cur_s, cur_e)
    prev = _agg_period(prev_s, prev_e)
    yoy = _agg_period(yoy_s, yoy_e)
    out_cur = _agg_output(cur_s, cur_e)
    out_prev = _agg_output(prev_s, prev_e)
    out_yoy = _agg_output(yoy_s, yoy_e)

    def tce(bucket, sec_id=None, et_id=None):
        total = Decimal(0)
        for (s, e), amt in bucket.items():
            if sec_id is not None and s != sec_id:
                continue
            if et_id is not None and e != et_id:
                continue
            total += amt * ce.get(e, Decimal(0))
        return total

    def native(bucket, et_id, sec_id=None):
        total = Decimal(0)
        for (s, e), amt in bucket.items():
            if sec_id is not None and s != sec_id:
                continue
            if e == et_id:
                total += amt
        return total

    def unit_kgce(tce_val, out_val):
        """单位产品综合能耗:tce ÷ 产量 -> kgce/单位产品(×1000);产量为 0 / 缺失返回 None。"""
        if not out_val:
            return None
        return float(Decimal(tce_val) / Decimal(out_val) * 1000)

    # ---- 全厂能效总览 ----
    total_tce_cur = tce(cur)
    total_out_cur = sum(out_cur.values(), Decimal(0))
    total_tce_prev = tce(prev)
    total_out_prev = sum(out_prev.values(), Decimal(0))
    total_tce_yoy = tce(yoy)
    total_out_yoy = sum(out_yoy.values(), Decimal(0))

    unit_cur = unit_kgce(total_tce_cur, total_out_cur)
    unit_prev = unit_kgce(total_tce_prev, total_out_prev)
    unit_yoy = unit_kgce(total_tce_yoy, total_out_yoy)

    overall = {
        'tce_disp': _fnum(total_tce_cur),
        'out_disp': _fnum(total_out_cur),
        'unit_disp': (f'{unit_cur:,.1f}' if unit_cur is not None else '—'),
        'unit_prev_disp': (f'{unit_prev:,.1f}' if unit_prev is not None else '—'),
        'unit_yoy_disp': (f'{unit_yoy:,.1f}' if unit_yoy is not None else '—'),
        'mom_d': _delta(_pct(unit_cur, unit_prev) if unit_cur is not None and unit_prev else None),
        'yoy_d': _delta(_pct(unit_cur, unit_yoy) if unit_cur is not None and unit_yoy else None),
    }

    # ---- 分工段单位产品能耗对标(对标全厂均值,标杆=最优工段) ----
    baseline = unit_cur  # 对标基准 = 全厂当期平均单耗
    sec_rows = []
    for sec in sections:
        sec_tce = tce(cur, sec_id=sec.pk)
        sec_out = out_cur.get(sec.pk, Decimal(0))
        u = unit_kgce(sec_tce, sec_out)
        u_prev = unit_kgce(tce(prev, sec_id=sec.pk), out_prev.get(sec.pk, Decimal(0)))
        u_yoy = unit_kgce(tce(yoy, sec_id=sec.pk), out_yoy.get(sec.pk, Decimal(0)))
        # 分能源单位消耗(原始单位 / 单位产品)
        by_energy = []
        for et in energies:
            en = native(cur, et.pk, sec_id=sec.pk)
            per = (float(en / sec_out) if sec_out else None)
            by_energy.append({'et': et, 'per': per,
                              'per_disp': (f'{per:,.3f}' if per is not None else '—')})
        sec_rows.append({
            'section': sec,
            'tce': sec_tce, 'out': sec_out, 'unit': u,
            'unit_prev': u_prev, 'unit_yoy': u_yoy,
            'by_energy': by_energy,
        })

    # 有产量数据的工段参与排名(单耗低者优);无产量的排最后
    ranked = [r for r in sec_rows if r['unit'] is not None]
    ranked.sort(key=lambda r: r['unit'])
    noout = [r for r in sec_rows if r['unit'] is None]
    max_unit = max((r['unit'] for r in ranked), default=1.0) or 1.0
    best_unit = ranked[0]['unit'] if ranked else None
    for i, r in enumerate(ranked, 1):
        r['rank'] = i
        r['is_best'] = (i == 1)
        r['bar'] = f'{(r["unit"] / max_unit * 100):.1f}'
        r['unit_disp'] = f'{r["unit"]:,.1f}'
        r['tce_disp'] = _fnum(r['tce'])
        r['out_disp'] = _fnum(r['out'])
        # 对标全厂均值:低于均值=领先(绿),高于=落后(红)
        r['gap_d'] = _delta(_pct(r['unit'], baseline) if baseline else None)
        r['lead'] = (baseline is not None and r['unit'] <= baseline)
        # 与标杆(最优工段)差距
        r['vs_best_d'] = _delta(_pct(r['unit'], best_unit) if best_unit else None)
        r['mom_d'] = _delta(_pct(r['unit'], r['unit_prev']) if r['unit_prev'] else None)
        r['yoy_d'] = _delta(_pct(r['unit'], r['unit_yoy']) if r['unit_yoy'] else None)
    for r in noout:
        r['rank'] = '—'
        r['is_best'] = False
        r['bar'] = '0'
        r['unit_disp'] = '—'
        r['tce_disp'] = _fnum(r['tce'])
        r['out_disp'] = _fnum(r['out'])
        r['gap_d'] = _delta(None)
        r['lead'] = False
        r['vs_best_d'] = _delta(None)
        r['mom_d'] = _delta(None)
        r['yoy_d'] = _delta(None)
    disp_rows = ranked + noout

    # ---- 分能源单位消耗(全厂:每单位产品消耗的原始能源量) ----
    energy_rows = []
    for et in energies:
        c = native(cur, et.pk)
        p = native(prev, et.pk)
        y = native(yoy, et.pk)
        per_cur = (float(c / total_out_cur) if total_out_cur else None)
        per_prev = (float(p / total_out_prev) if total_out_prev else None)
        per_yoy = (float(y / total_out_yoy) if total_out_yoy else None)
        energy_rows.append({
            'et': et,
            'per_cur_disp': (f'{per_cur:,.4f}' if per_cur is not None else '—'),
            'per_prev_disp': (f'{per_prev:,.4f}' if per_prev is not None else '—'),
            'per_yoy_disp': (f'{per_yoy:,.4f}' if per_yoy is not None else '—'),
            'cur_tce_disp': _fnum(c * ce.get(et.pk, Decimal(0))),
            'mom_d': _delta(_pct(per_cur, per_prev) if per_cur is not None and per_prev else None),
            'yoy_d': _delta(_pct(per_cur, per_yoy) if per_cur is not None and per_yoy else None),
        })

    # ---- 能效趋势(近 N 期单位产品综合能耗曲线) ----
    trend = _eff_trend(gran, ref, ce, unit_kgce)
    trend_avg = None
    tvals = [t['unit'] for t in trend if t['unit'] is not None]
    if tvals:
        trend_avg = sum(tvals) / len(tvals)
    trend_svg = _build_eff_svg(trend, trend_avg)

    ctx = {
        'gran': gran,
        'gran_choices': GRAN_CHOICES,
        'ref': f'{ref:%Y-%m-%d}',
        'today': f'{today:%Y-%m-%d}',
        'period_labels': {
            'cur': _period_label(gran, cur_s) + (f'(至{mtd_days}日)' if mtd_days else ''),
            'prev': _period_label(gran, prev_s) + (f'(前{mtd_days}日)' if mtd_days else ''),
            'yoy': _period_label(gran, yoy_s) + (f'(前{mtd_days}日)' if mtd_days else ''),
        },
        'overall': overall,
        'baseline_disp': (f'{baseline:,.1f}' if baseline is not None else '—'),
        'best_name': (ranked[0]['section'].name if ranked else '—'),
        'best_unit_disp': (f'{best_unit:,.1f}' if best_unit is not None else '—'),
        'sec_rows': disp_rows,
        'energy_rows': energy_rows,
        'trend': trend_svg,
        'trend_avg_disp': (f'{trend_avg:,.1f}' if trend_avg is not None else '—'),
    }
    return render(request, 'board/efficiency.html', ctx)


def _eff_trend(gran, ref, ce, unit_fn):
    """近 N 期单位产品综合能耗序列:[{label, unit(kgce), tce, output}]。"""
    def _one(s, e):
        agg = _agg_period(s, e)
        outs = _agg_output(s, e)
        t = Decimal(0)
        for (sec, et), amt in agg.items():
            t += amt * ce.get(et, Decimal(0))
        o = sum(outs.values(), Decimal(0))
        return t, o

    nodes = []
    if gran == 'month':
        base = ref.replace(day=1)
        for i in range(EFF_TREND_MONTHS - 1, -1, -1):
            ms = _add_months(base, -i)
            me = _add_months(ms, 1)
            t, o = _one(ms, me)
            nodes.append({'label': f'{ms:%y-%m}', 'unit': unit_fn(t, o),
                          'tce': t, 'output': o})
    else:
        for i in range(EFF_TREND_DAYS - 1, -1, -1):
            d = ref - timedelta(days=i)
            t, o = _one(d, d + timedelta(days=1))
            nodes.append({'label': f'{d:%m-%d}', 'unit': unit_fn(t, o),
                          'tce': t, 'output': o})
    return nodes


def _build_eff_svg(nodes, baseline):
    """把单位产品能耗序列换算成 SVG 折线 / 面积坐标 + 网格 + 对标均值线(坐标服务端算好,模板纯静态渲染)。"""
    W, H = 1000.0, 260.0
    pad_l, pad_r, pad_t, pad_b = 12.0, 12.0, 20.0, 28.0
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    n = len(nodes)
    vals = [t['unit'] for t in nodes if t['unit'] is not None]
    vmax = max(vals + ([baseline] if baseline else []), default=1.0)
    vmax = vmax if vmax > 0 else 1.0

    pts = []
    for idx, t in enumerate(nodes):
        x = pad_l + (plot_w * (idx / (n - 1)) if n > 1 else plot_w / 2)
        t['x'] = round(x, 1)
        t['label_disp'] = t['label']
        if t['unit'] is None:
            t['y'] = None
            t['unit_disp'] = '—'
            continue
        y = pad_t + plot_h * (1 - t['unit'] / vmax)
        t['y'] = round(y, 1)
        t['unit_disp'] = f'{t["unit"]:,.1f}'
        pts.append(f'{x:.1f},{y:.1f}')

    line_points = ' '.join(pts)
    area = ''
    if pts:
        first_x = pts[0].split(',')[0]
        last_x = pts[-1].split(',')[0]
        area = (f'{first_x},{H - pad_b:.1f} ' + line_points +
                f' {last_x},{H - pad_b:.1f}')

    grids = []
    for frac in (0.0, 0.5, 1.0):
        y = pad_t + plot_h * frac
        grids.append({'y': round(y, 1), 'val': f'{vmax * (1 - frac):,.0f}'})

    baseline_y = None
    if baseline:
        baseline_y = round(pad_t + plot_h * (1 - baseline / vmax), 1)

    peak = max((t for t in nodes if t['unit'] is not None),
               key=lambda t: t['unit'], default=None)  # 单耗峰值(最差)

    return {
        'w': W, 'h': H,
        'baseline_bottom': round(H - pad_b, 1),
        'x0': pad_l, 'x1': round(pad_l + plot_w, 1),
        'points': line_points,
        'area': area,
        'nodes': nodes,
        'grids': grids,
        'baseline_y': baseline_y,
        'peak': peak,
        'has_data': bool(pts),
    }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _period_bounds(gran, ref_date):
    """返回某周期的 [start_date, end_date)(end 为开区间的次日 / 次月一号)。"""
    if gran == 'month':
        start = ref_date.replace(day=1)
        return start, _add_months(start, 1)
    return ref_date, ref_date + timedelta(days=1)


def _add_months(d, delta):
    m = d.month - 1 + delta
    y = d.year + m // 12
    return date(y, m % 12 + 1, 1)


def _clip_month(month_start, mtd_days):
    """月周期结束边界:mtd_days 为 None 取整月次月一号,否则取本月前 mtd_days 天(月至今对齐)。"""
    full_end = _add_months(month_start, 1)
    if mtd_days is None:
        return full_end
    clipped = month_start + timedelta(days=mtd_days)
    return min(clipped, full_end)


def _shift_period(gran, ref_date, delta):
    """把参考日按周期粒度平移 delta 个周期。"""
    if gran == 'month':
        return _add_months(ref_date.replace(day=1), delta)
    return ref_date + timedelta(days=delta)


def _shift_year_date(d, delta):
    try:
        return d.replace(year=d.year + delta)
    except ValueError:  # 2-29
        return d.replace(year=d.year + delta, day=28)


def _period_label(gran, start_date):
    if gran == 'month':
        return f'{start_date:%Y-%m}'
    return f'{start_date:%Y-%m-%d}'


def _agg_period(start_date, end_date):
    """聚合 [start,end) 内读数为 {(section_id, energy_type_id): Decimal 用量}。"""
    start = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(end_date, datetime.min.time()))
    rows = (Reading.objects.filter(ts__gte=start, ts__lt=end)
            .values('section_id', 'energy_type_id')
            .annotate(total=Sum('amount')))
    out = {}
    for r in rows:
        out[(r['section_id'], r['energy_type_id'])] = r['total'] or Decimal(0)
    return out


def _agg_output(start_date, end_date):
    """聚合 [start,end) 内各工段的合格产量 -> {section_id: Decimal 产量}。

    产量是「工段 × 时段」的物理量,与能源类型无关;批量录入时同一时段各能源共享写入同一产量,
    故按 (section, ts) 先用 Max 去重(取该时段唯一产量),再按工段求和,避免跨能源三倍重复计数。
    """
    start = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(end_date, datetime.min.time()))
    rows = (Reading.objects.filter(ts__gte=start, ts__lt=end, output__isnull=False)
            .values('section_id', 'ts')
            .annotate(o=Max('output')))
    out = {}
    for r in rows:
        out[r['section_id']] = out.get(r['section_id'], Decimal(0)) + (r['o'] or Decimal(0))
    return out


def _pct(cur, base):
    """变化率(%),基期为 0 / 缺失时返回 None(前端显示「—」)。"""
    if not base:
        return None
    return float((cur - base) / base * 100)


def _delta(pct):
    """把变化率转成展示用 {文本, 方向类, 箭头}。能耗上升=红(up),下降=绿(down)。"""
    if pct is None:
        return {'text': '—', 'cls': 'flat', 'arrow': ''}
    if pct > 0.05:
        return {'text': f'+{pct:.1f}%', 'cls': 'up', 'arrow': '▲'}
    if pct < -0.05:
        return {'text': f'{pct:.1f}%', 'cls': 'down', 'arrow': '▼'}
    return {'text': '0.0%', 'cls': 'flat', 'arrow': ''}


def _fnum(dec, digits=1):
    try:
        return f'{Decimal(dec):,.{digits}f}'
    except (InvalidOperation, TypeError):
        return '0'


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _to_decimal(raw):
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw == '':
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _fmt(dec):
    """去掉小数末尾多余的 0,便于表格回填。"""
    if dec is None:
        return ''
    d = Decimal(dec).normalize()
    # normalize 会把整数变成科学计数(如 1E+4),用 quantize 兜底
    if d == d.to_integral():
        return str(d.to_integral())
    return format(d, 'f')
