from dbgpt_app.openapi.api_v1.answer_numbers import check_answer_numbers

EVIDENCE = ["| total_sales | invoice_count |\n| --- | --- |\n| 3875944906385.57 | 91933 |"]


def test_malformed_grouping_is_fixed_without_changing_digits():
    fixed, unverified, rewritten = check_answer_numbers(
        "جمع کل فروش: ۳۸,۷۵۹,۴۴۹,۰۶۳,۸۵.۵۷ ریال", EVIDENCE
    )
    assert "۳٬۸۷۵٬۹۴۴٬۹۰۶٬۳۸۵٫۵۷" in fixed
    assert rewritten == 1 and unverified == []


def test_persian_decimal_sign_used_as_thousands_separator():
    fixed, unverified, _ = check_answer_numbers(
        "**۳٫۸۷۵٫۹۴۴٫۹۰۶٫۳۸۵٫۵۷** و تعداد ۹۱٫۹۳۳ فاکتور", EVIDENCE
    )
    assert "۳٬۸۷۵٬۹۴۴٬۹۰۶٬۳۸۵٫۵۷" in fixed
    assert "۹۱٬۹۳۳" in fixed  # was readable as 91.933
    assert unverified == []


def test_correct_numbers_are_left_alone():
    text = "۳٬۸۷۵٬۹۴۴٬۹۰۶٬۳۸۵٫۵۷ و 3,875,944,906,385.57 و ۳.۵ درصد"
    fixed, unverified, rewritten = check_answer_numbers(text, EVIDENCE)
    assert fixed == text and rewritten == 0 and unverified == []


def test_large_number_not_in_observations_is_flagged():
    _, unverified, _ = check_answer_numbers("Total: 12,345,678", ["| total |\n| 999 |"])
    assert unverified == ["12,345,678"]


def test_sql_literals_dates_and_code_spans_are_not_touched():
    text = "از `m_regdate >= '20190101'` تا 1404/12/29 و `x = 1.234.567`"
    fixed, unverified, rewritten = check_answer_numbers(text, ["SELECT 1"])
    assert fixed == text and unverified == [] and rewritten == 0
