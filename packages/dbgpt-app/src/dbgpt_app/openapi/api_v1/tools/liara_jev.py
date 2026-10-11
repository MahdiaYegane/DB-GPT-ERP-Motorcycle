"""Agent tool for structured TypeSafe Jev decisions through Liara."""

import json
from typing import Any, Dict

from dbgpt.agent.resource.tool.base import tool

from ..liara_jev import evaluate_liara_jev


def make_liara_jev_decision():
    @tool(
        "liara_jev_decision",
        description=(
            "Use TypeSafe Jev 1.13 through Liara for fast structured decisions, "
            "classification, scoring, and yes/no judgments. This is not a chat "
            "or text-generation model. Pass state as text/JSON and questions as "
            "a JSON object with noul, choice, or score question definitions. "
            'Example questions: {"urgent":{"type":"noul",'
            '"instructions":"Does this require urgent attention?"}}'
        ),
    )
    async def liara_jev_decision(state: str = "", questions: str = "") -> str:
        """Evaluate the given state against named TypeSafe questions.

        Args:
            state: Text or JSON-serialized structured data to evaluate.
            questions: JSON object mapping names to typed question objects.
        """
        try:
            parsed_state: Any = json.loads(state)
        except (json.JSONDecodeError, TypeError):
            parsed_state = state
        try:
            parsed_questions: Dict[str, Dict[str, Any]] = json.loads(questions)
            result = await evaluate_liara_jev(parsed_state, parsed_questions)
            content = json.dumps(result, ensure_ascii=False)
            output_type = "json"
        except Exception as exc:
            content = f"Jev decision failed: {exc}"
            output_type = "text"
        return json.dumps(
            {"chunks": [{"output_type": output_type, "content": content}]},
            ensure_ascii=False,
        )

    return liara_jev_decision
