import pytest

from dbgpt_app.openapi.api_v1.report_charts import build_sql_chart_report


def test_monthly_chart_splits_scales_and_skips_zero_measures():
    cols = ["ym", "cnt", "total", "voidcnt"]
    rows = [["1404/01", 1224, 53995643646, 0], ["1404/02", 2287, 95854068785, 0],
            ["1404/03", 1570, 66549724789, 0]]
    html, summary = build_sql_chart_report(cols, rows, title="t", sql="SELECT 1")
    assert "2 chart(s)" in summary and "voidcnt" in summary
    assert "<polyline" in html  # time-like labels -> line chart
    assert "53,995,643,646" in html  # exact numbers in the data table


def test_pie_and_errors():
    html, _ = build_sql_chart_report(["m", "n"], [["A", 5], ["B", 3]], chart="pie")
    assert "<path" in html
    with pytest.raises(ValueError):
        build_sql_chart_report(["m", "n"], [])
    with pytest.raises(ValueError):
        build_sql_chart_report(["m", "n"], [["a", "x"], ["b", "y"]])

def test_many_categories_use_horizontal_bars_with_every_label():
    from dbgpt_app.openapi.api_v1.report_charts import build_sql_chart_report

    rows = [[f"استان شماره {i}", 1000 - i * 10] for i in range(32)] + [["", 5]]
    html, _ = build_sql_chart_report(["province", "total"], rows, chart="bar")
    for i in range(32):
        assert f"استان شماره {i}<" in html  # every name written, none thinned
    assert "(نامشخص)" in html
    assert 'class="axis cat"' in html
