"""Server-side charts from SQL results (no LLM-written HTML, no invented numbers).

The model only sends a SELECT statement plus a title (a few hundred tokens);
the server runs the query and draws dependency-free inline SVG charts plus a
data table. This replaces "the model types a whole HTML report" which was slow
(thousands of output tokens), could time out at the provider, and copied
numbers by hand.

Design rules (dataviz method): one value axis per chart — measures of very
different scale go to separate charts, never a dual axis; thin marks; legend
only for 2+ series; hover tooltip on every mark; a table view always present;
light and dark surfaces.
"""

from __future__ import annotations

import html as _html
import math
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Reference categorical palette (validated: adjacent CVD ΔE ≥ 8, light/dark).
_SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
_SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500"]
MAX_POINTS = 60
MAX_TABLE_ROWS = 200
MAX_SERIES_PER_CHART = 4
SCALE_RATIO_LIMIT = 20.0
_TIME_LIKE = re.compile(r"^\s*\d{2,4}([/\-.]\d{1,2}){1,2}\s*$|^\s*\d{4}\s*$")


def _to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        f = float(value)
        return f if math.isfinite(f) else None
    s = str(value).strip().replace(",", "").replace("٬", "")
    if not s:
        return None
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def _fmt_exact(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.2f}"


def _unit_for(vmax: float) -> Tuple[float, str]:
    """One display unit per chart (axis numbers stay plain, unit goes in the heading)."""
    for limit, unit in ((1e12, "هزار میلیارد"), (1e9, "میلیارد"), (1e6, "میلیون"), (1e4, "هزار")):
        if abs(vmax) >= limit:
            return limit, unit
    return 1.0, ""


def _fmt_axis(v: float, div: float = 1.0) -> str:
    x = v / div
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * exp >= v:
            return m * exp
    return 10 * exp


def _nice_step(span: float, target: int = 4) -> float:
    """Round tick step (1/2/2.5/5 x 10^k) giving about ``target`` intervals."""
    if span <= 0:
        return 1.0
    raw = span / target
    exp = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * exp >= raw:
            return m * exp
    return 10 * exp


def _fmt_compact(v: float) -> str:
    """Short value label with its own unit, e.g. '143 میلیارد'."""
    div, unit = _unit_for(abs(v))
    x = v / div
    s = f"{x:,.0f}" if abs(x) >= 100 else (f"{x:,.1f}" if abs(x) >= 10 else f"{x:,.2f}")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f"{s} {unit}".strip()


def _label_width(text: str) -> float:
    return 6.6 * len(text)


def _svg_hbar_chart(
    labels: List[str], series: List[Tuple[str, List[Optional[float]], int]]
) -> str:
    """Horizontal bars: every category name is written in full (RTL, right side)."""
    n = max(len(labels), 1)
    k = len(series)
    row_h = 22 + 12 * (k - 1)
    shown = [(str(l).strip() or "(نامشخص)")[:32] for l in labels]
    label_w = min(220.0, max(60.0, max((_label_width(l) for l in shown), default=60.0) + 16))
    width = 880
    pad_t, pad_b, pad_l = 8, 30, 96  # pad_l leaves room for value labels
    plot_r = width - label_w
    pw = plot_r - pad_l
    height = int(pad_t + n * row_h + pad_b)
    vals = [v for _, vs, _ in series for v in vs if v is not None]
    vmax = max([abs(v) for v in vals] + [0.0])
    step = _nice_step(vmax)
    top = max(step, math.ceil(vmax / step) * step)
    div, _unit = _unit_for(top)

    def w(v: float) -> float:
        return abs(v) / top * pw

    parts: List[str] = []
    t = 0.0
    while t <= top + 1e-9:  # vertical grid, value axis at the bottom
        gx = plot_r - w(t)
        parts.append(f'<line x1="{gx:.1f}" y1="{pad_t}" x2="{gx:.1f}" y2="{height - pad_b}" class="grid"/>')
        parts.append(
            f'<text x="{gx:.1f}" y="{height - pad_b + 16}" class="axis" text-anchor="middle">'
            f"{_html.escape(_fmt_axis(t, div))}</text>"
        )
        t += step
    parts.append(f'<line x1="{plot_r:.1f}" y1="{pad_t}" x2="{plot_r:.1f}" y2="{height - pad_b}" class="base"/>')
    bar_h = min(16.0, (row_h - 6 - 2 * (k - 1)) / k)
    for i, lab in enumerate(shown):
        y_row = pad_t + i * row_h
        cy = y_row + row_h / 2
        parts.append(
            f'<text x="{width - 6}" y="{cy + 4:.1f}" class="axis cat" direction="rtl" '
            f'text-anchor="start">{_html.escape(lab)}</text>'
        )
        group_h = bar_h * k + 2 * (k - 1)
        for si, (name, vs, color_idx) in enumerate(series):
            v = vs[i] if i < len(vs) else None
            if v is None:
                continue
            bw = max(w(v), 1.0)
            y0 = y_row + (row_h - group_h) / 2 + si * (bar_h + 2)
            tip = _html.escape(f"{labels[i]} — {name}: {_fmt_exact(v)}", quote=True)
            parts.append(
                f'<rect x="{plot_r - bw:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bar_h:.1f}" '
                f'rx="{min(3.0, bar_h / 2):.1f}" class="s{color_idx}" data-tip="{tip}"/>'
            )
            parts.append(
                f'<text x="{plot_r - bw - 6:.1f}" y="{y0 + bar_h / 2 + 4:.1f}" class="axis val" '
                f'direction="rtl" text-anchor="start">{_html.escape(_fmt_compact(v))}</text>'
            )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">' + "".join(parts) + "</svg>"
    )


def _use_hbar(labels: List[str], time_like: bool) -> bool:
    """Categories that would not fit (or be thinned) under vertical bars."""
    if time_like or len(labels) < 2:
        return False
    longest = max(len(str(l)) for l in labels)
    return len(labels) > 12 or (len(labels) > 6 and longest > 10)


def pick_columns(
    columns: Sequence[str], rows: Sequence[Sequence[Any]], x: str = "", y: Any = None
) -> Tuple[int, List[int]]:
    """Choose the label column and the numeric measure columns."""
    names = [str(c) for c in columns]
    lower = [n.lower() for n in names]

    def numeric_ratio(i: int) -> float:
        vals = [r[i] for r in rows if i < len(r) and r[i] is not None]
        if not vals:
            return 0.0
        return sum(1 for v in vals if _to_number(v) is not None) / len(vals)

    x_idx = lower.index(x.lower()) if x and x.lower() in lower else -1
    if x_idx < 0:
        x_idx = next((i for i in range(len(names)) if numeric_ratio(i) < 0.8), 0)
    wanted: List[str] = []
    if isinstance(y, str) and y.strip():
        wanted = [p.strip() for p in y.split(",") if p.strip()]
    elif isinstance(y, (list, tuple)):
        wanted = [str(p).strip() for p in y if str(p).strip()]
    y_idx = [lower.index(w.lower()) for w in wanted if w.lower() in lower and lower.index(w.lower()) != x_idx]
    if not y_idx:
        y_idx = [i for i in range(len(names)) if i != x_idx and numeric_ratio(i) >= 0.8]
    return x_idx, y_idx[:8]


def group_by_scale(maxima: Dict[int, float]) -> List[List[int]]:
    """Series whose maxima differ by more than SCALE_RATIO_LIMIT get separate charts."""
    order = sorted(maxima, key=lambda i: -abs(maxima[i]))
    groups: List[List[int]] = []
    for i in order:
        top = abs(maxima[i]) or 1.0
        for g in groups:
            ref = abs(maxima[g[0]]) or 1.0
            if ref / top <= SCALE_RATIO_LIMIT and len(g) < MAX_SERIES_PER_CHART:
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


def _svg_xy_chart(
    kind: str, labels: List[str], series: List[Tuple[str, List[Optional[float]], int]]
) -> str:
    width, height = 880, 380
    pad_l, pad_r, pad_t, pad_b = 96, 24, 20, 78
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for _, vs, _ in series for v in vs if v is not None]
    vmax = max(vals + [0.0])
    vmin = min(vals + [0.0])
    tick = _nice_step(max(vmax, 0.0) - min(vmin, 0.0))
    top = math.ceil(vmax / tick) * tick if vmax > 0 else 0.0
    bottom = math.floor(vmin / tick) * tick if vmin < 0 else 0.0
    span = (top - bottom) or 1.0
    div, _unit = _unit_for(max(abs(top), abs(bottom)))

    def ypos(v: float) -> float:
        return pad_t + ph - (v - bottom) / span * ph

    n = max(len(labels), 1)
    band = pw / n
    parts: List[str] = []
    n_ticks = int(round(span / tick)) if tick else 4
    for k in range(n_ticks + 1):  # recessive grid + value axis at round values
        val = bottom + tick * k
        gy = ypos(val)
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" class="axis" text-anchor="end">{_html.escape(_fmt_axis(val, div))}</text>')
    zero_y = ypos(0.0)
    parts.append(f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width - pad_r}" y2="{zero_y:.1f}" class="base"/>')
    step = max(1, math.ceil(n / 14))  # thin out x labels
    for i, lab in enumerate(labels):
        if i % step:
            continue
        cx = pad_l + band * (i + 0.5)
        parts.append(
            f'<text x="{cx:.1f}" y="{height - pad_b + 18}" class="axis" text-anchor="end" '
            f'transform="rotate(-35 {cx:.1f} {height - pad_b + 18})">{_html.escape(str(lab)[:18])}</text>'
        )
    if kind == "bar":
        k = len(series)
        group_w = band * 0.72
        bar_w = min(56.0, max(2.0, (group_w - 2 * (k - 1)) / k))
        group_w = bar_w * k + 2 * (k - 1)
        for si, (name, vs, color_idx) in enumerate(series):
            for i, v in enumerate(vs):
                if v is None:
                    continue
                x0 = pad_l + band * i + (band - group_w) / 2 + si * (bar_w + 2)
                y0, y1 = sorted((ypos(v), zero_y))
                h = max(y1 - y0, 0.5)
                r = min(4.0, bar_w / 2, h)
                tip = _html.escape(f"{labels[i]} — {name}: {_fmt_exact(v)}", quote=True)
                parts.append(
                    f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="{r:.1f}" '
                    f'class="s{color_idx}" data-tip="{tip}"/>'
                )
    else:  # line
        for name, vs, color_idx in series:
            pts = [(pad_l + band * (i + 0.5), ypos(v), v, i) for i, v in enumerate(vs) if v is not None]
            if len(pts) > 1:
                d = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in pts)
                parts.append(f'<polyline points="{d}" class="line s{color_idx}"/>')
            for x, y, v, i in pts:
                tip = _html.escape(f"{labels[i]} — {name}: {_fmt_exact(v)}", quote=True)
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="dot s{color_idx}"/>'
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" class="hit" data-tip="{tip}"/>'
                )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">' + "".join(parts) + "</svg>"
    )


def _svg_pie(labels: List[str], values: List[float], name: str) -> str:
    size, r, cx, cy = 360, 150, 180, 180
    total = sum(values) or 1.0
    parts: List[str] = []
    angle = -math.pi / 2
    for i, (lab, v) in enumerate(zip(labels, values)):
        frac = v / total
        a2 = angle + frac * 2 * math.pi
        large = 1 if frac > 0.5 else 0
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        tip = _html.escape(f"{lab} — {name}: {_fmt_exact(v)} ({frac:.1%})", quote=True)
        path = (
            f"M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} Z"
            if frac < 0.9999
            else f"M{cx - r},{cy} a{r},{r} 0 1 0 {2 * r},0 a{r},{r} 0 1 0 {-2 * r},0"
        )
        parts.append(f'<path d="{path}" class="slice p{i}" data-tip="{tip}"/>')
        angle = a2
    return f'<svg viewBox="0 0 {size} {size}" role="img" class="chart pie">' + "".join(parts) + "</svg>"


_PIE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
_PIE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]


def _css() -> str:
    def vars_(series: List[str], pie: List[str]) -> str:
        return "".join(f"--s{i}:{c};" for i, c in enumerate(series)) + "".join(
            f"--p{i}:{c};" for i, c in enumerate(pie)
        )

    light = "--bg:#f6f7f9;--surface:#fcfcfb;--text:#0b0b0b;--muted:#52514e;--grid:#e6e5e0;--border:#e3e2dc;" + vars_(_SERIES_LIGHT, _PIE_LIGHT)
    dark = "--bg:#111217;--surface:#1a1a19;--text:#f0efec;--muted:#c3c2b7;--grid:#34332f;--border:#3a3935;" + vars_(_SERIES_DARK, _PIE_DARK)
    series_css = "".join(
        f".s{i}{{fill:var(--s{i});stroke:var(--s{i})}}.legend .k{i}{{background:var(--s{i})}}"
        for i in range(len(_SERIES_LIGHT))
    ) + "".join(f".p{i}{{fill:var(--p{i})}}.legend .q{i}{{background:var(--p{i})}}" for i in range(len(_PIE_LIGHT)))
    return (
        f":root{{color-scheme:light;{light}}}"
        f"@media (prefers-color-scheme: dark){{:root{{color-scheme:dark;{dark}}}}}"
        "body{margin:0;padding:20px;background:var(--bg);color:var(--text);font-family:Vazirmatn,Tahoma,Arial,sans-serif}"
        ".card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:0 auto 16px;max-width:960px}"
        "h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px}.muted{color:var(--muted);font-size:12px}"
        ".chart{width:100%;height:auto;direction:ltr;display:block}.chart.pie{max-width:360px;margin:0 auto}"
        ".grid{stroke:var(--grid);stroke-width:1}.base{stroke:var(--muted);stroke-width:1}"
        ".axis{fill:var(--muted);font-size:11px}.axis.cat{fill:var(--text);font-size:12px}"
        "[data-tip]{cursor:default}"
        + series_css
        + "rect[data-tip],.slice{stroke:var(--surface);stroke-width:2}"
        ".chart .line{fill:none;stroke-width:2}.chart .dot{stroke:var(--surface);stroke-width:2}"
        ".chart .hit{fill:transparent;stroke:none}"
        + ".legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:4px 0 10px;font-size:12px;color:var(--muted)}"
        ".legend span{display:inline-flex;align-items:center;gap:6px}.legend i{width:10px;height:10px;border-radius:3px;display:inline-block}"
        "table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:start}"
        "td.num{text-align:end;direction:ltr;font-variant-numeric:tabular-nums}th{color:var(--muted);font-weight:600}"
        "details summary{cursor:pointer;color:var(--muted);font-size:12px}pre{white-space:pre-wrap;direction:ltr;text-align:left;font-size:12px}"
        "#tip{position:fixed;pointer-events:none;background:var(--text);color:var(--surface);padding:5px 8px;border-radius:6px;font-size:12px;display:none;z-index:9;direction:rtl}"
    )


_TIP_JS = (
    "<script>(function(){var t=document.getElementById('tip');"
    "document.addEventListener('mousemove',function(e){var el=e.target.closest&&e.target.closest('[data-tip]');"
    "if(!el){t.style.display='none';return;}t.textContent=el.getAttribute('data-tip');t.style.display='block';"
    "t.style.left=(e.clientX+12)+'px';t.style.top=(e.clientY+12)+'px';});})();</script>"
)


def build_sql_chart_report(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    title: str = "",
    chart: str = "",
    x: str = "",
    y: Any = None,
    sql: str = "",
) -> Tuple[str, str]:
    """Return (html, short text summary). Raises ValueError when nothing is plottable."""
    columns = [str(c) for c in columns]
    rows = [list(r) for r in rows]
    if not rows:
        raise ValueError("The query returned no rows, so there is nothing to chart.")
    x_idx, y_idx = pick_columns(columns, rows, x, y)
    if not y_idx:
        raise ValueError(
            "The query result has no numeric column to chart. Return one label column "
            "and at least one numeric column (e.g. SELECT month, SUM(amount) ...)."
        )
    shown = rows[:MAX_POINTS]
    labels = ["" if r[x_idx] is None else str(r[x_idx]).strip() for r in shown]
    data = {i: [_to_number(r[i]) if i < len(r) else None for r in shown] for i in y_idx}
    maxima = {i: max([abs(v) for v in data[i] if v is not None] + [0.0]) for i in y_idx}
    # A measure that is zero/empty everywhere carries no shape — table only.
    flat = [i for i in y_idx if maxima[i] == 0]
    maxima = {i: m for i, m in maxima.items() if m > 0}
    if not maxima:
        raise ValueError("All numeric values in the result are zero or empty; nothing to chart.")
    groups = group_by_scale(maxima)
    chart = (chart or "").strip().lower()
    time_like = sum(1 for l in labels if _TIME_LIKE.match(l)) >= max(2, int(0.8 * len(labels)))

    sections: List[str] = []
    for g in groups:
        names = [columns[i] for i in g]
        if chart == "pie" and len(g) == 1 and len(labels) <= 8 and all((v or 0) >= 0 for v in data[g[0]]):
            svg = _svg_pie(labels, [v or 0.0 for v in data[g[0]]], names[0])
            legend = '<div class="legend">' + "".join(
                f'<span><i class="q{i}"></i>{_html.escape(l)}</span>' for i, l in enumerate(labels)
            ) + "</div>"
        else:
            kind = chart if chart in ("bar", "line") else ("line" if time_like and len(labels) >= 3 else "bar")
            series = [(columns[i], data[i], k) for k, i in enumerate(g)]
            if kind == "bar" and _use_hbar(labels, time_like):
                svg = _svg_hbar_chart(labels, series)
            else:
                svg = _svg_xy_chart(kind, labels, series)
            legend = (
                '<div class="legend">'
                + "".join(f'<span><i class="k{k}"></i>{_html.escape(columns[i])}</span>' for k, i in enumerate(g))
                + "</div>"
                if len(g) > 1
                else ""
            )
        heading = "، ".join(names)
        if not (chart == "pie" and len(g) == 1):
            _div, unit = _unit_for(max(maxima[i] for i in g))
            if unit:
                heading += f" (به {unit})"
        sections.append(f'<div class="card"><h2>{_html.escape(heading)}</h2>{legend}{svg}</div>')

    note = (
        f'<p class="muted">نمودار فقط {MAX_POINTS} ردیف اول از {len(rows):,} ردیف را نشان می‌دهد.</p>'
        if len(rows) > MAX_POINTS
        else ""
    )
    num_cols = {i for i in range(len(columns)) if i in y_idx}
    head = "".join(f"<th>{_html.escape(c)}</th>" for c in columns)
    body_rows = []
    for r in rows[:MAX_TABLE_ROWS]:
        cells = []
        for i, c in enumerate(columns):
            v = r[i] if i < len(r) else None
            n = _to_number(v) if i in num_cols else None
            if n is not None:
                cells.append(f'<td class="num">{_fmt_exact(n)}</td>')
            else:
                cells.append(f"<td>{_html.escape('' if v is None else str(v))}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    table = f'<div class="card"><h2>جدول داده</h2><table><thead><tr>{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>'
    sql_block = (
        f'<div class="card"><details><summary>کوئری منبع</summary><pre>{_html.escape(sql)}</pre></details></div>'
        if sql
        else ""
    )
    safe_title = _html.escape((title or "گزارش").strip()[:150])
    page = (
        '<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{safe_title}</title><style>{_css()}</style></head><body>"
        f'<div class="card"><h1>{safe_title}</h1><p class="muted">ساخته‌شده مستقیم از نتیجهٔ کوئری روی دیتابیس — '
        f"{len(rows):,} ردیف</p>{note}</div>"
        + "".join(sections)
        + table
        + sql_block
        + '<div id="tip"></div>'
        + _TIP_JS
        + "</body></html>"
    )
    summary = (
        f"Chart rendered from the query result: {len(rows)} rows, label column "
        f"'{columns[x_idx]}', measures {', '.join(columns[i] for i in y_idx)}; "
        f"{len(groups)} chart(s) (measures of very different scale are drawn separately)"
        + (f"; all-zero measures shown only in the table: {', '.join(columns[i] for i in flat)}" if flat else "")
        + "."
    )
    return page, summary
