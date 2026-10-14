"""Deterministic checks for numbers in the final answer.

LLMs re-type long numbers instead of copying them and sometimes break the
digit grouping or misuse separators, e.g. SQL returned 3875944906385.57 and
the answer said "۳٫۸۷۵٫۹۴۴٫۹۰۶٫۳۸۵٫۵۷" (Persian *decimal* sign used as the
thousands separator) or "۹۱٫۹۳۳" for 91933 (reads as 91.933). This module

1. reads every number token in the answer, considers the plausible ways its
   separators can be interpreted, prefers the one that matches a value in the
   tool observations, and rewrites malformed / misleading tokens in standard
   form (digits unchanged: Persian digits -> ``٬`` thousands, ``٫`` decimal;
   ASCII digits -> ``,`` and ``.``);
2. lists large numbers (7+ integer digits) that do not appear in any tool
   observation, so the UI can mark them as unverified.

Code spans (`...`) are never touched.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Optional, Set, Tuple

_FA = "۰۱۲۳۴۵۶۷۸۹"
_AR = "٠١٢٣٤٥٦٧٨٩"
_DIGITS_TO_ASCII = str.maketrans(_FA + _AR, "0123456789" * 2)
_TO_FA = str.maketrans("0123456789", _FA)
_THOUSANDS_SEPS = ",٬"
_DECIMAL_SEPS = ".٫"
_D = "0-9۰-۹٠-٩"

# Digits with single separators between digits. Dates like 1404/01/01 are
# excluded by the "/" look-arounds; a sentence-final "." is not part of it.
_NUM = re.compile(
    rf"(?<![\w/])[{_D}](?:[{_D}]|[,٬٫.](?=[{_D}]))*(?![\w/])"
)
_CODE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")

MIN_CHECK_DIGITS = 7


def _split(token: str) -> Tuple[List[str], List[str]]:
    """Digit groups (ASCII) and the separators between them."""
    groups, seps, cur = [], [], ""
    for ch in token:
        if ch in _THOUSANDS_SEPS or ch in _DECIMAL_SEPS:
            groups.append(cur)
            seps.append(ch)
            cur = ""
        else:
            cur += ch.translate(_DIGITS_TO_ASCII)
    groups.append(cur)
    return groups, seps


def _canon(int_digits: str, frac: str) -> str:
    int_digits = int_digits.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{int_digits}.{frac}" if frac else int_digits


def _interpretations(token: str) -> List[Tuple[str, str]]:
    """Plausible (int_digits, frac_digits) readings, most plausible first."""
    groups, seps = _split(token)
    if not seps:
        return [(groups[0], "")]
    all_int = ("".join(groups), "")
    last_decimal = ("".join(groups[:-1]), groups[-1])
    last_sep, last_group = seps[-1], groups[-1]
    if last_sep in _THOUSANDS_SEPS and len(last_group) == 3:
        order = [all_int, last_decimal]
    elif last_sep in _DECIMAL_SEPS and len(last_group) != 3:
        order = [last_decimal, all_int]
    elif seps.count(last_sep) > 1 and len(last_group) == 3:
        # e.g. "1.234.567": a repeated separator followed by 3 digits.
        order = [all_int, last_decimal]
    else:
        order = [last_decimal, all_int] if last_sep in _DECIMAL_SEPS else [all_int, last_decimal]
    return list(dict.fromkeys(order))


def _naive(token: str) -> Optional[Tuple[str, str]]:
    """How a reader reads the token in standard notation (None if invalid)."""
    groups, seps = _split(token)
    if not seps:
        return groups[0], ""
    dec_positions = [i for i, s in enumerate(seps) if s in _DECIMAL_SEPS]
    if len(dec_positions) > 1:
        return None
    if dec_positions and dec_positions[0] != len(seps) - 1:
        return None
    int_groups = groups[: dec_positions[0] + 1] if dec_positions else groups
    frac = groups[-1] if dec_positions else ""
    if len(int_groups) > 1:
        if not (1 <= len(int_groups[0]) <= 3 and all(len(g) == 3 for g in int_groups[1:])):
            return None
    return "".join(int_groups), frac


def _render(int_digits: str, frac: str, persian: bool) -> str:
    int_digits = int_digits.lstrip("0") or "0"
    grouped = f"{int(int_digits):,}"
    out = grouped + ("." + frac if frac else "")
    if persian:
        out = out.replace(",", "\x00").replace(".", "٫").replace("\x00", "٬").translate(_TO_FA)
    return out


def numbers_in(texts: Iterable[Any]) -> Set[str]:
    """All canonical readings of numbers found in evidence texts."""
    found: Set[str] = set()
    for text in texts:
        if not isinstance(text, str):
            continue
        for m in _NUM.finditer(text):
            for int_digits, frac in _interpretations(m.group(0)):
                found.add(_canon(int_digits, frac))
    return found


def observation_texts(history_steps: Any) -> List[str]:
    texts: List[str] = []
    for step in history_steps if isinstance(history_steps, list) else []:
        if not isinstance(step, dict):
            continue
        # The SQL text itself is evidence too (dates/ids used in filters).
        action_input = step.get("action_input")
        if isinstance(action_input, str):
            texts.append(action_input)
        elif action_input is not None:
            texts.append(json.dumps(action_input, ensure_ascii=False, default=str))
        for out in step.get("outputs") or []:
            if isinstance(out, dict) and isinstance(out.get("content"), str):
                texts.append(out["content"])
    return texts


def check_answer_numbers(
    answer: str, evidence_texts: Iterable[Any]
) -> Tuple[str, List[str], int]:
    """Return (fixed_answer, unverified_numbers, rewritten_count)."""
    if not isinstance(answer, str) or not answer:
        return answer, [], 0
    evidence = numbers_in(evidence_texts)
    unverified: List[str] = []
    rewritten = 0

    def _fix(m: "re.Match[str]") -> str:
        nonlocal rewritten
        token = m.group(0)
        readings = _interpretations(token)
        chosen = next((r for r in readings if _canon(*r) in evidence), readings[0])
        naive = _naive(token)
        new = token
        has_sep = any(ch in _THOUSANDS_SEPS + _DECIMAL_SEPS for ch in token)
        if has_sep and (naive is None or _canon(*naive) != _canon(*chosen)):
            persian = any(ch in _FA + _AR for ch in token)
            new = _render(chosen[0], chosen[1], persian)
            rewritten += 1
        if len(chosen[0].lstrip("0")) >= MIN_CHECK_DIGITS and _canon(*chosen) not in evidence:
            if new not in unverified:
                unverified.append(new)
        return new

    parts = _CODE.split(answer)
    fixed = "".join(
        part if _CODE.fullmatch(part) else _NUM.sub(_fix, part) for part in parts
    )
    return fixed, unverified, rewritten
