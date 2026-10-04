"""Tests for ReAct agent stream terminal-event lifecycle handling."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from dbgpt_app.openapi.api_v1 import agentic_data_api
from dbgpt_app.openapi.api_v1.react_final import AgentFinalAnswer


def _decode_sse_event(event: str):
    assert event.startswith("data: ")
    return json.loads(event.removeprefix("data: ").strip())


def test_history_failure_does_not_drop_final_or_done(caplog) -> None:
    class _FailingStorageConversation:
        def add_view_message(self, payload: str) -> None:
            raise RuntimeError("history storage unavailable")

        def end_current_round(self) -> None:
            raise AssertionError("must stop after the first persistence failure")

        def save_to_storage(self) -> None:
            raise AssertionError("must stop after the first persistence failure")

    events = agentic_data_api._react_terminal_events(
        _FailingStorageConversation(),
        '{"type":"react-agent"}',
        AgentFinalAnswer(content="answer"),
    )

    assert [_decode_sse_event(event) for event in events] == [
        {
            "type": "final",
            "protocol_version": 2,
            "content": "answer",
            "citations": [],
        },
        {"type": "done"},
    ]
    assert "Failed to persist ReAct agent history" in caplog.text


@pytest.mark.asyncio
async def test_closing_stream_cancels_and_awaits_agent_task(monkeypatch) -> None:
    task_started = asyncio.Event()
    task_finished = asyncio.Event()
    created_tasks = []

    async def _agent_work() -> None:
        task_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            task_finished.set()

    async def _fake_stream_impl(dialogue, tool_mode="full", agent_task_holder=None):
        del dialogue, tool_mode
        task = asyncio.create_task(_agent_work())
        created_tasks.append(task)
        agent_task_holder.append(task)
        await task_started.wait()
        yield "data: first\n\n"
        await asyncio.Event().wait()

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _fake_stream_impl,
    )

    stream = agentic_data_api._react_agent_stream(SimpleNamespace())
    assert await anext(stream) == "data: first\n\n"

    await stream.aclose()

    assert len(created_tasks) == 1
    assert created_tasks[0].cancelled()
    assert created_tasks[0].done()
    assert task_finished.is_set()


@pytest.mark.asyncio
async def test_runtime_failure_emits_structured_final_and_done(
    monkeypatch, caplog
) -> None:
    async def _failing_stream_impl(dialogue, tool_mode="full", agent_task_holder=None):
        del dialogue, tool_mode, agent_task_holder
        if False:
            yield ""
        raise RuntimeError("model stream failed")

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _failing_stream_impl,
    )

    events = [
        _decode_sse_event(event)
        async for event in agentic_data_api._react_agent_stream(SimpleNamespace())
    ]

    assert events == [
        {
            "type": "final",
            "protocol_version": 2,
            "content": "Sorry, an error occurred while generating the answer. Please try again.",
            "citations": [],
        },
        {"type": "done"},
    ]
    assert "ReAct agent stream failed before normal completion" in caplog.text


@pytest.mark.asyncio
async def test_runtime_failure_does_not_duplicate_a_final_event(monkeypatch) -> None:
    async def _partially_failing_stream_impl(
        dialogue, tool_mode="full", agent_task_holder=None
    ):
        del dialogue, tool_mode, agent_task_holder
        yield agentic_data_api._sse_event(
            AgentFinalAnswer(content="answer").to_sse_payload()
        )
        raise RuntimeError("failed after final")

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _partially_failing_stream_impl,
    )

    events = [
        _decode_sse_event(event)
        async for event in agentic_data_api._react_agent_stream(SimpleNamespace())
    ]

    assert [event["type"] for event in events] == ["final", "done"]
    assert events[0]["content"] == "answer"


@pytest.mark.asyncio
async def test_response_disconnect_closes_stream_and_agent_task(monkeypatch) -> None:
    task_started = asyncio.Event()
    task_finished = asyncio.Event()
    body_send_started = asyncio.Event()
    never = asyncio.Event()
    created_tasks = []

    async def _agent_work() -> None:
        task_started.set()
        try:
            await never.wait()
        finally:
            task_finished.set()

    async def _fake_stream_impl(dialogue, tool_mode="full", agent_task_holder=None):
        del dialogue, tool_mode
        task = asyncio.create_task(_agent_work())
        created_tasks.append(task)
        agent_task_holder.append(task)
        await task_started.wait()
        yield agentic_data_api._sse_event({"type": "step", "content": "first"})
        await never.wait()

    async def _send(message) -> None:
        if message["type"] == "http.response.body" and message.get("more_body"):
            body_send_started.set()
            await never.wait()

    async def _receive():
        await body_send_started.wait()
        return {"type": "http.disconnect"}

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _fake_stream_impl,
    )
    response = agentic_data_api._AgentStreamingResponse(
        agentic_data_api._react_agent_stream(SimpleNamespace()),
        media_type="text/event-stream",
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/react-agent",
        "headers": [],
        "asgi": {"version": "3.0", "spec_version": "2.0"},
    }

    await asyncio.wait_for(response(scope, _receive, _send), timeout=1)

    assert len(created_tasks) == 1
    assert created_tasks[0].cancelled()
    assert created_tasks[0].done()
    assert task_finished.is_set()


def test_truncated_tool_call_trace_is_detected() -> None:
    # Exact shape of the leaked trace persisted as final_content in
    # conversation 6863c8db (report-building run that exhausted retries):
    # free-form Thought text, a bare tool-name line with no "Action:"
    # prefix, then the tool-argument JSON blob.
    leaked = (
        "Now append the final section: sample table, key notes.\n"
        "code_interpreter\n"
        '{"code": "part3 = r\'\'\'<div class=\\"card\\"><h2>report</h2>"}'
    )
    assert agentic_data_api._is_truncated_tool_call(leaked) is True


def test_genuine_answers_are_not_flagged_as_tool_traces() -> None:
    # Prose mentioning a tool name must not match without the JSON blob...
    assert (
        agentic_data_api._is_truncated_tool_call(
            "For the query I used the sql_query tool. Result: 10 rows."
        )
        is False
    )
    # ...nor must a bare tool name alone...
    assert agentic_data_api._is_truncated_tool_call("code_interpreter") is False
    # ...nor must a JSON result object without a bare tool-name line...
    assert agentic_data_api._is_truncated_tool_call('{"result": "done"}') is False
    # ...nor must well-formed ReAct text (the parser handles that path).
    assert (
        agentic_data_api._is_truncated_tool_call(
            'Thought: test\nAction: sql_query\nAction Input: {"sql": "SELECT 1"}'
        )
        is False
    )


def test_repeated_tool_call_guard_stops_only_identical_repetitions() -> None:
    guard = agentic_data_api._RepeatedToolCallGuard(repeat_limit=3)
    action_input = {"sql": "SELECT TOP 10 id FROM accounts"}
    observation = '{"chunks": [{"content": "| id |\\n| 1 |"}]}'

    assert guard.observe("sql_query", action_input, observation) is False
    # Control actions between tool calls do not count as progress or repetitions.
    assert guard.observe("todowrite", {}, "updated") is False
    assert guard.observe("sql_query", action_input, observation) is False
    assert guard.observe("sql_query", action_input, observation) is True


def test_repeated_tool_call_guard_resets_when_result_changes() -> None:
    guard = agentic_data_api._RepeatedToolCallGuard(repeat_limit=3)

    assert guard.observe("sql_query", {"sql": "SELECT 1"}, "1") is False
    assert guard.observe("sql_query", {"sql": "SELECT 1"}, "2") is False
    assert guard.observe("sql_query", {"sql": "SELECT 1"}, "2") is False
    assert guard.observe("sql_query", {"sql": "SELECT 1"}, "2") is True
