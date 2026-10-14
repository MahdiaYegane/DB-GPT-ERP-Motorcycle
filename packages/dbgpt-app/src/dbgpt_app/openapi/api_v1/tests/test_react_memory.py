import json
from types import SimpleNamespace

from dbgpt_app.openapi.api_v1.react_memory import (
    AI,
    EMPTY_REPLY,
    HUMAN,
    INTERRUPTED_REPLY,
    TOOLS_HEADER,
    BoundedConvCache,
    build_history_turns,
    summarize_tool_steps,
)


def _msg(t, content):
    return SimpleNamespace(type=t, content=content)


def _view(final, steps=None):
    return json.dumps({"type": "react-agent", "final_content": final, "steps": steps or []})


def test_roles_alternate_and_view_final_content_used():
    turns = build_history_turns(
        [_msg("human", "q1"), _msg("view", _view("a1")), _msg("human", "q2"), _msg("view", _view("a2"))]
    )
    assert turns == [(HUMAN, "q1"), (AI, "a1"), (HUMAN, "q2"), (AI, "a2")]


def test_interrupted_round_gets_placeholder_and_roles_do_not_shift():
    turns = build_history_turns(
        [_msg("human", "q1"), _msg("human", "q2"), _msg("view", _view("a2")), _msg("human", "q3")]
    )
    assert [r for r, _ in turns] == [HUMAN, AI, HUMAN, AI, HUMAN, AI]
    assert turns[1] == (AI, INTERRUPTED_REPLY)
    assert turns[-1] == (AI, INTERRUPTED_REPLY)


def test_empty_answer_keeps_alternation():
    turns = build_history_turns([_msg("human", "q1"), _msg("view", _view("")), _msg("human", "q2"), _msg("ai", "a2")])
    assert turns[1] == (AI, EMPTY_REPLY)
    assert [r for r, _ in turns] == [HUMAN, AI, HUMAN, AI]


def test_tool_summary_contains_sql_and_result_but_skips_bookkeeping():
    steps = [
        {"action": "todowrite", "action_input": "{}", "outputs": [], "status": "done"},
        {
            "action": "sql_query",
            "action_input": json.dumps({"sql": "SELECT SUM(amount) FROM factor"}),
            "outputs": [{"output_type": "markdown", "content": "| sum |\n| 42 |"}],
            "status": "done",
        },
        {"action": "terminate", "action_input": "{}", "outputs": [], "status": "done"},
    ]
    text = build_history_turns([_msg("human", "q"), _msg("view", _view("answer", steps))])[1][1]
    assert text.startswith("answer")
    assert TOOLS_HEADER in text
    assert "SELECT SUM(amount) FROM factor" in text
    assert "42" in text
    assert "todowrite" not in text and "terminate" not in text


def test_tool_summary_is_bounded():
    steps = [
        {"action": "sql_query", "action_input": "x" * 5000, "outputs": [{"content": "y" * 5000}]}
        for _ in range(30)
    ]
    assert len(summarize_tool_steps(steps)) <= 4000


def test_bounded_cache_evicts_lru_but_not_pinned():
    evicted = []
    cache = BoundedConvCache(max_size=2, on_evict=lambda k, v: evicted.append(k))
    cache["a"] = 1
    cache.acquire("a")
    cache["b"] = 2
    cache["c"] = 3  # "a" is pinned -> "b" is evicted instead
    assert "a" in cache and "b" not in cache and "c" in cache
    assert evicted == ["b"]
    cache["d"] = 4  # still over capacity only via pinned entry -> evict "c"
    assert evicted == ["b", "c"]
    cache.release("a")
    cache["e"] = 5
    assert "a" not in cache and evicted[-1] == "a"
