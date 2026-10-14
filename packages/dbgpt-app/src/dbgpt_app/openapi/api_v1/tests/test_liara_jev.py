import json

import httpx
import pytest
from pydantic import ValidationError

from dbgpt_app.openapi.api_v1 import liara_jev_api
from dbgpt_app.openapi.api_v1.liara_jev import (
    LIARA_DEFAULT_API_BASE,
    LIARA_JEV_MODEL_ID,
    LiaraJevDecisionRequest,
    evaluate_liara_jev,
)
from dbgpt_app.openapi.api_v1.tools import liara_jev as liara_jev_tool_module


def _questions():
    return {
        "urgent": {
            "type": "noul",
            "instructions": "Does this require urgent attention?",
        }
    }


@pytest.mark.asyncio
async def test_evaluate_liara_jev_posts_typed_decision_request():
    observed = {}

    async def handler(request: httpx.Request):
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"urgent": {"type": "noul", "noul": 0.97}},
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    result = await evaluate_liara_jev(
        state={"message": "Production is down"},
        questions=_questions(),
        api_key="liara-test-key",
        transport=httpx.MockTransport(handler),
    )

    assert observed["url"] == f"{LIARA_DEFAULT_API_BASE}/decisions"
    assert observed["authorization"] == "Bearer liara-test-key"
    assert observed["payload"] == {
        "model": LIARA_JEV_MODEL_ID,
        "state": {"message": "Production is down"},
        "questions": _questions(),
    }
    assert result["answers"]["urgent"]["noul"] == 0.97


@pytest.mark.asyncio
async def test_evaluate_liara_jev_uses_configured_liara_base():
    observed = {}

    async def handler(request: httpx.Request):
        observed["url"] = str(request.url)
        return httpx.Response(200, json={"answers": {}})

    await evaluate_liara_jev(
        state="A support ticket",
        questions=_questions(),
        api_key="liara-test-key",
        api_base="https://ai.liara.ir/api/workspace/v1/",
        transport=httpx.MockTransport(handler),
    )

    assert observed["url"] == "https://ai.liara.ir/api/workspace/v1/decisions"


@pytest.mark.asyncio
async def test_evaluate_liara_jev_requires_api_key(monkeypatch):
    monkeypatch.delenv("LIARA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="LIARA_API_KEY"):
        await evaluate_liara_jev("state", _questions())


def test_jev_request_rejects_invalid_question_types():
    with pytest.raises(ValidationError, match="type must be"):
        LiaraJevDecisionRequest(
            state="state",
            questions={"bad": {"type": "chat", "instructions": "Decide"}},
        )


def test_jev_request_requires_at_least_one_question():
    with pytest.raises(ValidationError):
        LiaraJevDecisionRequest(state="state", questions={})


def test_jev_choice_and_score_questions_require_criteria():
    with pytest.raises(ValidationError, match="Choice question"):
        LiaraJevDecisionRequest(
            state="state",
            questions={"choice": {"type": "choice", "instructions": "Choose"}},
        )
    with pytest.raises(ValidationError, match="Score question"):
        LiaraJevDecisionRequest(
            state="state",
            questions={
                "score": {
                    "type": "score",
                    "instructions": "Rate",
                    "criteria": ["low"],
                }
            },
        )


@pytest.mark.asyncio
async def test_decision_api_returns_result(monkeypatch):
    async def fake_evaluate(state, questions):
        assert state == "A support ticket"
        assert questions == _questions()
        return {"model": "jev-1.13.0", "answers": {"urgent": {"noul": 0.97}}}

    monkeypatch.setattr(liara_jev_api, "evaluate_liara_jev", fake_evaluate)
    response = await liara_jev_api.liara_jev_decisions(
        LiaraJevDecisionRequest(state="A support ticket", questions=_questions())
    )

    assert response.success is True
    assert response.data["answers"]["urgent"]["noul"] == 0.97


@pytest.mark.asyncio
async def test_agent_tool_forwards_state_and_questions(monkeypatch):
    observed = {}

    async def fake_evaluate(state, questions):
        observed["state"] = state
        observed["questions"] = questions
        return {"model": "jev-1.13.0", "answers": {"urgent": {"noul": 0.97}}}

    monkeypatch.setattr(liara_jev_tool_module, "evaluate_liara_jev", fake_evaluate)
    decision_tool = liara_jev_tool_module.make_liara_jev_decision()
    result = json.loads(
        await decision_tool(
            state='{"ticket":"Production is down"}',
            questions=json.dumps(_questions()),
        )
    )

    assert observed == {
        "state": {"ticket": "Production is down"},
        "questions": _questions(),
    }
    assert result["chunks"][0]["output_type"] == "json"
    result_payload = json.loads(result["chunks"][0]["content"])
    assert result_payload["answers"]["urgent"]["noul"] == 0.97


def _triage_result():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "database_query",
                "confidence": 0.91,
            },
            "needs_clarification": {"type": "noul", "noul": 0.2},
            "task_complexity": {"type": "score", "score": 1.4},
        },
    }


def test_auto_triage_disabled_without_key_or_flag(monkeypatch):
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    monkeypatch.setenv("LIARA_JEV_AUTO_ENABLE", "0")

    async def _run():
        return await auto.run_jev_triage("some input")

    import asyncio

    assert asyncio.run(_run()) is None


def test_auto_triage_fails_open_on_transport_error(monkeypatch):
    import asyncio

    import httpx

    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    monkeypatch.setenv("LIARA_JEV_AUTO_ENABLE", "1")
    monkeypatch.setenv("LIARA_API_KEY", "liara-test-key")

    async def _boom(state, questions, **kwargs):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(auto, "evaluate_liara_jev", _boom)
    assert asyncio.run(auto.run_jev_triage("some input")) is None


def test_auto_triage_summary_and_prompt_guidance():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    summary = auto.summarize_jev_triage(_triage_result())

    assert summary["intent"] == "database_query"
    assert summary["intent_confidence"] == 0.91
    assert summary["needs_clarification"] == 0.2
    assert summary["complexity"] == 1.4
    assert summary["complexity_label"] == "multi-step"

    guidance = auto.format_jev_guidance_for_prompt(summary)
    assert "sql_query" in guidance
    assert "advisory only" in guidance

    step_content = auto.render_jev_step_content(summary)
    assert "intent=database_query" in step_content


def test_auto_triage_summary_never_raises_on_malformed_payload():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    summary = auto.summarize_jev_triage({"answers": {"intent": {"choice": "nope"}}})

    assert summary["intent"] == "general_answer"
    assert "general_answer" in auto.format_jev_guidance_for_prompt(summary)


def test_build_jev_triage_state_truncates_and_marks_context():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    state = auto.build_jev_triage_state(
        "x" * 100000, database_name="sales", has_file=True
    )

    assert "selected database: sales" in state
    assert "an input file is attached" in state
    assert "[truncated]" in state


def test_governor_triage_detects_visualization_and_report_required():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    payload = {
        "model": "jev-1.13.0",
        "answers": {
            "intent": {"choice": "report_build", "confidence": 0.9},
            "needs_clarification": {"type": "noul", "noul": 0.1},
            "task_complexity": {"type": "score", "score": 2.0},
            "needs_visualization": {"type": "noul", "noul": 0.95},
        },
    }
    # Explicit chart request in the question -> report is mandatory.
    summary = auto.summarize_jev_triage(
        payload, user_input="نمودار فروش ماهانه را رسم کن"
    )
    assert summary["expects_report"] is True
    assert summary["needs_visualization"] == 0.95
    guidance = auto.format_jev_guidance_for_prompt(summary)
    assert "REPORT REQUIRED" in guidance
    assert "html_interpreter" in guidance

    # Jev alone only *suggests* a visualization; it is never forced.
    summary = auto.summarize_jev_triage(payload, user_input="فروش ماه جاری چقدر بود؟")
    assert summary["expects_report"] is False
    guidance = auto.format_jev_guidance_for_prompt(summary)
    assert "REPORT REQUIRED" not in guidance
    assert "NOT requested" in guidance


def test_governor_history_html_detection():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    assert auto.history_has_html([]) is False
    assert auto.history_has_html([{"outputs": [{"output_type": "text"}]}]) is False
    assert (
        auto.history_has_html(
            [{"outputs": [{"output_type": "html", "content": "<html>" + "y" * 500}]}]
        )
        is True
    )


def test_governor_needs_repair_logic():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    assert (
        auto.needs_report_repair(
            user_input="نمودار فروش را نشان بده",
            triage_summary={"expects_report": True},
            has_html=False,
        )
        is True
    )
    # HTML already rendered -> never repair.
    assert (
        auto.needs_report_repair(
            user_input="نمودار فروش را نشان بده",
            triage_summary={"expects_report": True},
            has_html=True,
        )
        is False
    )
    # Without an explicit chart/visual request no fallback HTML is synthesized,
    # even if Jev verification thinks a report was expected.
    assert (
        auto.needs_report_repair(
            user_input="hello",
            triage_summary={"expects_report": False},
            verification_summary={"needs_repair": True},
            has_html=False,
        )
        is False
    )
    assert (
        auto.needs_report_repair(
            user_input="گزارش فروش استان ها",
            triage_summary={"expects_report": False},
            has_html=False,
        )
        is False
    )


def test_governor_fallback_html_embeds_images_and_snippets():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    history = [
        {
            "action": "sql_query",
            "outputs": [{"output_type": "text", "content": "total sales: 100"}],
        }
    ]
    doc = auto.build_fallback_report_html(
        title="Sales",
        user_input="نمودار فروش",
        final_content="Done",
        history_steps=history,
        generated_images=["/images/chart.png"],
    )

    assert "<!DOCTYPE html>" in doc
    assert "/images/chart.png" in doc
    assert "total sales: 100" in doc


def test_governor_fallback_never_leaks_code_or_json():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    code_output = (
        "import matplotlib.pyplot as plt\n"
        'months=["a", "b"]\n'
        "sales = [10, 20]\n"
        "plt.plot(months, sales)\n"
        'plt.savefig("/mnt/data/x.png")'
    )
    history = [
        {"action": "code_interpreter", "outputs": [{"output_type": "code", "content": code_output}]},
        {"action": "sql_query", "outputs": [{"output_type": "text", "content": "total: 30"}]},
    ]
    doc = auto.build_fallback_report_html(
        title="نمودار [Database: sorooshan1040]",
        user_input="نمودار [Database: sorooshan1040]",
        final_content='```json\n{"code": "import plt"}\n```',
        history_steps=history,
        generated_images=[],
    )

    assert "[Database:" not in doc
    assert "import matplotlib" not in doc
    assert '{"code"' not in doc
    # Inline SVG chart rebuilt from the executed data must exist.
    assert "<svg" in doc
    assert "total: 30" in doc


def test_governor_strip_and_clean_helpers():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    assert auto.strip_context_tags("نمودار بده [Database: sorooshan1040]") == "نمودار بده"
    assert auto._looks_like_tool_json('{"code": "x"}') is True
    assert auto._looks_like_tool_json("پاسخ عادی") is False
    assert auto.clean_answer_text('```json\n{"code": "x"}\n```') == ""
    assert auto.clean_answer_text("روند صعودی است") == "روند صعودی است"


def test_governor_chart_series_and_svg():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    history = [
        {
            "action": "code_interpreter",
            "outputs": [
                {
                    "output_type": "code",
                    "content": 'months=["a", "b", "c"]\nsales = [1, 2, 3]',
                }
            ],
        }
    ]
    series = auto.extract_chart_series(history)
    assert series is not None
    assert series["values"] == [1.0, 2.0, 3.0]
    svg = auto.build_inline_svg_chart(series["labels"], series["values"], title="t")
    assert "<svg" in svg and "<polyline" in svg


def test_governor_verification_summary_flags_missing_html():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    summary = auto.summarize_jev_verification(
        {
            "answers": {
                "report_expected": {"noul": 0.9},
                "html_delivered": {"noul": 0.1},
                "answer_completeness": {"score": 1.0},
            }
        }
    )

    assert summary["needs_repair"] is True
    card = auto.render_jev_verification_content(summary, repaired=True)
    assert "repaired=yes" in card


def test_explicit_database_question_overrides_jev_report_misclassification():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    question = "چه استان هایی بیشترین فروش رو داشتند؟"
    assert auto.classify_request_intent(question) == "database_query"
    summary = auto.summarize_jev_triage(
        {
            "answers": {
                "intent": {"choice": "report_build", "confidence": 0.99},
                "needs_visualization": {"noul": 0.99},
            }
        },
        user_input=question,
    )
    assert summary["intent"] == "database_query"
    assert summary["intent_source"] == "explicit_request"
    assert summary["expects_report"] is False


def test_relevance_guard_requires_successful_sql_but_has_no_domain_rules():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    question = "چه استان هایی بیشترین فروش رو داشتند؟"
    no_sql = auto.assess_database_answer_relevance(question, [])
    assert no_sql["requires_database"] is True
    assert no_sql["relevant"] is False

    failed = [{"action": "sql_query", "action_input": "SELECT 1", "status": "failed",
               "outputs": [{"output_type": "text", "content": "SQL execution failed"}]}]
    assert auto.assess_database_answer_relevance(question, failed)["relevant"] is False

    any_table = [{"action": "sql_query", "action_input": "SELECT a, SUM(b) FROM t GROUP BY a",
                  "status": "done",
                  "outputs": [{"output_type": "markdown", "content": "| a | b |\n| x | 1 |"}]}]
    # Semantic relevance is judged by Jev verification, not by column-name rules.
    assert auto.assess_database_answer_relevance(question, any_table)["relevant"] is True


def test_sales_by_province_sql_evidence_passes_relevance_guard():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    question = "چه استان هایی بیشترین فروش رو داشتند؟"
    relevant_history = [
        {
            "action": "sql_query",
            "action_input": '{"sql":"SELECT statename, SUM(totalprice) AS sales FROM dbo.factors GROUP BY statename"}',
            "status": "done",
            "outputs": [
                {
                    "output_type": "markdown",
                    "content": "| statename | sales |\\n| --- | --- |\\n| Tehran | 500 |",
                }
            ],
        }
    ]
    assessment = auto.assess_database_answer_relevance(question, relevant_history)
    assert assessment["relevant"] is True


def test_database_verification_state_includes_sql_and_result():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    history = [
        {
            "action": "sql_query",
            "action_input": {"sql": "SELECT statename, SUM(totalprice) FROM dbo.factors"},
            "status": "done",
            "outputs": [
                {"output_type": "markdown", "content": "Tehran | 500"}
            ],
        }
    ]
    state = auto.build_jev_verification_state(
        "چه استان هایی بیشترین فروش رو داشتند؟",
        history,
        "Tehran 500",
    )
    assert "SQL:" in state
    assert "statename" in state
    assert "SQL_RESULT:" in state


# --- Performance hardening (P0/P1/P2) ---


def test_jev_trivial_inputs_skip_triage():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    for trivial in ["hi", "سلام", "ممنون", "thanks!", "ok", "  ", "ab", "خداحافظ"]:
        assert auto.should_skip_jev_triage(trivial) is True, trivial
    # Report-seeking inputs NEVER skip, even when short.
    assert auto.should_skip_jev_triage("نمودار بده") is False
    assert auto.should_skip_jev_triage("hi, نمودار فروش را نشان بده") is False
    assert auto.should_skip_jev_triage("کوئری فروش را بزن و تحلیل کن") is False


def test_jev_triage_state_uses_small_budget_and_extra_hints():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    state = auto.build_jev_triage_state(
        "x" * 100000,
        database_name="sales",
        has_file=True,
        extra_hints=["7 skills registered", "2 connectors selected"],
    )

    # P0: triage budget is 2000, not the 6000 verification budget.
    assert len(state) <= 2000 + 500
    assert "selected database: sales" in state
    assert "7 skills registered" in state
    assert "2 connectors selected" in state
    assert "[truncated]" in state


def test_jev_report_threshold_is_tunable(monkeypatch):
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    payload = {
        "answers": {
            "intent": {"choice": "general_answer", "confidence": 0.9},
            "needs_visualization": {"type": "noul", "noul": 0.65},
        }
    }

    monkeypatch.delenv("LIARA_JEV_REPORT_THRESHOLD", raising=False)
    assert auto.summarize_jev_triage(payload)["suggests_visualization"] is True

    monkeypatch.setenv("LIARA_JEV_REPORT_THRESHOLD", "0.9")
    assert auto.summarize_jev_triage(payload)["suggests_visualization"] is False
    assert auto.get_jev_report_threshold() == 0.9

    monkeypatch.setenv("LIARA_JEV_REPORT_THRESHOLD", "garbage")
    assert auto.get_jev_report_threshold() == 0.6


def test_jev_mid_reminder_cadence_is_adaptive(monkeypatch):
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    monkeypatch.delenv("LIARA_JEV_MID_REMINDER_EVERY", raising=False)
    assert auto.get_jev_mid_reminder_every() == 5
    # High confidence keeps the base cadence.
    assert auto.get_jev_mid_reminder_every_for({"intent_confidence": 0.9}) == 5
    # Low confidence reminders are spaced out to avoid nagging.
    assert auto.get_jev_mid_reminder_every_for({"intent_confidence": 0.3}) == 8
    assert auto.get_jev_mid_reminder_every_for({}) == 5


def test_jev_verify_timeout_default_is_fast():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    import os

    os.environ.pop("LIARA_JEV_VERIFY_TIMEOUT", None)
    assert auto.get_jev_verify_timeout() == 4.0


def test_jev_triage_questions_mention_persian_report_terms():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    questions = auto.build_jev_triage_questions()
    viz_text = (
        questions["needs_visualization"]["instructions"]
        + questions["needs_visualization"]["criteria"]["true"]
    )
    assert "نمودار" in viz_text
    report_text = questions["intent"]["criteria"]["report_build"]
    assert "گزارش" in report_text


@pytest.mark.asyncio
async def test_jev_shared_client_is_reused(monkeypatch):
    import httpx

    from dbgpt_app.openapi.api_v1 import liara_jev as jev_mod

    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answers": {}}

    class _FakeClient:
        is_closed = False

        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            return _FakeResponse()

    fake = _FakeClient()
    monkeypatch.setattr(jev_mod, "_JEV_SHARED_CLIENT", fake)

    await jev_mod.evaluate_liara_jev(
        state="s", questions=_questions(), api_key="k"
    )
    await jev_mod.evaluate_liara_jev(
        state="s", questions=_questions(), api_key="k"
    )

    assert len(calls) == 2
    timeouts = {c[1].get("timeout") for c in calls}
    assert timeouts == {30.0}


def test_database_tag_and_conversational_questions_are_not_db_queries():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    # The UI "[Database: ...]" prefix must not turn every message into a DB query.
    assert (
        auto.classify_request_intent("[Database: sorooshan1040] سوال قبلی رو یادت هست چی بود؟")
        == "general_answer"
    )
    assert auto.classify_request_intent("[Database: sorooshan1040] سلام خوبی؟") is None
    assert (
        auto.classify_request_intent("[Database: sorooshan1040] جمع فروش محصولات رو بگو")
        == "database_query"
    )
    assessment = auto.assess_database_answer_relevance(
        "[Database: sorooshan1040] سوال قبلی رو یادت هست چی بود؟", []
    )
    assert assessment["requires_database"] is False


def test_follow_up_answered_from_previous_sql_evidence_is_accepted():
    from dbgpt_app.openapi.api_v1 import liara_jev_auto as auto

    question = "[Database: x] جمع فروش محصولات رو دوباره بگو"
    assert auto.assess_database_answer_relevance(question, [])["relevant"] is False
    assert (
        auto.assess_database_answer_relevance(
            question, [], history_has_sql_evidence=True
        )["relevant"]
        is True
    )
