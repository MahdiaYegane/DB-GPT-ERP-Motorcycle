import asyncio
import json

import pytest

from dbgpt_app.openapi.api_v1 import governor as g


def _route(**kw):
    st = g.RunState(question="q")
    args = dict(triage=None, rule_intent=None, user_input="q", has_db=True, has_skill=False,
                has_kb=False, has_file=False, has_connectors=False, has_history=False)
    args.update(kw)
    return g.decide_route(st, **args)


def test_routing():
    assert _route(rule_intent="general_answer", user_input="[Database: db] سلام").route == "fast"
    st = _route(triage={"intent": "database_query", "complexity": 0.4, "needs_clarification": 0.9},
                user_input="فروش امسال؟")
    assert st.route == "data" and st.profile == "simple" and st.clarify_first
    assert _route(triage={"intent": "database_query", "complexity": 1.9}).route == "legacy"
    assert _route(triage={"intent": "database_query"}, has_skill=True).route == "legacy"
    assert not _route(triage={"intent": "database_query", "needs_clarification": 0.9},
                      has_history=True).clarify_first


@pytest.fixture
def fake_jev(monkeypatch):
    calls = []

    async def fake(payload, questions):
        calls.append(payload)
        name = list(questions)[0]
        return {"answers": {name: {"noul": 0.9 if name == "answers_question" else 0.1}}}

    import dbgpt_app.openapi.api_v1.liara_jev as lj

    monkeypatch.setattr(lj, "evaluate_liara_jev", fake)
    return calls


def _obs(md):
    return json.dumps({"chunks": [{"output_type": "markdown", "content": md}]})


def test_policy_partition_then_early_exit_then_verify(fake_jev):
    cat = {"tables": {"dbo.factors": {"columns": [
        {"name": "kindwork", "profile": {"values": [["S", 80], ["B", 20]], "values_source": "exact"}}]}}}
    st = g.RunState(question="total sales", route="data")
    pol = g.StepPolicy(st, catalog_loader=lambda: cat)
    run = asyncio.run
    v = run(pol({"round": 1, "action": "sql_query", "success": True,
                 "action_input": json.dumps({"sql": "SELECT SUM(totalprice) t FROM dbo.factors"}),
                 "observation": _obs("| t |\n| --- |\n| 100 |")}))
    assert "kindwork" in v["hint"]
    v = run(pol({"round": 2, "action": "sql_query", "success": True,
                 "action_input": json.dumps({"sql": "SELECT SUM(totalprice) t FROM dbo.factors WHERE kindwork='S'"}),
                 "observation": _obs("| t |\n| --- |\n| 3409670719977.40 |")}))
    assert "terminate" in v["hint"] and st.early_exit_hinted
    v = run(pol({"round": 3, "terminal": True, "action": "terminate",
                 "action_input": json.dumps({"result": "total = 9999999999"})}))
    assert v and v["continue"] and st.repairs == 1
    # only one repair per run
    assert run(pol({"round": 4, "terminal": True, "action": "terminate",
                    "action_input": json.dumps({"result": "total = 9999999999"})})) is None


def test_policy_stuck_hint(fake_jev):
    st = g.RunState(question="q", route="data")
    pol = g.StepPolicy(st)
    hints = [asyncio.run(pol({"round": r, "action": "code_interpreter", "success": True,
                              "observation": "x"})) for r in range(1, 5)]
    assert hints[-1] and "approach" in hints[-1]["hint"]


def test_data_question_without_database_routes_to_no_db():
    from dbgpt_app.openapi.api_v1.governor import RunState, decide_route

    st = decide_route(
        RunState(question="q"),
        triage={"intent": "report_build", "complexity": 1.2},
        rule_intent=None,
        user_input="یک گزارش مدیریتی از فروش کل و تعداد فاکتور تهیه کن",
        has_db=False, has_skill=False, has_kb=False, has_file=False,
        has_connectors=False, has_history=False,
    )
    assert st.route == "no_db" and st.profile == "fast"


def test_off_topic_gate_is_conservative():
    off = lambda text, score=0.97, hist=False: _route(  # noqa: E731
        triage={"intent": "general_answer", "off_topic": score}, user_input=text, has_history=hist
    ).route
    assert off("پایتخت فرانسه کجاست؟") == "out_of_scope"
    assert off("یک شعر کوتاه درباره بهار برام بنویس") == "out_of_scope"
    # business words, the assistant itself, low score, short follow-ups: never gated
    assert off("قیمت فروش قطعه پیستون چنده؟") != "out_of_scope"
    assert off("تو کی هستی؟") != "out_of_scope"
    assert off("پایتخت فرانسه کجاست؟", score=0.5) != "out_of_scope"
    assert off("سال بعدش چطور؟", hist=True) != "out_of_scope"
    assert _route(triage={"intent": "general_answer", "off_topic": 0.99},
                  user_input="یک شعر بنویس", has_file=True).route != "out_of_scope"
