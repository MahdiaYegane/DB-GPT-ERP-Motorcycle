"""Conversation memory helpers for the ReAct agent chat.

Two things live here so they can be unit-tested without the whole API module:

* :func:`build_history_turns` turns the persisted chat history of a
  conversation into an explicit, role-tagged list of (role, content) turns
  that is passed to the agent as ``historical_dialogues``. It
    - tags roles explicitly (no more "even index = user, odd = AI" guessing),
    - fills a placeholder answer for rounds that were interrupted (stopped,
      server restart, pending question) so roles never shift,
    - appends a compact summary of the tools used in each previous turn
      (e.g. the SQL that was run and a preview of its result) so follow-up
      questions like "which query did you run?" can be answered.

* :class:`BoundedConvCache` is a small LRU cache with reference counting used
  for the per-conversation ``GptsMemory`` objects, so the process no longer
  keeps one memory object (and one thread pool) per conversation forever.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Generic, Iterable, List, Optional, Tuple
from typing import TypeVar

logger = logging.getLogger(__name__)

HUMAN = "human"
AI = "ai"

INTERRUPTED_REPLY = (
    "[این نوبت پیش از تکمیل پاسخ متوقف شد / "
    "This turn was interrupted before an answer was produced.]"
)
EMPTY_REPLY = "[No text answer was produced in this turn.]"
TOOLS_HEADER = "[Tools used in this turn — for reference in follow-up questions]"

# Bookkeeping / UI-only actions that carry no useful memory for follow-ups.
_SKIP_ACTIONS = {
    "terminate",
    "todowrite",
    "question",
    "load_tools",
    "load_skill",
    "get_skill_resource",
    "html_interpreter",
    "liara_jev_decision",  # automatic triage/verification, not a user-facing step
}
MAX_STEPS_PER_TURN = 8
MAX_INPUT_CHARS = 800
MAX_OUTPUT_CHARS = 500
MAX_SUMMARY_CHARS = 4000


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 15)].rstrip() + " …[truncated]"


def _format_action_input(raw: Any) -> str:
    """Prefer the SQL / code / query field of a tool input when present."""
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            data = raw
    if isinstance(data, dict):
        for key in ("sql", "query", "code", "command", "pattern", "path"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return str(data)
    return "" if data is None else str(data)


def _format_outputs(outputs: Any) -> str:
    if not isinstance(outputs, list):
        return ""
    parts: List[str] = []
    for item in outputs:
        if isinstance(item, dict):
            if item.get("output_type") in ("image", "html", "code"):
                continue
            content = item.get("content")
        else:
            content = item
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return "\n".join(parts)


def summarize_tool_steps(steps: Any) -> str:
    """Build a compact, model-readable summary of the tool calls of one turn."""
    if not isinstance(steps, list):
        return ""
    lines: List[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if not isinstance(action, str) or not action or action in _SKIP_ACTIONS:
            continue
        status = step.get("status") or "done"
        action_input = _truncate(
            _format_action_input(step.get("action_input")), MAX_INPUT_CHARS
        )
        output = _truncate(_format_outputs(step.get("outputs")), MAX_OUTPUT_CHARS)
        entry = f"- {action} [{status}]"
        if action_input:
            entry += f"\n  input: {action_input}"
        if output:
            entry += f"\n  result: {output}"
        lines.append(entry)
    if not lines:
        return ""
    lines = lines[-MAX_STEPS_PER_TURN:]
    return _truncate("\n".join(lines), MAX_SUMMARY_CHARS)


def view_payload_to_text(content: Any) -> str:
    """Convert a persisted react-agent ``view`` payload into the AI turn text."""
    payload: Any = content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except Exception:
            return content.strip()
    if not isinstance(payload, dict):
        return str(payload or "").strip()
    final = str(payload.get("final_content") or "").strip()
    tools = summarize_tool_steps(payload.get("steps"))
    if tools:
        final = f"{final}\n\n{TOOLS_HEADER}\n{tools}" if final else (
            f"{TOOLS_HEADER}\n{tools}"
        )
    return final


def build_history_turns(messages: Iterable[Any]) -> List[Tuple[str, str]]:
    """Return role-tagged (role, content) turns from stored history messages.

    ``messages`` are ``BaseMessage``-like objects with ``type`` and ``content``.
    The result always strictly alternates human → ai and never ends with a
    dangling human turn (the current question is appended by the caller).
    """
    turns: List[Tuple[str, str]] = []
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        content = getattr(msg, "content", None)
        if msg_type == "human":
            text = "" if content is None else str(content).strip()
            if not text:
                continue
            if turns and turns[-1][0] == HUMAN:
                # Previous round never got an answer (stopped / restarted).
                turns.append((AI, INTERRUPTED_REPLY))
            turns.append((HUMAN, text))
        elif msg_type in ("ai", "view"):
            text = (
                view_payload_to_text(content)
                if msg_type == "view"
                else ("" if content is None else str(content).strip())
            )
            if not turns:
                # An answer without a question carries no usable context.
                continue
            if turns[-1][0] == AI:
                # Several answer messages in one round: merge them.
                if text:
                    prev = turns[-1][1]
                    merged = text if prev in (EMPTY_REPLY, INTERRUPTED_REPLY) else (
                        f"{prev}\n\n{text}"
                    )
                    turns[-1] = (AI, merged)
                continue
            turns.append((AI, text or EMPTY_REPLY))
    if turns and turns[-1][0] == HUMAN:
        turns.append((AI, INTERRUPTED_REPLY))
    return turns


V = TypeVar("V")


class BoundedConvCache(Generic[V]):
    """Thread-safe LRU cache keyed by conversation id, with in-use pinning.

    Entries that are currently in use (``acquire`` without a matching
    ``release``) are never evicted. Evicted values are passed to ``on_evict``
    so their resources can be released.
    """

    def __init__(
        self, max_size: int = 64, on_evict: Optional[Callable[[str, V], None]] = None
    ) -> None:
        self._max_size = max(1, int(max_size))
        self._on_evict = on_evict
        self._data: "OrderedDict[str, V]" = OrderedDict()
        self._refs: Dict[str, int] = {}
        self._lock = threading.RLock()

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def __getitem__(self, key: str) -> V:
        with self._lock:
            value = self._data[key]
            self._data.move_to_end(key)
            return value

    def __setitem__(self, key: str, value: V) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
        self._evict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def get(self, key: str, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            if key not in self._data:
                return default
            return self[key]

    def acquire(self, key: str) -> None:
        with self._lock:
            self._refs[key] = self._refs.get(key, 0) + 1

    def release(self, key: str) -> None:
        with self._lock:
            count = self._refs.get(key, 0) - 1
            if count > 0:
                self._refs[key] = count
            else:
                self._refs.pop(key, None)
        self._evict()

    def _evict(self) -> None:
        evicted: List[Tuple[str, V]] = []
        with self._lock:
            if len(self._data) <= self._max_size:
                return
            for key in list(self._data.keys()):
                if len(self._data) <= self._max_size:
                    break
                if self._refs.get(key):
                    continue  # in use by a running turn
                evicted.append((key, self._data.pop(key)))
        for key, value in evicted:
            if self._on_evict is None:
                continue
            try:
                self._on_evict(key, value)
            except Exception:
                logger.debug("Failed to dispose evicted entry %s", key, exc_info=True)
