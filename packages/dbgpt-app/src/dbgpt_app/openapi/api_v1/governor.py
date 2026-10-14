"""Governor: one run controller for the ReAct data agent.

The agent loop stays a ReAct loop, but every run is steered by one shared
``RunState`` and a policy that decides at fixed checkpoints, always with the
cheapest decider that is good enough:

    rule (≈0 ms)  →  local embeddings (catalog search, done elsewhere)
                  →  Jev typed decision (≈1 s)  →  main LLM (only for SQL/text)

Phases and decisions
--------------------
1. Intake  — route the request using Jev triage + explicit wording:
   ``fast`` (small talk, no tools), ``data`` (database question, compact
   prompt, 4 tools) or ``legacy`` (everything else, unchanged behaviour).
   Budget profile (steps / max tokens / seconds) from Jev complexity.
   Ambiguous first questions ask one clarifying question before any SQL.
2. Assemble — compact data-mode prompt and tool set (built by the caller with
   the helpers below); temperature 0 and text-only tool calls in data mode.
3. Execute — ``StepPolicy`` runs after every step:
   * successful ``sql_query`` → Jev "does this result answer the question?";
     yes → instruct the model to finish now (early exit);
   * aggregate over a table with an unfiltered low-cardinality column
     (from the catalog profile) → partition hint;
   * repeated SQL errors → hint with the real columns from the catalog;
   * several steps without new evidence → change-approach hint,
     optional one-time model escalation.
4. Verify — on ``terminate``: numbers not found in any query result, or a Jev
   "not relevant" verdict, send the answer back once with a targeted hint.
5. Telemetry — one JSON line per run (route, profile, decisions, steps, time)
   in ``pilot/meta_data/run_telemetry/<date>.jsonl``.

Everything is fail-open: any error here falls back to the plain ReAct loop.
Nothing in this module knows anything about a specific database.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── configuration (env) ─────────────────────────────────────────────────────
def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def enabled() -> bool:
    return _env_bool("DBGPT_GOV_ENABLE", True)


FAST_PATH = lambda: _env_bool("DBGPT_GOV_FAST_PATH", True)  # noqa: E731
DATA_MODE = lambda: _env_bool("DBGPT_GOV_DATA_MODE", True)  # noqa: E731
CLARIFY = lambda: _env_bool("DBGPT_GOV_CLARIFY", True)  # noqa: E731
SUFFICIENCY = lambda: _env_bool("DBGPT_GOV_SUFFICIENCY", True)  # noqa: E731
PARTITION_HINT = lambda: _env_bool("DBGPT_GOV_PARTITION_HINT", True)  # noqa: E731
STUCK_HINT = lambda: _env_bool("DBGPT_GOV_STUCK_HINT", True)  # noqa: E731
VERIFY_ON_TERMINATE = lambda: _env_bool("DBGPT_GOV_VERIFY_TERMINATE", True)  # noqa: E731
TELEMETRY = lambda: _env_bool("DBGPT_GOV_TELEMETRY", True)  # noqa: E731
ESCALATE_MODEL = lambda: (os.getenv("DBGPT_GOV_ESCALATE_MODEL") or "").strip()  # noqa: E731
DATA_TEMPERATURE = lambda: _env_float("DBGPT_GOV_DATA_TEMPERATURE", 0.0)  # noqa: E731
TH_AMBIGUITY = lambda: _env_float("DBGPT_GOV_TH_AMBIGUITY", 0.75)  # noqa: E731
TH_SUFFICIENT = lambda: _env_float("DBGPT_GOV_TH_SUFFICIENT", 0.7)  # noqa: E731
TH_RELEVANT = lambda: _env_float("DBGPT_GOV_TH_RELEVANT", 0.35)  # noqa: E731
MAX_JEV_CALLS = lambda: _env_int("DBGPT_GOV_MAX_JEV_CALLS", 6)  # noqa: E731
JEV_TIMEOUT = lambda: _env_float("DBGPT_GOV_JEV_TIMEOUT", 3.0)  # noqa: E731
STUCK_STEPS = lambda: _env_int("DBGPT_GOV_STUCK_STEPS", 4)  # noqa: E731


@dataclass
class Budget:
    steps: int
    max_tokens: int
    seconds: int


# Jev task_complexity is a 0..2 score (three criteria).
PROFILES: Dict[str, Budget] = {
    # max_tokens is only a ceiling (billing counts generated tokens), so keep
    # it generous: reasoning models spend part of it on hidden thinking, and
    # a too-low ceiling cuts the visible reply mid-sentence.
    "fast": Budget(steps=2, max_tokens=2048, seconds=60),
    "simple": Budget(steps=8, max_tokens=4096, seconds=150),
    "standard": Budget(steps=15, max_tokens=4096, seconds=300),
    "complex": Budget(steps=25, max_tokens=8192, seconds=480),
}


def profile_for(complexity: Optional[float]) -> str:
    if complexity is None:
        return "standard"
    if complexity < 0.8:
        return "simple"
    if complexity < 1.6:
        return "standard"
    return "complex"


# ── run state ───────────────────────────────────────────────────────────────
@dataclass
class Decision:
    checkpoint: str
    decider: str  # rule | jev | llm
    value: Any
    latency_ms: int = 0
    round: Optional[int] = None


@dataclass
class Evidence:
    round: int
    sql: str
    columns: List[str]
    row_count: int
    preview: List[List[str]]
    text: str


@dataclass
class RunState:
    conv_id: str = ""
    question: str = ""
    database: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    intent: Optional[str] = None
    intent_source: Optional[str] = None
    ambiguity: Optional[float] = None
    complexity: Optional[float] = None
    expects_report: bool = False
    route: str = "legacy"
    route_reason: str = ""
    profile: str = "standard"
    clarify_first: bool = False
    steps: int = 0
    sql_ok: int = 0
    sql_errors: int = 0
    consecutive_errors: int = 0
    steps_without_evidence: int = 0
    jev_calls: int = 0
    repairs: int = 0
    escalated_to: Optional[str] = None
    early_exit_hinted: bool = False
    evidence: List[Evidence] = field(default_factory=list)
    decisions: List[Decision] = field(default_factory=list)
    partition_hinted: List[str] = field(default_factory=list)

    @property
    def budget(self) -> Budget:
        return PROFILES.get(self.profile, PROFILES["standard"])

    def decide(self, checkpoint: str, decider: str, value: Any, latency_ms: int = 0) -> None:
        self.decisions.append(
            Decision(checkpoint, decider, value, int(latency_ms), self.steps or None)
        )
        logger.info("Governor %s by %s: %s (%d ms)", checkpoint, decider, str(value)[:200], int(latency_ms))

    def describe(self) -> str:
        b = self.budget
        return (
            f"Governor: route={self.route} ({self.route_reason}), profile={self.profile} "
            f"(≤{b.steps} steps, ≤{b.seconds}s)"
            + (", clarify-first" if self.clarify_first else "")
        )


# ── intake: routing ─────────────────────────────────────────────────────────
def decide_route(
    state: RunState,
    *,
    triage: Optional[Dict[str, Any]],
    rule_intent: Optional[str],
    user_input: str,
    has_db: bool,
    has_skill: bool,
    has_kb: bool,
    has_file: bool,
    has_connectors: bool,
    has_history: bool,
) -> RunState:
    """Single intent decision for the run (Jev + explicit wording), then route."""
    from .liara_jev_auto import (
        _DATABASE_QUERY_KEYWORDS,
        is_trivial_user_input,
        strip_context_tags,
    )

    triage = triage if isinstance(triage, dict) else {}
    state.intent = triage.get("intent") or rule_intent or "general_answer"
    state.intent_source = triage.get("intent_source") or ("rule" if rule_intent else "default")
    state.ambiguity = triage.get("needs_clarification")
    state.complexity = triage.get("complexity")
    state.expects_report = bool(triage.get("expects_report"))
    state.profile = profile_for(state.complexity)

    text = strip_context_tags(user_input or "")
    lowered = re.sub(r"\s+", " ", text.casefold()).strip()
    mentions_data = any(k in lowered for k in _DATABASE_QUERY_KEYWORDS)

    if not enabled():
        state.route, state.route_reason = "legacy", "governor disabled"
    elif (
        FAST_PATH()
        and not (has_skill or has_file or has_kb)
        and not mentions_data
        and (
            is_trivial_user_input(text)
            or (rule_intent == "general_answer" and len(lowered) <= 80)
        )
    ):
        state.route, state.route_reason = "fast", "small talk"
        state.profile = "fast"
    elif (
        not has_db
        and not (has_skill or has_kb or has_file or has_connectors)
        and (state.intent in ("database_query", "report_build") or mentions_data)
    ):
        # A data question with nothing to read from: say so in one cheap step
        # instead of letting the full agent guess schemas and fail.
        state.route, state.route_reason = "no_db", "data question but no database selected"
        state.profile = "fast"
    elif (
        DATA_MODE()
        and has_db
        and not (has_skill or has_kb or has_file or has_connectors)
        and state.intent in ("database_query", "report_build")
        and state.profile != "complex"
    ):
        state.route, state.route_reason = "data", f"{state.intent} on selected database"
    else:
        state.route, state.route_reason = "legacy", "full tool set needed"

    state.clarify_first = bool(
        CLARIFY()
        and state.route == "data"
        and not has_history
        and state.ambiguity is not None
        and state.ambiguity >= TH_AMBIGUITY()
    )
    state.decide("intake.route", "jev+rule", {"route": state.route, "profile": state.profile,
                                              "intent": state.intent, "ambiguity": state.ambiguity,
                                              "complexity": state.complexity})
    return state


# ── assemble: compact prompts ───────────────────────────────────────────────
_REACT_FORMAT = """## ReAct Output Format
Each round output exactly:
Thought: what to do next and why (short)
Action Intention: <= 8 English words
Action Reason: <= 12 English words
Action: one tool name from the list
Action Input: the JSON parameters of that tool

Exactly ONE Action per round, written only as the plain-text lines above (no
markup wrappers or special tokens)."""


def build_fast_prompt(identity_line: str, sections: str) -> str:
    return "\n\n".join(
        [
            identity_line,
            sections,
            """## How to reply
This is a short conversational message (greeting, thanks, who-are-you).
Reply briefly in the user's language without tools and without business facts.
Return the reply with the terminate tool.

## Tools
1. **terminate**: Finish with the reply. Parameters: {"result": "your reply"}""",
            _REACT_FORMAT,
        ]
    )


def build_no_db_prompt(identity_line: str, sections: str) -> str:
    return "\n\n".join(
        [
            identity_line,
            sections,
            """## How to reply
The user asked a question that needs company data, but NO database (and no
file or knowledge base) is selected for this conversation, so there is no data
to read. Do not answer the question, do not guess and do not give any numbers.
In the user's language, say briefly that no database has been selected for
this question, and ask them to choose a database from the data-source selector
next to the message box (or the «منابع داده» page) and ask again.
Persian example: «برای این سؤال دیتابیسی انتخاب نکرده‌اید. لطفاً از بخش انتخاب
منبع داده کنار کادر پیام، دیتابیس موردنظر را انتخاب کنید و دوباره بپرسید.»
Return the reply with the terminate tool.

## Tools
1. **terminate**: Finish with the reply. Parameters: {"result": "your reply"}""",
            _REACT_FORMAT,
        ]
    )


def build_data_prompt(
    *,
    identity_line: str,
    sections: str,
    database_context: str,
    clarify_first: bool,
    expects_report: bool,
) -> str:
    steps = [
        "1. Use the database catalog below (tables with data, their columns, observed "
        "values, date formats and links). Query INFORMATION_SCHEMA only for a table "
        "or column that is not described there.",
        "2. Write focused SELECT statements that compute the requested metric directly "
        "(aggregate in SQL; never fetch raw rows to count by eye).",
        "3. As soon as a query result answers the exact question, stop querying and "
        "terminate with the answer (numbers copied exactly from the result).",
        "4. If the data needed does not exist, say so clearly (what you checked) and "
        "terminate. Never invent or estimate data.",
    ]
    if expects_report:
        steps.append(
            "5. The user asked for a chart/report: call html_interpreter in SQL mode "
            "with the aggregated SELECT and a title — the server draws it — then "
            "terminate with a 1-2 sentence summary."
        )
    if clarify_first:
        steps.insert(
            0,
            "0. The request is ambiguous in a way that changes the result: BEFORE any "
            "query, call the question tool once with 2-4 concrete options (e.g. period, "
            "measure, product group), then continue with the user's choice.",
        )
    tools = """## Tools
1. **sql_query**: Run one read-only SELECT on the selected database.
   Parameters: {"sql": "SELECT ..."}
2. **html_interpreter** (SQL mode): Draw a chart + data table from a query result.
   Parameters: {"sql": "SELECT label, SUM(x) AS total ... GROUP BY ... ORDER BY ...",
   "title": "title", "chart": "line|bar|pie (optional)"}
   Use only when a chart is requested or a table cannot convey a trend.
3. **question**: Ask the user one clarifying question and wait for the answer.
   Parameters: {"questions": [{"question": "...", "header": "...",
   "options": [{"label": "...", "description": "..."}]}]}
4. **terminate**: Finish with the final answer. Parameters: {"result": "final answer"}"""
    return "\n\n".join(
        [
            identity_line,
            sections,
            "## How to work (database question)\n" + "\n".join(steps),
            tools,
            database_context.strip(),
            _REACT_FORMAT,
        ]
    )


# ── execute: observation parsing ────────────────────────────────────────────
_SCHEMA_SQL = re.compile(r"\b(information_schema|sys\.(tables|columns|objects)|sqlite_master|pg_catalog)\b", re.I)
_AGG_SQL = re.compile(r"\b(sum|count|avg|min|max)\s*\(", re.I)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([\[\]\w\.\"`]+)", re.I)
_ROWS_OF = re.compile(r"of\s+([\d,]+)\s+rows", re.I)
_ERROR_HINT = re.compile(r"(error|invalid|failed|exception|timed out|not allowed)", re.I)


def _observation_text(observation: Any) -> str:
    text = observation if isinstance(observation, str) else json.dumps(observation, ensure_ascii=False, default=str)
    start = text.find("{")
    if start >= 0:
        try:
            payload = json.loads(text[start:])
            if isinstance(payload, dict) and isinstance(payload.get("chunks"), list):
                return "\n".join(str(c.get("content") or "") for c in payload["chunks"] if isinstance(c, dict))
        except Exception:
            pass
    return text


def parse_table(text: str) -> Optional[Tuple[List[str], List[List[str]], int]]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3 or not re.match(r"^\|\s*:?-{3,}", lines[1]):
        return None

    def cells(line: str) -> List[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    cols = cells(lines[0])
    rows = [r for r in (cells(ln) for ln in lines[2:]) if len(r) == len(cols)]
    m = _ROWS_OF.search(text or "")
    total = int(m.group(1).replace(",", "")) if m else len(rows)
    return cols, rows, total


def _sql_from_input(action_input: Any) -> str:
    if isinstance(action_input, dict):
        return str(action_input.get("sql") or "")
    s = str(action_input or "")
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return str(obj.get("sql") or "")
    except Exception:
        pass
    m = re.search(r'"sql"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
    return bytes(m.group(1), "utf-8").decode("unicode_escape") if m else s


def _catalog_key(catalog: Dict[str, Any], raw: str) -> Optional[str]:
    tables = catalog.get("tables") or {}
    name = raw.strip().strip(";").replace("[", "").replace("]", "").replace('"', "").replace("`", "")
    if name in tables:
        return name
    low = name.lower()
    leaf = low.split(".")[-1]
    exact = [k for k in tables if k.lower() == low]
    if exact:
        return exact[0]
    same_leaf = [k for k in tables if k.lower().split(".")[-1] == leaf]
    return same_leaf[0] if len(same_leaf) == 1 else None


def partition_hint(catalog: Optional[Dict[str, Any]], sql: str, already: List[str]) -> Optional[str]:
    """Aggregate over a table that has an unfiltered low-cardinality column."""
    if not catalog or not sql or not _AGG_SQL.search(sql):
        return None
    sql_low = sql.lower()
    notes = []
    for raw in _TABLE_REF.findall(sql)[:3]:
        key = _catalog_key(catalog, raw)
        if not key or key in already:
            continue
        t = (catalog.get("tables") or {}).get(key) or {}
        found = []
        for c in t.get("columns", []):
            p = c.get("profile") or {}
            values = p.get("values") or []
            if not (2 <= len(values) <= 8) or p.get("values_source") != "exact":
                continue
            if re.search(r"\b" + re.escape(str(c.get("name", "")).lower()) + r"\b", sql_low):
                continue
            total = sum(int(n or 0) for _, n in values) or 1
            if max(int(n or 0) for _, n in values) / total > 0.995:
                continue
            shown = ", ".join(f"'{v}' ({int(n):,})" for v, n in values[:6])
            found.append(f"`{c['name']}` = {shown}")
            if len(found) >= 3:
                break
        if found:
            already.append(key)
            notes.append(f"{key}: " + "; ".join(found))
    if not notes:
        return None
    return (
        "[Check] Your aggregate mixes categories that the table distinguishes — "
        + " | ".join(notes)
        + ". Decide from the question whether one of these must be filtered or grouped "
        "(for example document type, status, voided/archived). If yes, re-run with "
        "that filter; if not, continue."
    )


def columns_hint(catalog: Optional[Dict[str, Any]], sql: str) -> Optional[str]:
    if not catalog:
        return None
    parts = []
    for raw in _TABLE_REF.findall(sql or "")[:3]:
        key = _catalog_key(catalog, raw)
        if not key:
            continue
        cols = [c.get("name") for c in ((catalog.get("tables") or {}).get(key) or {}).get("columns", [])]
        if cols:
            parts.append(f"{key}: " + ", ".join(str(c) for c in cols[:60]))
    if not parts:
        return None
    return "[Check] The last queries failed. Real columns from the catalog — " + " | ".join(parts)


# ── Jev checkpoints ─────────────────────────────────────────────────────────
_SUFFICIENT_Q = {
    "answers_question": {
        "type": "noul",
        "instructions": (
            "Read the user's question and the SQL result. Does this result already "
            "contain the final answer — the same measure, the same grouping, the same "
            "time range and filters — so that the answer can be written from this "
            "result alone, with no further query? Answer near 1 only in that case."
        ),
        "criteria": {
            "true": "The result directly answers the question.",
            "false": "Something is still missing, different or unverified.",
        },
    }
}
_RELEVANT_Q = {
    "answer_relevant": {
        "type": "noul",
        "instructions": (
            "Does the final answer address the user's exact question (same measure, "
            "dimensions and period) and is it supported by the SQL evidence shown? "
            "Answer near 0 if it answers a different question or is unsupported."
        ),
        "criteria": {
            "true": "Relevant and supported by the evidence.",
            "false": "Different question, or not supported by the evidence.",
        },
    }
}


def _jev_state_text(payload: Dict[str, Any], limit: int = 6000) -> str:
    """Render a checkpoint payload as plain text (the form Jev accepts)."""
    lines: List[str] = []
    for key, value in payload.items():
        if value is None or value == "" or value == []:
            continue
        if key == "rows" and isinstance(value, list):
            lines.append("rows:")
            for row in value:
                cells = row if isinstance(row, (list, tuple)) else [row]
                lines.append("  " + " | ".join(str(c).strip() for c in cells))
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}: " + ", ".join(str(v).strip() for v in value))
        else:
            lines.append(f"{key}: {str(value).strip()}")
    return "\n".join(lines)[:limit]


async def _jev_noul(state: RunState, payload: Dict[str, Any], questions: Dict[str, Any], name: str) -> Tuple[Optional[float], int]:
    if state.jev_calls >= MAX_JEV_CALLS():
        return None, 0
    from .liara_jev import evaluate_liara_jev

    state.jev_calls += 1
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(evaluate_liara_jev(_jev_state_text(payload), questions), JEV_TIMEOUT())
        ans = ((result or {}).get("answers") or {}).get(name) or {}
        value = ans.get("noul")
        return (float(value) if value is not None else None), int((time.monotonic() - started) * 1000)
    except Exception:
        logger.info("Governor Jev checkpoint %s skipped (fail-open)", name, exc_info=True)
        return None, int((time.monotonic() - started) * 1000)


# ── execute: step policy ────────────────────────────────────────────────────
_NON_PROGRESS_ACTIONS = {"todowrite", "question", "liara_jev_decision"}


class StepPolicy:
    """Called by the agent after every step (see ``ConversableAgent.step_policy``)."""

    def __init__(
        self,
        state: RunState,
        *,
        catalog_loader: Callable[[], Optional[Dict[str, Any]]] = lambda: None,
    ) -> None:
        self.state = state
        self._catalog_loader = catalog_loader
        self._catalog: Optional[Dict[str, Any]] = None
        self._catalog_loaded = False

    @property
    def catalog(self) -> Optional[Dict[str, Any]]:
        if not self._catalog_loaded:
            self._catalog_loaded = True
            try:
                self._catalog = self._catalog_loader()
            except Exception:
                self._catalog = None
        return self._catalog

    async def __call__(self, ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = self.state
        st.steps = int(ev.get("round") or st.steps + 1)
        action = str(ev.get("action") or "").strip()
        if ev.get("terminal"):
            return await self._on_terminate(ev)
        if action == "sql_query":
            return await self._on_sql(ev)
        if action not in _NON_PROGRESS_ACTIONS:
            st.steps_without_evidence += 1
        return await self._progress_check(ev)

    async def _on_sql(self, ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = self.state
        sql = _sql_from_input(ev.get("action_input"))
        text = _observation_text(ev.get("observation"))
        table = parse_table(text)
        if not ev.get("success") or table is None:
            if table is None and _ERROR_HINT.search(text[:600]):
                st.sql_errors += 1
                st.consecutive_errors += 1
                st.steps_without_evidence += 1
                st.decide("execute.sql_error", "rule", text[:160])
                if st.consecutive_errors >= 2:
                    hint = columns_hint(self.catalog, sql)
                    if hint:
                        st.consecutive_errors = 0
                        return {"hint": hint}
            else:
                st.steps_without_evidence += 1
            return await self._progress_check(ev)

        cols, rows, total = table
        st.consecutive_errors = 0
        if _SCHEMA_SQL.search(sql) or not rows:
            st.steps_without_evidence += 1
            return await self._progress_check(ev)
        st.sql_ok += 1
        st.steps_without_evidence = 0
        st.evidence.append(Evidence(st.steps, sql[:4000], cols, total, rows[:10], text[:6000]))

        part = partition_hint(self.catalog, sql, st.partition_hinted) if PARTITION_HINT() else None
        if part:
            # Resolve the category question first; sufficiency is checked on
            # the next result (no Jev call, no contradicting "finish now").
            st.decide("execute.partition_check", "rule", part[:200])
            return {"hint": part}
        if not SUFFICIENCY() or st.route not in ("data", "legacy"):
            return None
        value, ms = await _jev_noul(
            st,
            {"question": st.question, "sql": sql[:2000], "columns": cols,
             "rows": rows[:12], "total_rows": total},
            _SUFFICIENT_Q,
            "answers_question",
        )
        st.decide("execute.sufficiency", "jev", value, ms)
        if value is not None and value >= TH_SUFFICIENT():
            st.early_exit_hinted = True
            if st.expects_report:
                finish = (
                    "[Governor] This result answers the question. Next: call "
                    "html_interpreter in SQL mode with this SELECT and a title, then "
                    "terminate with a 1-2 sentence summary. Do not run other queries."
                )
            else:
                finish = (
                    "[Governor] This result answers the question. Do not run more "
                    "queries: your next action MUST be terminate with the direct answer "
                    "(numbers copied exactly from the result, a compact table only if "
                    "there are several rows, then one source line)."
                )
            return {"hint": finish}
        return None

    async def _progress_check(self, ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = self.state
        if st.route != "data" or not STUCK_HINT() or st.steps_without_evidence < STUCK_STEPS():
            return None
        st.steps_without_evidence = 0
        st.decide("execute.stuck", "rule", st.steps)
        await self._maybe_escalate(ev)
        return {
            "hint": (
                "[Governor] Several steps passed without new query results. Change "
                "approach: pick tables from the catalog in the system prompt (it already "
                "shows columns and values) and compute the metric with one aggregated "
                "SELECT. If the needed data does not exist, say so and terminate."
            )
        }

    async def _maybe_escalate(self, ev: Dict[str, Any]) -> None:
        st = self.state
        target = ESCALATE_MODEL()
        agent = ev.get("agent")
        if not target or st.escalated_to or agent is None:
            return
        try:
            models = await agent.not_null_llm_client.models()
            if target not in [m.model for m in models]:
                st.decide("execute.escalate", "rule", f"{target} not deployed")
                return
            cfg = agent.not_null_llm_config
            from dbgpt.agent.util.llm.llm import LLMStrategyType

            cfg.llm_strategy = LLMStrategyType.Priority
            cfg.strategy_context = json.dumps([target])
            st.escalated_to = target
            st.decide("execute.escalate", "rule", target)
        except Exception:
            logger.info("Governor escalation skipped", exc_info=True)

    async def _on_terminate(self, ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = self.state
        if not VERIFY_ON_TERMINATE() or st.route != "data" or st.repairs >= 1 or not st.evidence:
            return None
        answer = _terminate_text(ev.get("action_input"))
        if not answer:
            return None
        problems: List[str] = []
        try:
            from .answer_numbers import check_answer_numbers

            _fixed, unverified, _rewritten = check_answer_numbers(
                answer, [e.text for e in st.evidence] + [e.sql for e in st.evidence] + [st.question]
            )
            if unverified:
                problems.append(
                    "these numbers do not appear in any query result: " + ", ".join(map(str, unverified[:6]))
                )
        except Exception:
            logger.debug("Governor number check skipped", exc_info=True)
        last = st.evidence[-1]
        value, ms = await _jev_noul(
            st,
            {"question": st.question, "final_answer": answer[:2500], "sql": last.sql[:1500],
             "columns": last.columns, "rows": last.preview[:8]},
            _RELEVANT_Q,
            "answer_relevant",
        )
        st.decide("verify.relevance", "jev", value, ms)
        if value is not None and value < TH_RELEVANT():
            problems.append("the answer does not seem to address the exact question asked")
        if not problems:
            return None
        st.repairs += 1
        st.decide("verify.repair", "rule", problems)
        return {
            "continue": True,
            "hint": (
                "[Governor] The final answer was not accepted: "
                + "; ".join(problems)
                + ". Fix it using only the query results (re-query if needed), then "
                "terminate again."
            ),
        }


def _terminate_text(action_input: Any) -> str:
    if isinstance(action_input, dict):
        return str(action_input.get("result") or "")
    s = str(action_input or "")
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return str(obj.get("result") or "")
    except Exception:
        pass
    return s


# ── telemetry ───────────────────────────────────────────────────────────────
def _telemetry_dir() -> str:
    base = os.getenv("DBGPT_GOV_TELEMETRY_DIR")
    if not base:
        try:
            from dbgpt.configs.model_config import PILOT_PATH

            base = os.path.join(PILOT_PATH, "meta_data", "run_telemetry")
        except Exception:
            base = os.path.join(os.getcwd(), "pilot", "meta_data", "run_telemetry")
    os.makedirs(base, exist_ok=True)
    return base


def write_telemetry(state: RunState, **extra: Any) -> Optional[str]:
    """Append one JSON line describing the run. Never raises."""
    if not TELEMETRY():
        return None
    try:
        record = asdict(state)
        record["evidence"] = [
            {"round": e.round, "sql": e.sql[:500], "columns": e.columns, "row_count": e.row_count}
            for e in state.evidence
        ]
        record["elapsed_s"] = round(time.time() - state.started_at, 2)
        record["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        record.update(extra)
        path = os.path.join(_telemetry_dir(), f"{_dt.date.today().isoformat()}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return path
    except Exception:
        logger.debug("Governor telemetry write failed", exc_info=True)
        return None
