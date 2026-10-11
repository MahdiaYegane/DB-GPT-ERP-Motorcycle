"""Jev Governor for the ReAct agent (fail-open, three-phase).

Architecture
------------
::

    Pre-Jev (triage)          Mid-Jev (guard)            Post-Jev (verifier + repair)
    User input -> [Jev] -> workflow prompt -> agent loop -> [Jev verify] -> final
                        (+ SSE card)        |  (SSE guard)   (+ fallback HTML)
                                            liara_jev_decision tool (manual)

Phase 1 — Pre (triage, advisory only):
    Classify intent / clarification / complexity / visualization need.
    Only contributes a hint section to the system prompt + visible step card.

Phase 2 — Mid (guard, advisory only):
    While the agent loop runs, count tool steps. When the triage expects a
    visual report (``intent == report_build`` or ``needs_visualization >= 0.6``)
    and no ``html_interpreter`` output was observed after N steps, emit a
    visible SSE guard step reminding the model to call ``html_interpreter``.
    Never blocks, never rewrites tool calls.

Phase 3 — Post (verifier + repair, fail-open):
    After the agent loop ends, check whether an HTML report was actually
    rendered (``history_has_html``). When a report was expected but no HTML
    exists, Jev verifies the gap and the governor synthesizes a fallback HTML
    report from the collected observations + generated images so the final
    output ALWAYS contains a chart/report instead of plain text.

Every failure (missing key, timeout, transport error, invalid payload)
degrades gracefully: triage -> no hint, guard -> no reminder, verifier ->
heuristic fallback. The normal ReAct flow never breaks because of Jev.

Configuration (environment variables)
-------------------------------------
- ``LIARA_JEV_AUTO_ENABLE``: ``1`` (default) to enable triage, ``0`` to disable.
- ``LIARA_JEV_AUTO_TIMEOUT``: seconds for the triage call (default ``8.0``).
- ``LIARA_JEV_TRIAGE_MAX_CHARS``: triage state budget (default ``2000``;
  verification keeps the larger ``LIARA_JEV_MAX_STATE_CHARS`` budget).
- ``LIARA_JEV_REPORT_THRESHOLD``: ``noul`` threshold for report detection
  (default ``0.6``; tune against a golden set, see ``docs/`` note below).
- ``LIARA_JEV_VERIFY_ENABLE``: ``1`` (default) to enable post verification.
- ``LIARA_JEV_VERIFY_TIMEOUT``: seconds for verify call (default ``4.0``).
- ``LIARA_JEV_FALLBACK_REPORT``: ``1`` (default) to synthesize fallback HTML.
- ``LIARA_JEV_MID_REMINDER_EVERY``: tool steps between mid reminders (default ``5``).
- ``LIARA_JEV_MAX_STATE_CHARS``: max state characters sent (default ``6000``).
- ``LIARA_API_KEY`` / ``LIARA_API_BASE``: reused from the Liara chat setup.

Latency notes (P0): triage launches BEFORE the model-window probe in
``agentic_data_api`` so the two network calls overlap; trivial greetings skip
triage entirely (``should_skip_jev_triage``); the HTTP layer reuses one
keepalive client (``liara_jev._get_shared_jev_client``).
"""

import asyncio
import html as _html_lib
import json as _json_lib
import logging
import os
import re
import time as _time_mod
from typing import Any, Dict, List, Optional, Tuple, Union

from .liara_jev import evaluate_liara_jev

logger = logging.getLogger(__name__)

TRIAGE_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "intent": {
        "type": "choice",
        "instructions": (
            "Classify the user's request into exactly one category. "
            "Answer with the option that best describes what the user wants. "
            "Questions asking a factual aggregate from a database (for example "
            "which province had the highest sales) are database_query, NOT "
            "report_build unless a report/chart/dashboard is explicitly asked. "
            "Persian examples: 'چه استان هایی بیشترین فروش را داشتند؟' is "
            "database_query; 'گزارش فروش را بساز' is report_build."
        ),
        "criteria": {
            "skill_task": "Run a named skill or scripted workflow.",
            "database_query": (
                "Answer a factual question or perform an aggregation over the "
                "selected database using SQL. Do not make a report unless requested. "
                "(FA: فروش، استان، شهر، بیشترین، کمترین، چند، جمع)"
            ),
            "knowledge_search": "Search or read the knowledge base.",
            "file_analysis": "Analyze an uploaded file (CSV/Excel/PDF).",
            "report_build": "Build a report, dashboard, or visualization. "
            "(FA: گزارش، نمودار، داشبورد، جدول، رندر، نمایش)",
            "general_answer": "Answer directly without tools.",
        },
    },
    "needs_clarification": {
        "type": "noul",
        "instructions": (
            "Is the request ambiguous or missing information required to act? "
            "Answer near 1 when a clarifying question is needed before tool calls."
        ),
        "criteria": {
            "true": "Ambiguous or missing critical details.",
            "false": "Clear enough to start working.",
        },
    },
    "task_complexity": {
        "type": "score",
        "instructions": "How complex is this request on the given scale?",
        "criteria": [
            "Simple single-step request.",
            "Multi-step task needing tools.",
            "Complex analysis needing planning, code, and verification.",
        ],
    },
    "needs_visualization": {
        "type": "noul",
        "instructions": (
            "Does the user expect a visual deliverable (HTML report, dashboard, "
            "chart, table visualization, rendered page)? Answer near 1 when the "
            "final answer should include a rendered HTML report or chart, near "
            "0 for pure text answers. "
            "Persian requests such as 'نمودار بده' or 'گزارش بساز' count as "
            "near 1."
        ),
        "criteria": {
            "true": "User asked for a report, chart, dashboard, visualization, "
            "web page, or rendered output. (FA: نمودار، گزارش، داشبورد)",
            "false": "Plain text answer is sufficient.",
        },
    },
}

# Post-loop verification questions: Jev judges the finished turn.
VERIFICATION_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "report_expected": {
        "type": "noul",
        "instructions": (
            "Given the user request, was a visual report (HTML page, dashboard, "
            "chart) expected as the final deliverable? Answer near 1 when the "
            "user asked for a report/visualization/rendered output. "
            "Persian requests such as 'نمودار بده' or 'گزارش بساز' count as "
            "near 1."
        ),
        "criteria": {
            "true": "Report, dashboard, chart, visualization, HTML page requested. "
            "(FA: نمودار، گزارش، داشبورد)",
            "false": "Plain text answer is sufficient.",
        },
    },
    "html_delivered": {
        "type": "noul",
        "instructions": (
            "Given the tool execution summary, was an HTML report actually "
            "rendered and delivered (html_interpreter called successfully with "
            "non-empty HTML)? Answer near 1 only when HTML output exists."
        ),
        "criteria": {
            "true": "HTML was rendered via html_interpreter or file mode.",
            "false": "No HTML output was produced.",
        },
    },
    "answer_completeness": {
        "type": "score",
        "instructions": "How complete is the final answer on the given scale?",
        "criteria": [
            "Empty or truncated tool-call trace, nothing user-facing.",
            "Partial answer: analysis exists but the visual report is missing.",
            "Complete answer: analysis plus rendered HTML report (or no report needed).",
        ],
    },
    "answer_relevant": {
        "type": "noul",
        "instructions": (
            "Compare the user's exact question with the SQL statements, query "
            "result column names/values, and final answer. Answer near 1 only "
            "if the SQL evidence and final answer address the same requested "
            "metric and dimensions. Answer near 0 if the user asks about sales "
            "by province but the evidence/report is about inventory, stock, or "
            "product groups. Never infer relevance merely because both concern "
            "the same database."
        ),
        "criteria": {
            "true": "Evidence and answer match the requested metric and dimensions.",
            "false": "Answer or evidence is about a different metric/topic.",
        },
    },
    "sql_evidence_present": {
        "type": "noul",
        "instructions": (
            "For a database question, did a successful SQL query produce "
            "evidence relevant to the exact measure and dimensions asked? "
            "If the question asks sales by province, require sales/revenue "
            "evidence grouped by province/state; inventory balance or stock "
            "by product group is not relevant."
        ),
        "criteria": {
            "true": "Successful SQL output matches the requested metric and dimensions.",
            "false": "SQL failed, no SQL was run, or result answers another topic.",
        },
    },
}

_INTENT_TOOL_HINTS = {
    "skill_task": "prefer load_skill / load_tools and follow the skill steps",
    "database_query": "prefer sql_query against the selected database",
    "knowledge_search": "prefer kb_ls / kb_glob / kb_grep / kb_cat / semantic_search",
    "file_analysis": "prefer load_file then execute_analysis / code_interpreter",
    "report_build": "prefer html_interpreter for the final report",
    "general_answer": "answer directly; avoid unnecessary tool calls",
}

# Keywords (EN/FA/ZH) that strongly suggest a visual report was requested.
_REPORT_KEYWORDS = (
    "report",
    "dashboard",
    "chart",
    "graph",
    "plot",
    "visual",
    "html",
    "نمودار",
    "چارت",
    "گراف",
    "گزارش",
    "داشبورد",
    "رندر",
    "تصویری",
    "图表",
    "报告",
    "报表",
    "可视化",
    "渲染",
    "展示",
)

_DATABASE_QUERY_KEYWORDS = (
    "sql",
    "database",
    "query",
    "data",
    "sales",
    "sale",
    "revenue",
    "sold",
    "invoice",
    "order",
    "province",
    "provinces",
    "region",
    "city",
    "customer",
    "فروش",
    "فروخته",
    "درآمد",
    "فاکتور",
    "سفارش",
    "استان",
    "استانها",
    "شهر",
    "منطقه",
    "مشتری",
    "دیتابیس",
    "پایگاه داده",
    "کوئری",
    "بیشترین",
    "کمترین",
    "جمع",
    "میانگین",
    "تعداد",
    "فصلی",
    "ماهانه",
)


def classify_request_intent(user_input: Any) -> Optional[str]:
    """Apply high-precision intent rules before trusting Jev's softer score.

    Jev remains the general classifier. Explicit user wording is authoritative:
    a direct question about database facts such as sales by province is a
    ``database_query`` and must not be converted into an unrelated report or
    skill workflow. Visual/report keywords opt into report presentation.
    """
    try:
        text = user_input if isinstance(user_input, str) else str(user_input or "")
        lowered = re.sub(r"\s+", " ", text.casefold()).strip()
        if not lowered:
            return None
        if _mentions_report(lowered):
            return "report_build"
        if any(keyword in lowered for keyword in _DATABASE_QUERY_KEYWORDS):
            return "database_query"
    except Exception:
        return None
    return None


def is_jev_auto_enabled() -> bool:
    """Return True unless the operator explicitly disabled automatic triage."""
    return os.getenv("LIARA_JEV_AUTO_ENABLE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def is_jev_verify_enabled() -> bool:
    """Return True unless post-loop verification was explicitly disabled."""
    return os.getenv("LIARA_JEV_VERIFY_ENABLE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def is_jev_fallback_enabled() -> bool:
    """Return True unless fallback HTML synthesis was explicitly disabled."""
    return os.getenv("LIARA_JEV_FALLBACK_REPORT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def get_jev_auto_timeout() -> float:
    """Return the triage timeout in seconds (default 8.0, fail-open)."""
    try:
        return max(1.0, float(os.getenv("LIARA_JEV_AUTO_TIMEOUT", "8.0")))
    except (TypeError, ValueError):
        return 8.0


def get_jev_verify_timeout() -> float:
    """Return the verification timeout in seconds (default 4.0, fail-open).

    Kept short on purpose: verification runs AFTER the agent loop, directly
    delaying the final answer. On timeout the heuristic fallback decides.
    """
    try:
        return max(1.0, float(os.getenv("LIARA_JEV_VERIFY_TIMEOUT", "4.0")))
    except (TypeError, ValueError):
        return 4.0


def get_jev_report_threshold() -> float:
    """Return the ``noul`` threshold for report detection (default 0.6).

    Single knob used by triage (``expects_report``) and the post-loop repair
    decision. Tune against a golden set of report-seeking vs plain-text
    requests (especially Persian) instead of guessing.
    """
    try:
        raw = float(os.getenv("LIARA_JEV_REPORT_THRESHOLD", "0.6"))
    except (TypeError, ValueError):
        return 0.6
    return min(0.95, max(0.1, raw))


def get_jev_triage_max_chars() -> int:
    """Return the triage state budget (default 2000, clamped 500..6000).

    Triage only needs the request gist; the larger ``LIARA_JEV_MAX_STATE_CHARS``
    budget is reserved for post-loop verification state.
    """
    try:
        raw = int(os.getenv("LIARA_JEV_TRIAGE_MAX_CHARS", "2000"))
    except (TypeError, ValueError):
        return 2000
    return min(6000, max(500, raw))


# Inputs so trivial that triage adds nothing but latency (P0 skip).
_TRIVIAL_GREETING_PATTERN = re.compile(
    r"^\s*(hi+|hello+|hey+|yo|سلام+|درود|صبح\s*بخیر|عصر\s*بخیر|شب\s*بخیر|"
    r"ممنون+|مرسی+|متشکرم|تشکر|سپاس|thanks?|thank\s*you|thx|ok(ay)?|باشه|"
    r"چشم|حله|bye+|goodbye|خداحافظ|فعلا|خدانگهدار)[\s!.؟?،,]*$",
    re.IGNORECASE,
)


def is_trivial_user_input(user_input: Any) -> bool:
    """Return True for greetings/thanks/fragments not worth a Jev call."""
    try:
        text = user_input if isinstance(user_input, str) else str(user_input or "")
        text = text.strip()
        if not text or len(text) < 4:
            return True
        return _TRIVIAL_GREETING_PATTERN.match(text) is not None
    except Exception:
        return False


def should_skip_jev_triage(user_input: Any) -> bool:
    """Return True when the triage call should be skipped (fail-open).

    Report-seeking inputs NEVER skip, even when short. Everything else that
    is trivial (greetings, thanks, fragments) skips — the outcome would be
    ``general_answer`` with no report block anyway.
    """
    try:
        if _mentions_report(user_input):
            return False
        return is_trivial_user_input(user_input)
    except Exception:
        return False


def get_jev_mid_reminder_every_for(summary: Optional[Dict[str, Any]]) -> int:
    """Return adaptive mid-loop reminder cadence for a triage summary.

    High-confidence triage keeps the base cadence; low-confidence triage
    (``intent_confidence < 0.5``) reminds less often to avoid nagging the
    model on a possibly-wrong verdict.
    """
    base = get_jev_mid_reminder_every()
    try:
        conf = (summary or {}).get("intent_confidence")
        if isinstance(conf, float) and conf < 0.5:
            return base + 3
    except Exception:
        pass
    return base


def get_jev_mid_reminder_every() -> int:
    """Return tool steps between mid-loop reminders (default 5)."""
    try:
        return max(2, int(os.getenv("LIARA_JEV_MID_REMINDER_EVERY", "5")))
    except (TypeError, ValueError):
        return 5


def get_jev_max_state_chars() -> int:
    """Return the max state characters sent to Jev (default 6000)."""
    try:
        return max(500, int(os.getenv("LIARA_JEV_MAX_STATE_CHARS", "6000")))
    except (TypeError, ValueError):
        return 6000


def build_jev_triage_state(
    user_input: Any,
    *,
    skill_name: Optional[str] = None,
    database_name: Optional[str] = None,
    knowledge_space: Optional[str] = None,
    has_file: bool = False,
    max_chars: Optional[int] = None,
    extra_hints: Optional[List[str]] = None,
) -> str:
    """Build a compact Jev state string from the user turn context.

    ``max_chars`` defaults to the small triage budget (``2000``), not the
    global ``LIARA_JEV_MAX_STATE_CHARS`` — triage only needs the gist.
    ``extra_hints`` carries cheap disambiguating context (e.g. how many
    skills/connectors are available) so ``skill_task`` vs ``report_build``
    vs ``database_query`` mix up less often.
    """
    text = user_input if isinstance(user_input, str) else str(user_input or "")
    hints: List[str] = []
    if skill_name:
        hints.append(f"selected skill: {skill_name}")
    if database_name:
        hints.append(f"selected database: {database_name}")
    if knowledge_space:
        hints.append(f"selected knowledge space: {knowledge_space}")
    if has_file:
        hints.append("an input file is attached")
    try:
        for hint in extra_hints or []:
            if isinstance(hint, str) and hint.strip():
                hints.append(hint.strip()[:200])
    except Exception:
        pass
    budget_chars = max_chars if isinstance(max_chars, int) and max_chars > 0 \
        else get_jev_triage_max_chars()
    suffix = ("\n[context: " + "; ".join(hints) + "]") if hints else ""
    # Truncate the user text first so selection context is never cut off.
    budget = max(200, budget_chars - len(suffix) - len("\n[truncated]"))
    text = text.strip()
    truncated = len(text) > budget
    if truncated:
        text = text[:budget]
    state = text + suffix
    if truncated:
        state += "\n[truncated]"
    return state


def build_jev_triage_questions() -> Dict[str, Dict[str, Any]]:
    """Return a fresh copy of the fixed triage question set."""
    return {name: dict(question) for name, question in TRIAGE_QUESTIONS.items()}


def build_jev_verification_questions() -> Dict[str, Dict[str, Any]]:
    """Return a fresh copy of the post-loop verification question set."""
    return {name: dict(question) for name, question in VERIFICATION_QUESTIONS.items()}


async def run_jev_triage(
    state: Union[str, Dict[str, Any], List[Any]],
    *,
    questions: Optional[Dict[str, Dict[str, Any]]] = None,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Run automatic Jev triage, returning None on any failure (fail-open).

    Returns the raw Jev decision payload (with an ``answers`` dict) or None
    when triage is disabled, unconfigured, slow, or erroneous.
    """
    if not is_jev_auto_enabled():
        return None
    if isinstance(state, str) and not state.strip():
        return None
    started = _time_mod.monotonic()
    try:
        triage_questions = (
            questions if questions is not None else build_jev_triage_questions()
        )
        result = await asyncio.wait_for(
            evaluate_liara_jev(state, triage_questions),
            timeout=timeout if timeout is not None else get_jev_auto_timeout(),
        )
    except Exception:
        logger.warning("Automatic Jev triage skipped (fail-open)", exc_info=True)
        return None
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        logger.warning("Automatic Jev triage returned an invalid payload")
        return None
    logger.info(
        "Jev triage ok in %.2fs",
        _time_mod.monotonic() - started,
    )
    return result


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    if result in (float("inf"), float("-inf")):
        return None
    return result


def _mentions_report(text: Any) -> bool:
    if not isinstance(text, str) or not text:
        return False
    lowered = text.casefold()
    return any(keyword in lowered for keyword in _REPORT_KEYWORDS)


def summarize_jev_triage(
    result: Dict[str, Any], *, user_input: Any = None
) -> Dict[str, Any]:
    """Summarize Jev triage, with explicit user wording taking precedence.

    Jev's probabilistic scores are useful for ambiguous requests, but must not
    turn a factual database question into an unrelated report. For example,
    "چه استان‌هایی بیشترین فروش را داشتند؟" is deterministically a database
    query even if Jev gives ``report_build`` a high score.
    """
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        answers = {}

    intent = "general_answer"
    intent_confidence: Optional[float] = None
    intent_answer = answers.get("intent")
    if isinstance(intent_answer, dict):
        choice = intent_answer.get("choice")
        if isinstance(choice, str) and choice in _INTENT_TOOL_HINTS:
            intent = choice
        intent_confidence = _as_float(intent_answer.get("confidence"))

    needs_clarification: Optional[float] = None
    clarification_answer = answers.get("needs_clarification")
    if isinstance(clarification_answer, dict):
        needs_clarification = _as_float(clarification_answer.get("noul"))

    complexity: Optional[float] = None
    complexity_answer = answers.get("task_complexity")
    if isinstance(complexity_answer, dict):
        complexity = _as_float(complexity_answer.get("score"))

    needs_visualization: Optional[float] = None
    visualization_answer = answers.get("needs_visualization")
    if isinstance(visualization_answer, dict):
        needs_visualization = _as_float(visualization_answer.get("noul"))

    if complexity is None:
        complexity_label = "unknown"
    elif complexity < 0.8:
        complexity_label = "simple"
    elif complexity < 1.6:
        complexity_label = "multi-step"
    else:
        complexity_label = "complex"

    rule_intent = classify_request_intent(user_input)
    if rule_intent:
        intent = rule_intent
    expects_report = intent == "report_build" or (
        intent != "database_query"
        and needs_visualization is not None
        and needs_visualization >= get_jev_report_threshold()
    )

    return {
        "intent": intent,
        "intent_confidence": intent_confidence,
        "needs_clarification": needs_clarification,
        "complexity": complexity,
        "complexity_label": complexity_label,
        "needs_visualization": needs_visualization,
        "expects_report": expects_report,
        "intent_source": "explicit_request" if rule_intent else "jev",
        "model": result.get("model") if isinstance(result, dict) else None,
    }


def format_jev_guidance_for_prompt(summary: Dict[str, Any]) -> str:
    """Render the advisory Jev hint section injected into the system prompt."""
    intent = summary.get("intent") or "general_answer"
    tool_hint = _INTENT_TOOL_HINTS.get(intent, _INTENT_TOOL_HINTS["general_answer"])
    lines = [
        "## Automatic Input Triage (TypeSafe Jev 1.13 via Liara, advisory only)",
        f"- Detected intent: {intent} — {tool_hint}.",
    ]
    confidence = summary.get("intent_confidence")
    if isinstance(confidence, float):
        lines.append(f"- Intent confidence: {confidence:.2f}.")
    clarification = summary.get("needs_clarification")
    if isinstance(clarification, float):
        level = (
            "high — prefer the question tool before heavy work"
            if clarification >= 0.7
            else "low — proceed without asking unless tools fail"
        )
        lines.append(f"- Clarification need: {clarification:.2f} ({level}).")
    complexity = summary.get("complexity")
    complexity_label = summary.get("complexity_label") or "unknown"
    if isinstance(complexity, float):
        lines.append(
            f"- Complexity: {complexity:.2f} ({complexity_label}). "
            "Use todowrite for 3+ step work."
        )
    else:
        lines.append(f"- Complexity: {complexity_label}.")
    visualization = summary.get("needs_visualization")
    if isinstance(visualization, float):
        lines.append(f"- Visualization need: {visualization:.2f}.")
    # Hard enforcement block: this is what fixes "HTML never rendered".
    # The model MUST NOT terminate with plain text when a report is expected.
    if summary.get("expects_report"):
        lines.append(
            "- REPORT REQUIRED: the user expects a rendered HTML report. "
            "You MUST call `html_interpreter` with the complete HTML "
            "(DOCTYPE, head, body, styles, charts/tables) before `terminate`. "
            "Terminating with only text is a FAILURE for this task."
        )
    lines.append(
        "Jev is a hint, not ground truth. If tool observations contradict it, "
        "follow the observations."
    )
    return "\n".join(lines)


def render_jev_step_content(summary: Dict[str, Any]) -> str:
    """Render the visible step-card body for an automatic triage result."""
    parts = [f"intent={summary.get('intent', 'general_answer')}"]
    if isinstance(summary.get("intent_confidence"), float):
        parts.append(f"confidence={summary['intent_confidence']:.2f}")
    if isinstance(summary.get("needs_clarification"), float):
        parts.append(f"clarification={summary['needs_clarification']:.2f}")
    if isinstance(summary.get("complexity"), float):
        parts.append(
            f"complexity={summary['complexity']:.2f} "
            f"({summary.get('complexity_label', 'unknown')})"
        )
    if isinstance(summary.get("needs_visualization"), float):
        parts.append(f"visualization={summary['needs_visualization']:.2f}")
    if summary.get("expects_report"):
        parts.append("expects_report=yes")
    return "Jev triage: " + ", ".join(parts)


# ---------------------------------------------------------------------------
# Mid-loop guard
# ---------------------------------------------------------------------------


def build_jev_mid_reminder(summary: Dict[str, Any], steps_without_html: int) -> str:
    """Build the advisory mid-loop reminder text (never raises)."""
    try:
        intent = summary.get("intent", "general_answer")
    except Exception:
        intent = "general_answer"
    return (
        f"Jev guard: report expected (intent={intent}) but no HTML rendered "
        f"after {steps_without_html} tool steps. Call `html_interpreter` with "
        "the complete HTML next — do not terminate with text only."
    )


# ---------------------------------------------------------------------------
# Post-loop verifier + repair
# ---------------------------------------------------------------------------


def history_has_html(history_steps: Any) -> bool:
    """Return True when any history step carries non-empty HTML output."""
    try:
        steps = history_steps if isinstance(history_steps, list) else []
        for step in steps:
            if not isinstance(step, dict):
                continue
            for output in step.get("outputs") or []:
                if not isinstance(output, dict):
                    continue
                if output.get("output_type") != "html":
                    continue
                content = output.get("content")
                if isinstance(content, str) and content.strip():
                    # Guard against placeholder/empty shells.
                    if len(content.strip()) > 200:
                        return True
    except Exception:
        return False
    return False


def get_last_html_content(history_steps: Any) -> Optional[str]:
    """Return the most recent non-empty HTML output, if any."""
    try:
        steps = history_steps if isinstance(history_steps, list) else []
        for step in reversed(steps):
            if not isinstance(step, dict):
                continue
            for output in reversed(step.get("outputs") or []):
                if not isinstance(output, dict):
                    continue
                if output.get("output_type") != "html":
                    continue
                content = output.get("content")
                if isinstance(content, str) and len(content.strip()) > 200:
                    return content
    except Exception:
        return None
    return None


def _summarize_history_for_jev(
    history_steps: Any,
    *,
    max_steps: int = 12,
    max_chars: int = 3000,
) -> str:
    """Compress tool history into a short verification state string."""
    try:
        steps = history_steps if isinstance(history_steps, list) else []
        lines: List[str] = []
        for step in steps[-max_steps:]:
            if not isinstance(step, dict):
                continue
            action = step.get("action") or "unknown"
            status = step.get("status") or "unknown"
            outputs = step.get("outputs") or []
            kinds = ",".join(
                str(o.get("output_type"))
                for o in outputs
                if isinstance(o, dict) and o.get("output_type")
            )
            has_html = any(
                isinstance(o, dict)
                and o.get("output_type") == "html"
                and isinstance(o.get("content"), str)
                and len(o["content"].strip()) > 200
                for o in outputs
            )
            lines.append(
                f"- {action} [{status}] outputs=[{kinds or 'none'}]"
                + (" HTML_OK" if has_html else "")
            )
        text = "\n".join(lines) or "(no tool steps)"
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[truncated]"
        return text
    except Exception:
        return "(history unavailable)"


def build_jev_verification_state(
    user_input: Any,
    history_steps: Any,
    final_content: Any,
    *,
    has_html: Optional[bool] = None,
) -> str:
    """Build the verification state string (never raises)."""
    try:
        request = user_input if isinstance(user_input, str) else str(user_input or "")
        answer = (
            final_content
            if isinstance(final_content, str)
            else str(final_content or "")
        )
        max_chars = get_jev_max_state_chars()
        history_text = _summarize_history_for_jev(history_steps)
        evidence_lines: List[str] = []
        if isinstance(history_steps, list):
            for step in history_steps:
                if not isinstance(step, dict) or str(step.get("action", "")).lower() != "sql_query":
                    continue
                raw_input = step.get("action_input")
                if isinstance(raw_input, str):
                    try:
                        raw_input = _json_lib.loads(raw_input)
                    except Exception:
                        pass
                sql = raw_input.get("sql") if isinstance(raw_input, dict) else raw_input
                if isinstance(sql, str) and sql.strip():
                    evidence_lines.append("SQL: " + sql.strip()[:900])
                for output in step.get("outputs") or []:
                    if not isinstance(output, dict) or output.get("output_type") not in {
                        "markdown",
                        "text",
                    }:
                        continue
                    content = output.get("content")
                    if isinstance(content, str) and content.strip():
                        evidence_lines.append("SQL_RESULT: " + content.strip()[:900])
                        break
        evidence_text = "\n".join(evidence_lines[-12:]) or "(no SQL evidence)"
        resolved_has_html = (
            has_html if has_html is not None else history_has_html(history_steps)
        )
        state = (
            f"[user request]\n{request.strip()[:2000]}\n\n"
            f"[tool history]\n{history_text}\n\n"
            f"[SQL evidence]\n{evidence_text}\n\n"
            f"[html_rendered={bool(resolved_has_html)}]\n\n"
            f"[final answer draft]\n{answer.strip()[:2000]}"
        )
        if len(state) > max_chars:
            state = state[:max_chars] + "\n[truncated]"
        return state
    except Exception:
        return str(user_input or "")[:2000]


async def run_jev_verification(
    state: Union[str, Dict[str, Any], List[Any]],
    *,
    questions: Optional[Dict[str, Dict[str, Any]]] = None,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Run post-loop Jev verification, None on any failure (fail-open)."""
    if not is_jev_verify_enabled():
        return None
    if isinstance(state, str) and not state.strip():
        return None
    started = _time_mod.monotonic()
    try:
        verify_questions = (
            questions if questions is not None else build_jev_verification_questions()
        )
        result = await asyncio.wait_for(
            evaluate_liara_jev(state, verify_questions),
            timeout=timeout if timeout is not None else get_jev_verify_timeout(),
        )
    except Exception:
        logger.warning("Jev verification skipped (fail-open)", exc_info=True)
        return None
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        logger.warning("Jev verification returned an invalid payload")
        return None
    logger.info(
        "Jev verification ok in %.2fs",
        _time_mod.monotonic() - started,
    )
    return result


def summarize_jev_verification(result: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize a raw verification payload defensively (never raises)."""
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        answers = {}
    report_expected: Optional[float] = None
    html_delivered: Optional[float] = None
    completeness: Optional[float] = None
    raw = answers.get("report_expected")
    if isinstance(raw, dict):
        report_expected = _as_float(raw.get("noul"))
    raw = answers.get("html_delivered")
    if isinstance(raw, dict):
        html_delivered = _as_float(raw.get("noul"))
    raw = answers.get("answer_completeness")
    if isinstance(raw, dict):
        completeness = _as_float(raw.get("score"))
    answer_relevant: Optional[float] = None
    raw = answers.get("answer_relevant")
    if isinstance(raw, dict):
        answer_relevant = _as_float(raw.get("noul"))
    sql_evidence_present: Optional[float] = None
    raw = answers.get("sql_evidence_present")
    if isinstance(raw, dict):
        sql_evidence_present = _as_float(raw.get("noul"))
    needs_repair = (
        report_expected is not None
        and html_delivered is not None
        and report_expected >= get_jev_report_threshold()
        and html_delivered < 0.5
    )
    answer_mismatch = answer_relevant is not None and answer_relevant < 0.5
    return {
        "report_expected": report_expected,
        "html_delivered": html_delivered,
        "completeness": completeness,
        "answer_relevant": answer_relevant,
        "sql_evidence_present": sql_evidence_present,
        "answer_mismatch": answer_mismatch,
        "needs_repair": needs_repair,
        "model": result.get("model") if isinstance(result, dict) else None,
    }


def needs_report_repair(
    *,
    user_input: Any = None,
    triage_summary: Optional[Dict[str, Any]] = None,
    verification_summary: Optional[Dict[str, Any]] = None,
    has_html: bool = False,
) -> bool:
    """Decide whether the fallback HTML repair must run (never raises).

    Priority:
    1. If HTML already exists -> no repair.
    2. If Jev verification explicitly flags the gap -> repair.
    3. Heuristic fallback: triage expects a report (or the raw user text
       mentions report keywords) and no HTML was rendered -> repair.
    """
    try:
        if has_html:
            return False
        if (
            isinstance(verification_summary, dict)
            and verification_summary.get("needs_repair") is True
        ):
            logger.info("Jev repair decision: repair=True (verification flagged gap)")
            return True
        if isinstance(triage_summary, dict) and triage_summary.get("expects_report"):
            logger.info("Jev repair decision: repair=True (triage expects report)")
            return True
        if _mentions_report(user_input):
            logger.info("Jev repair decision: repair=True (report keywords)")
            return True
        if (
            isinstance(verification_summary, dict)
            and isinstance(verification_summary.get("report_expected"), float)
            and verification_summary["report_expected"] >= get_jev_report_threshold()
        ):
            return True
        logger.info("Jev repair decision: repair=False has_html=%s", bool(has_html))
        return False
    except Exception:
        return False


def assess_database_answer_relevance(
    user_input: Any,
    history_steps: Any,
) -> Dict[str, Any]:
    """Deterministically check SQL evidence for common metric/dimension asks.

    This is a fail-closed guard for the concrete class of error where the
    model returns a polished report about a neighboring but unrelated table.
    It never executes SQL and only inspects the SQL/action outputs already
    collected by the ReAct loop.
    """
    try:
        text = user_input if isinstance(user_input, str) else str(user_input or "")
        lowered = re.sub(r"\s+", " ", text.casefold())
        intent = classify_request_intent(text)
        asks_province_sales = (
            any(term in lowered for term in ("استان", "province", "provinces", "state"))
            and any(
                term in lowered
                for term in ("فروش", "فروخت", "sales", "sale", "revenue", "sold")
            )
        )
        if intent != "database_query":
            return {
                "requires_database": False,
                "relevant": True,
                "reason": "request is not classified as a database question",
                "sql_count": 0,
                "successful_sql_count": 0,
            }

        sql_count = 0
        successful_sql_count = 0
        evidence_parts: List[str] = []
        for step in history_steps if isinstance(history_steps, list) else []:
            if not isinstance(step, dict) or str(step.get("action", "")).lower() != "sql_query":
                continue
            sql_count += 1
            raw_input = step.get("action_input")
            if isinstance(raw_input, str):
                try:
                    raw_input = _json_lib.loads(raw_input)
                except Exception:
                    pass
            sql = raw_input.get("sql") if isinstance(raw_input, dict) else raw_input
            if isinstance(sql, str):
                evidence_parts.append(sql.casefold())

            step_failed = str(step.get("status", "done")).lower() in {
                "failed",
                "error",
            }
            output_text: List[str] = []
            for output in step.get("outputs") or []:
                if isinstance(output, dict) and isinstance(output.get("content"), str):
                    output_text.append(output["content"].casefold())
            joined_output = "\n".join(output_text)
            if any(
                marker in joined_output
                for marker in (
                    "sql execution failed",
                    "no database selected",
                    "query timed out",
                    "permission denied",
                )
            ):
                step_failed = True
            if not step_failed and joined_output.strip():
                successful_sql_count += 1
                evidence_parts.append(joined_output)

        evidence = "\n".join(evidence_parts)
        if not sql_count or not successful_sql_count:
            return {
                "requires_database": True,
                "relevant": False,
                "reason": "no successful SQL result",
                "sql_count": sql_count,
                "successful_sql_count": successful_sql_count,
            }

        if asks_province_sales:
            has_province = any(
                term in evidence
                for term in ("statename", "state_name", "province", "استان", "statecity")
            )
            has_sales_metric = any(
                term in evidence
                for term in (
                    "totalprice",
                    "finaltotal",
                    "gtotal",
                    "mainprice",
                    "grandtotalprice",
                    "saleprice",
                    "revenue",
                    "فروش",
                    "فاکتور",
                )
            )
            relevant = has_province and has_sales_metric
            reason = (
                "province and sales evidence found"
                if relevant
                else "province-sales question requires both geographic and sales evidence"
            )
        else:
            # For other factual DB asks require at least one SQL query and
            # non-empty results; specialized metric guards can be added from
            # labelled production examples without penalizing free-form asks.
            relevant = True
            reason = "successful SQL evidence found"
        return {
            "requires_database": True,
            "relevant": relevant,
            "reason": reason,
            "asks_province_sales": asks_province_sales,
            "sql_count": sql_count,
            "successful_sql_count": successful_sql_count,
        }
    except Exception:
        logger.debug("Database answer relevance check failed", exc_info=True)
        # Fail closed for a recognized province-sales question; fail open for
        # other intents where this narrow guard has no domain-specific rule.
        text = user_input if isinstance(user_input, str) else str(user_input or "")
        lowered = text.casefold()
        narrow_case = ("استان" in lowered or "province" in lowered) and (
            "فروش" in lowered or "sales" in lowered
        )
        return {
            "requires_database": classify_request_intent(text) == "database_query",
            "relevant": not narrow_case,
            "reason": "relevance check exception",
            "sql_count": 0,
            "successful_sql_count": 0,
        }


def build_database_relevance_failure_html(
    user_input: Any, assessment: Dict[str, Any]
) -> str:
    """Build an honest replacement report when SQL evidence is absent/mismatched."""
    question = user_input if isinstance(user_input, str) else str(user_input or "")
    reason = str((assessment or {}).get("reason") or "درخواست با شواهد SQL تطبیق نداشت")
    sql_count = (assessment or {}).get("sql_count", 0)
    successful = (assessment or {}).get("successful_sql_count", 0)
    safe_question = _html_lib.escape(strip_context_tags(question)[:1500])
    safe_reason = _html_lib.escape(reason[:500])
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>پاسخ نیازمند بررسی</title>
<style>
body{{font-family:Tahoma,Arial,sans-serif;background:#f6f7fb;color:#1f2937;padding:24px}}
main{{max-width:760px;margin:auto;background:white;border-radius:12px;padding:24px;box-shadow:0 2px 12px #0001}}
h1{{font-size:22px;color:#9a3412}}.notice{{background:#fff7ed;border-right:4px solid #f97316;padding:14px;line-height:1.9}}
dt{{font-weight:bold;margin-top:14px}}dd{{margin:6px 0;line-height:1.8}}.muted{{color:#64748b;font-size:13px}}
</style></head><body><main>
<h1>نتیجهٔ سؤال با دادهٔ اجراشده تطبیق نداشت</h1>
<div class="notice">برای جلوگیری از ارائهٔ آمار نامرتبط، نتیجهٔ قبلی تأیید نشد. در این گزارش عدد یا نمودار جایگزین ساخته نشده است.</div>
<dl><dt>درخواست شما</dt><dd>{safe_question}</dd>
<dt>وضعیت بررسی</dt><dd>{safe_reason}</dd>
<dt>کوئری‌های SQL موفق</dt><dd>{successful} از {sql_count}</dd></dl>
<p class="muted">پاسخ فقط زمانی نمایش داده می‌شود که نتیجهٔ کوئری، شاخص و بُعد خواسته‌شده را واقعاً پوشش دهد.</p>
</main></body></html>"""


def _collect_text_snippets(
    history_steps: Any,
    *,
    max_snippets: int = 6,
    max_chars_each: int = 1200,
) -> List[str]:
    """Collect short human-readable snippets for the fallback report.

    Skips tool-call JSON blobs (``{"code": ...}``, ``{"sql": ...}``), source
    code echoes, and status noise — those are what made the previous fallback
    report show raw ``json``` blocks to the user.
    """
    _NOISE_PREFIXES = (
        "(no output",
        "No code was received",
        "Generated image URLs",
        "HTML report has been successfully rendered",
        "SyntaxError before execution",
    )
    snippets: List[str] = []
    try:
        steps = history_steps if isinstance(history_steps, list) else []
        for step in steps:
            if len(snippets) >= max_snippets:
                break
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or "")
            if action.lower() in {"todowrite", "question", "liara_jev_decision"}:
                continue
            for output in step.get("outputs") or []:
                if len(snippets) >= max_snippets:
                    break
                if not isinstance(output, dict):
                    continue
                if output.get("output_type") not in {"text", "markdown"}:
                    continue
                content = output.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                text = content.strip()
                if text.startswith(_NOISE_PREFIXES):
                    continue
                if _looks_like_tool_json(text):
                    continue
                # Skip bare code dumps (import/def/plt chains with no prose).
                if _looks_like_code_dump(text):
                    continue
                text = _unescape_text(text)
                if len(text) > max_chars_each:
                    text = text[:max_chars_each] + "…"
                snippets.append(text)
    except Exception:
        pass
    return snippets


_CONTEXT_TAG_PATTERN = re.compile(
    r"\[(?:Database|database|Skill|skill|context|truncated)[^\]]*\]"
)

_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json|python|sql)?\s*\n?(.*?)\n?\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)

_TOOL_JSON_KEYS = frozenset(
    {
        "code",
        "sql",
        "todos",
        "tasks",
        "questions",
        "html",
        "file_path",
        "template_path",
        "script_name",
        "script_file_name",
        "tool_name",
        "args",
        "result",
    }
)

_CODE_HINT_PATTERN = re.compile(
    r"(import matplotlib|import pandas|from matplotlib|plt\.(figure|plot|savefig|title)|"
    r"np\.array|def |SELECT\s+\w+\s+FROM)",
    re.IGNORECASE,
)


def strip_context_tags(text: Any) -> str:
    """Remove ``[Database: ...]``/``[context: ...]`` suffixes (never raises)."""
    try:
        cleaned = _CONTEXT_TAG_PATTERN.sub("", str(text or ""))
        return re.sub(r"\s{2,}", " ", cleaned).strip()
    except Exception:
        return str(text or "")[:500]


def _unescape_text(text: str) -> str:
    """Turn literal ``\\n``/``\\\"`` escapes into real characters (best-effort)."""
    try:
        if "\\n" in text and "\n" not in text:
            text = text.replace("\\n", "\n")
        if '\\"' in text:
            text = text.replace('\\"', '"')
        if "\\t" in text and "\t" not in text:
            text = text.replace("\\t", "\t")
        return text
    except Exception:
        return text


def _looks_like_tool_json(text: str) -> bool:
    """True when text is a tool-call JSON blob, not a human answer."""
    try:
        candidate = text.strip()
        fence = _FENCE_PATTERN.match(candidate)
        if fence:
            candidate = fence.group(1).strip()
        if not candidate.startswith("{"):
            return False
        parsed = _json_lib.loads(candidate)
        if not isinstance(parsed, dict) or not parsed:
            return False
        return any(key in _TOOL_JSON_KEYS for key in parsed)
    except Exception:
        return False


def _looks_like_code_dump(text: str) -> bool:
    """True when text is source code rather than a readable result."""
    try:
        candidate = _FENCE_PATTERN.match(text.strip())
        body = candidate.group(1) if candidate else text
        lines = [line for line in body.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        hits = sum(1 for line in lines if _CODE_HINT_PATTERN.search(line))
        return hits >= 2 and hits / len(lines) >= 0.4
    except Exception:
        return False


def clean_answer_text(text: Any, *, max_chars: int = 8000) -> str:
    """Return a human-readable answer (never raw JSON/code, never raises)."""
    try:
        raw = text if isinstance(text, str) else str(text or "")
        raw = raw.strip()
        if not raw:
            return ""
        # Unwrap ```json fences the model sometimes terminates with.
        fence = _FENCE_PATTERN.match(raw)
        if fence:
            raw = fence.group(1).strip()
            try:
                parsed = _json_lib.loads(raw)
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("result"), str) and parsed[
                        "result"
                    ].strip():
                        raw = parsed["result"].strip()
                    elif any(k in parsed for k in ("code", "sql", "html", "todos")):
                        return ""
            except Exception:
                pass
        if _looks_like_tool_json(raw) or _looks_like_code_dump(raw):
            return ""
        raw = _unescape_text(strip_context_tags(raw))
        # Strip a lonely leaked tool-call trace line if present.
        raw = re.sub(r"^(Thought|Action|Action Input|Observation)\s*:.*$", "", raw,
                     flags=re.MULTILINE).strip()
        return raw[:max_chars].strip()
    except Exception:
        return ""


def _iter_code_outputs(history_steps: Any) -> List[str]:
    """Yield executed source-code strings from history (never raises)."""
    codes: List[str] = []
    try:
        steps = history_steps if isinstance(history_steps, list) else []
        for step in steps:
            if not isinstance(step, dict):
                continue
            for output in step.get("outputs") or []:
                if not isinstance(output, dict):
                    continue
                if output.get("output_type") != "code":
                    continue
                content = output.get("content")
                if isinstance(content, str) and content.strip():
                    codes.append(_unescape_text(content.strip()))
    except Exception:
        pass
    return codes


_SAVEFIG_PATTERN = re.compile(
    r"savefig\(\s*['\"]([^'\"]+\.(?:png|jpg|jpeg|gif|svg|webp))['\"]",
    re.IGNORECASE,
)

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"})


def harvest_lost_chart_images(
    history_steps: Any,
    generated_images: Any = None,
) -> List[str]:
    """Copy chart files saved outside PLOT_DIR into the served images dir.

    Post-loop twin of the code_interpreter harvest: scans executed ``code``
    outputs for ``savefig('...')`` absolute paths and serves any file that
    actually exists on disk. Returns the new ``/images/...`` URLs (also
    appended to ``generated_images`` when it is a list). Never raises.
    """
    recovered: List[str] = []
    try:
        import shutil
        import uuid as _uuid_mod

        try:
            from dbgpt.configs.model_config import STATIC_MESSAGE_IMG_PATH
        except Exception:
            return recovered
        known: set = set()
        try:
            for url in generated_images or []:
                if isinstance(url, str):
                    known.add(url)
        except Exception:
            pass
        try:
            os.makedirs(STATIC_MESSAGE_IMG_PATH, exist_ok=True)
        except Exception:
            return recovered
        seen_paths: set = set()
        for code in _iter_code_outputs(history_steps):
            for raw_path in _SAVEFIG_PATTERN.findall(code):
                if not raw_path or raw_path in seen_paths:
                    continue
                seen_paths.add(raw_path)
                if "PLOT_DIR" in raw_path:
                    continue
                candidate = os.path.expandvars(os.path.expanduser(raw_path))
                if (
                    not os.path.isfile(candidate)
                    or os.path.splitext(candidate)[1].lower() not in _IMAGE_EXTS
                ):
                    continue
                try:
                    unique_name = (
                        f"{_uuid_mod.uuid4().hex[:8]}_{os.path.basename(candidate)}"
                    )
                    dest = os.path.join(STATIC_MESSAGE_IMG_PATH, unique_name)
                    shutil.copy2(candidate, dest)
                    img_url = f"/images/{unique_name}"
                    if img_url not in known:
                        known.add(img_url)
                        recovered.append(img_url)
                        if isinstance(generated_images, list):
                            generated_images.append(img_url)
                        logger.warning(
                            "Jev Governor: recovered lost chart %s -> %s",
                            candidate,
                            img_url,
                        )
                except Exception:
                    continue
    except Exception:
        pass
    return recovered


_SERIES_LABEL_PATTERN = re.compile(
    r"(?:months|labels|categories|x_labels|x)\s*=\s*\[([^\]]{3,500})\]",
    re.IGNORECASE | re.DOTALL,
)
_SERIES_VALUE_PATTERN = re.compile(
    r"(?:sales|values|data|y_values|y)\s*=\s*\[([0-9][0-9,\.\s]*)\]",
    re.IGNORECASE,
)


def _parse_str_list(body: str) -> List[str]:
    items: List[str] = []
    try:
        for chunk in body.split(","):
            item = chunk.strip().strip("'\"").strip()
            if item:
                items.append(item)
    except Exception:
        pass
    return items


def _parse_num_list(body: str) -> List[float]:
    numbers: List[float] = []
    try:
        for chunk in body.split(","):
            chunk = chunk.strip()
            if chunk:
                numbers.append(float(chunk))
    except Exception:
        return []
    return numbers


def extract_chart_series(history_steps: Any) -> Optional[Dict[str, Any]]:
    """Extract (labels, values) for an inline chart from executed code.

    Looks for ``months/labels = [...]`` + ``sales/values = [...]`` list
    assignments in ``code`` outputs. Returns ``None`` when nothing plottable
    is found. Never raises.
    """
    try:
        for code in _iter_code_outputs(history_steps):
            label_match = _SERIES_LABEL_PATTERN.search(code)
            value_match = _SERIES_VALUE_PATTERN.search(code)
            if not label_match or not value_match:
                continue
            labels = _parse_str_list(label_match.group(1))
            values = _parse_num_list(value_match.group(1))
            if len(labels) >= 2 and len(labels) == len(values):
                return {"labels": labels[:24], "values": values[:24]}
        return None
    except Exception:
        return None


def build_inline_svg_chart(
    labels: List[str], values: List[float], *, title: str = ""
) -> str:
    """Render a dependency-free inline SVG line+bar chart (never raises)."""
    try:
        width, height = 860, 340
        pad_l, pad_r, pad_t, pad_b = 56, 20, 30, 64
        plot_w = width - pad_l - pad_r
        plot_h = height - pad_t - pad_b
        top = max(values) if values else 1.0
        if top <= 0:
            top = 1.0
        top *= 1.15
        n = len(values)
        step = plot_w / max(n - 1, 1) if n > 1 else 0
        points: List[Tuple[float, float]] = []
        for i, value in enumerate(values):
            x = pad_l + (step * i if n > 1 else plot_w / 2)
            y = pad_t + plot_h - (float(value) / top) * plot_h
            points.append((x, y))
        # Gridlines (4 levels).
        grid = ""
        for level in range(5):
            gy = pad_t + plot_h * level / 4
            gval = top * (1 - level / 4)
            grid += (
                f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" '
                f'y2="{gy:.1f}" stroke="#e2e8f0"/>'
                f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" font-size="11" '
                f'fill="#64748b" text-anchor="end">{gval:,.0f}</text>'
            )
        bars = ""
        bar_w = min(46, (step * 0.5) if n > 1 else 60)
        for (x, y), value in zip(points, values):
            bars += (
                f'<rect x="{x - bar_w / 2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                f'height="{pad_t + plot_h - y:.1f}" rx="4" fill="#bbf7d0"/>'
            )
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        dots = "".join(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#16a34a"/>'
            f'<text x="{x:.1f}" y="{y - 10:.1f}" font-size="11" fill="#166534" '
            f'text-anchor="middle">{v:,.0f}</text>'
            for (x, y), v in zip(points, values)
        )
        xlabels = "".join(
            f'<text x="{x:.1f}" y="{height - 18}" font-size="12" fill="#334155" '
            f'text-anchor="middle">{_html_lib.escape(str(label)[:12])}</text>'
            for (x, _y), label in zip(points, labels)
        )
        safe_title = _html_lib.escape(str(title or "")[:60])
        title_svg = (
            f'<text x="{width / 2}" y="18" font-size="14" fill="#1f2937" '
            f'text-anchor="middle">{safe_title}</text>'
            if safe_title
            else ""
        )
        return (
            f'<svg viewBox="0 0 {width} {height}" '
            f'style="max-width:100%;height:auto;background:#fff;border:1px solid '
            f'#e2e8f0;border-radius:8px" role="img">{title_svg}{grid}{bars}'
            f'<polyline points="{poly}" fill="none" stroke="#16a34a" '
            f'stroke-width="2.5"/>{dots}{xlabels}</svg>'
        )
    except Exception:
        return ""


def build_fallback_report_html(
    *,
    title: str = "Analysis Report",
    user_input: Any = "",
    final_content: Any = "",
    history_steps: Any = None,
    generated_images: Any = None,
    verification_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """Synthesize a complete standalone HTML report (never raises).

    Used ONLY when a visual report was expected but the agent terminated
    without calling ``html_interpreter``. The report contains:

    - cleaned request + final answer (raw tool JSON / source code is never
      shown — it is the bug visible in the first fallback version),
    - key human-readable observation snippets,
    - an inline SVG chart rebuilt from data found in executed code plus a
      data table, so a real chart is ALWAYS visible even when the matplotlib
      file was lost,
    - every generated/recovered chart image.
    """
    try:
        raw_title = strip_context_tags(title or "گزارش تحلیل") or "گزارش تحلیل"
        safe_title = _html_lib.escape(raw_title[:120])
        request_text = strip_context_tags(
            user_input if isinstance(user_input, str) else str(user_input or "")
        )[:3000]
        answer_text = clean_answer_text(final_content)
        snippets = _collect_text_snippets(history_steps)
        # If the final answer was raw code/JSON (dropped above), promote the
        # first readable snippet so the summary section is never empty.
        promoted = False
        if not answer_text and snippets:
            answer_text = snippets[0]
            promoted = True
        images: List[str] = []
        try:
            for url in generated_images or []:
                if isinstance(url, str) and url.strip():
                    images.append(url.strip())
        except Exception:
            pass
        series = extract_chart_series(history_steps)

        def _para(text: str) -> str:
            return "<p>" + _html_lib.escape(text).replace("\n", "<br>") + "</p>"

        request_html = _para(request_text) if request_text else "<p>—</p>"
        answer_html = (
            _para(answer_text) if answer_text else "<p>خلاصه متنی ثبت نشد.</p>"
        )
        if snippets:
            shown = snippets[1:] if (promoted and len(snippets) > 1) else snippets
            findings_html = (
                f"<ol>{''.join(f'<li>{_html_lib.escape(s)[:2000]}</li>' for s in shown)}</ol>"
            )
        else:
            findings_html = "<p>جزئیات میانی قابل‌نمایش ثبت نشد.</p>"
        charts_parts: List[str] = []
        if series:
            svg = build_inline_svg_chart(
                series["labels"], series["values"], title=raw_title
            )
            if svg:
                charts_parts.append(f'<div style="margin:16px 0">{svg}</div>')
            rows = "".join(
                f"<tr><td>{_html_lib.escape(str(label))}</td>"
                f"<td>{value:,.0f}</td></tr>"
                for label, value in zip(series["labels"], series["values"])
            )
            charts_parts.append(
                '<table style="border-collapse:collapse;margin:12px auto;'
                'min-width:60%"><thead><tr>'
                '<th style="border:1px solid #cbd5e1;padding:6px 12px;'
                'background:#f1f5f9">دسته</th>'
                '<th style="border:1px solid #cbd5e1;padding:6px 12px;'
                'background:#f1f5f9">مقدار</th></tr></thead><tbody>'
                + "".join(
                    row.replace("<td>", '<td style="border:1px solid #e2e8f0;'
                                       'padding:6px 12px;text-align:center">')
                    for row in rows.split("</tr>")
                    if row.strip()
                )
                + "</tbody></table>"
            )
        for url in images[:12]:
            charts_parts.append(
                f'<div style="margin:16px 0">'
                f'<img src="{_html_lib.escape(url, quote=True)}" '
                f'style="max-width:100%;height:auto;border-radius:8px">'
                f"</div>"
            )
        if not charts_parts:
            charts_parts.append(
                "<p>در این اجرا نموداری ذخیره نشد؛ برای دریافت نمودار، "
                "درخواست را با جزئیات بیشتر تکرار کنید.</p>"
            )
        charts_html = "".join(charts_parts)

        return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
body{{font-family:Tahoma,Arial,sans-serif;background:#f6f7fb;color:#1f2430;margin:0;padding:24px}}
.card{{background:#fff;border-radius:12px;padding:20px;margin:0 auto 16px;max-width:960px;box-shadow:0 2px 10px rgba(0,0,0,.06)}}
h1{{font-size:22px;margin:0 0 8px}}h2{{font-size:17px;margin:20px 0 8px;color:#334155}}
.badge{{display:inline-block;background:#dcfce7;color:#166534;border-radius:999px;padding:2px 12px;font-size:12px}}
pre,li,p{{line-height:1.9}}ol{{padding-inline-start:20px}}
.muted{{color:#64748b;font-size:13px}}
</style>
</head>
<body>
<div class="card">
<h1>{safe_title}</h1>
<div><span class="badge">گزارش خودکار</span></div>
<p class="muted">ایجنت بدون رندر HTML به پایان رسید؛ این گزارش به‌صورت خودکار
از گام‌های تأییدشده ساخته شد. نمودارهای تولیدشده در ادامه نمایش داده شده‌اند.</p>
<h2>درخواست</h2>
{request_html}
<h2>خلاصه نتیجه</h2>
{answer_html}
</div>
<div class="card">
<h2>یافته‌های کلیدی</h2>
{findings_html}
</div>
<div class="card">
<h2>📊 نمودارها</h2>
{charts_html}
</div>
</body>
</html>"""
    except Exception:
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Report</title></head><body><p>Report unavailable.</p>"
            "</body></html>"
        )


def render_jev_verification_content(summary: Dict[str, Any], repaired: bool) -> str:
    """Render the visible post-loop verification card body."""
    try:
        parts = []
        if isinstance(summary.get("report_expected"), float):
            parts.append(f"report_expected={summary['report_expected']:.2f}")
        if isinstance(summary.get("html_delivered"), float):
            parts.append(f"html_delivered={summary['html_delivered']:.2f}")
        if isinstance(summary.get("completeness"), float):
            parts.append(f"completeness={summary['completeness']:.2f}")
        parts.append(f"repaired={'yes' if repaired else 'no'}")
        return "Jev verification: " + (", ".join(parts) if parts else "heuristic check")
    except Exception:
        return "Jev verification: heuristic check"
