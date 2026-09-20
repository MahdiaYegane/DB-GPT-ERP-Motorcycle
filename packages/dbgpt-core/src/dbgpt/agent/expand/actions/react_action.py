import json
import logging
import re
from typing import Any, Dict, Optional

from dbgpt.agent import Action, ActionOutput, AgentResource, Resource, ResourceType
from dbgpt.util.json_utils import parse_or_raise_error

from ...resource.tool.base import BaseTool, ToolParameter
from ...resource.tool.pack import ToolPack
from ...util.react_parser import ReActOutputParser, ReActStep
from .tool_action import ToolAction, run_tool

logger = logging.getLogger(__name__)


class Terminate(Action[None], BaseTool):
    """Terminate action.

    It is a special action to terminate the conversation, at same time, it can be a
    tool to return the final answer.
    """

    async def run(
        self,
        ai_message: str,
        resource: Optional[AgentResource] = None,
        rely_action_out: Optional[ActionOutput] = None,
        need_vis_render: bool = True,
        **kwargs,
    ) -> ActionOutput:
        return ActionOutput(
            is_exe_success=True,
            terminate=True,
            content=ai_message,
        )

    @classmethod
    def get_action_description(cls) -> str:
        return (
            "Terminate action representing the task is finished, or you think it is"
            " impossible for you to complete the task"
        )

    @classmethod
    def parse_action(
        cls,
        ai_message: str,
        default_action: "Action",
        resource: Optional[Resource] = None,
        **kwargs,
    ) -> Optional["Action"]:
        """Parse the action from the message.

        If you want skip the action, return None.
        """
        if "parser" in kwargs and isinstance(kwargs["parser"], ReActOutputParser):
            parser = kwargs["parser"]
        else:
            parser = ReActOutputParser()
        steps = parser.parse_current_step(ai_message)
        if len(steps) == 0:
            return None
        if len(steps) > 1:
            logger.warning(
                "Terminate.parse_action: Model output contains %d steps, using first.",
                len(steps),
            )
        step: ReActStep = steps[0]
        if not step.action:
            return None
        if step.action.lower() == default_action.name.lower():
            return default_action
        return None

    @property
    def name(self):
        return "terminate"

    @property
    def description(self):
        return self.get_action_description()

    @property
    def args(self):
        return {
            "output": ToolParameter(
                type="string",
                name="output",
                description=(
                    "Final answer to the task, or the reason why you think it "
                    "is impossible to complete the task"
                ),
            ),
        }

    def execute(self, *args, **kwargs):
        if "output" in kwargs:
            return kwargs["output"]
        if "final_answer" in kwargs:
            return kwargs["final_answer"]
        return args[0] if args else "terminate unknown"

    async def async_execute(self, *args, **kwargs):
        return self.execute(*args, **kwargs)


class ReActAction(ToolAction):
    """React action class."""

    # Stop the agent instead of looping forever when the model keeps emitting
    # tool calls with EMPTY arguments (e.g. truncated tool-call JSON from a
    # small output limit, or a lost tool-call format). Each such call returns
    # a success observation like "No code provided", so the normal retry loop
    # never terminates on its own.
    MAX_CONSECUTIVE_EMPTY_ARGS = 3
    # Separate budget for unparseable model output ("No valid ReAct step..."):
    # a model that cannot follow the Thought/Action format at all would
    # otherwise burn all max_retry_count (30) rounds showing empty 思考中 steps.
    MAX_CONSECUTIVE_PARSE_FAILURES = 5

    # Provider/model-level failures arrive as *text* (e.g. "**LLMServer
    # Generate Error...**: Error code: 403 ..."), so the ReAct parser finds no
    # step and the agent retries the same broken model call ~30 times. Detect
    # these up front and terminate immediately with a readable message.
    _PROVIDER_ERROR_MARKER = "LLMServer Generate Error"

    _PROVIDER_ERROR_KIND_PATTERNS = (
        ("email_verify", ("email_verification_required", "verify your email")),
        ("rate_limit", ("rate_limit", "rate limit", "429", "tokens per day", "TPD")),
        ("auth", ("invalid_api_key", "incorrect api key", "401", "unauthorized",
                   "authentication")),
        ("context_length", ("context_too_long", "context_length_exceeded",
                            "maximum context length")),
        ("not_found", ("model_not_found", "does not exist", "404")),
        ("overloaded", ("overloaded", "503", "service unavailable", "timeout",
                        "timed out")),
    )

    _EMPTY_ARGS_FINAL_MESSAGE = {        "en": (
            "I stopped because '{tool}' was called {count} times in a row "
            "without any usable arguments (the tool kept reporting that no "
            "input was provided). This usually means the model's output was "
            "cut off before the tool arguments were finished — try asking "
            "again with a shorter request. If it keeps happening, increase "
            "`max_new_tokens` in the app config or switch to a model with "
            "more reliable tool calling."
        ),
        "fa": (
            "متوقف شدم چون ابزار «{tool}» تعداد {count} بار پشت سر هم بدون "
            "هیچ ورودی قابل‌استفاده‌ای فراخوانی شد (ابزار هر بار گزارش داد "
            "که ورودی‌ای دریافت نکرده است). این معمولاً یعنی خروجی مدل قبل "
            "از کامل شدن آرگومان‌ها قطع شده است — لطفاً دوباره با یک درخواست "
            "کوتاه‌تر تلاش کنید. اگر ادامه داشت، مقدار `max_new_tokens` را "
            "در کانفیگ بیشتر کنید یا مدل دیگری انتخاب کنید."
        ),
        "zh": (
            "已停止，因为工具“{tool}”连续 {count} 次被调用但都没有收到可用参数"
            "（工具每次都报告未提供输入）。这通常意味着模型输出在工具参数写完"
            "之前被截断了——请用更短的请求再试一次。如果问题持续，请调大应用"
            "配置中的 `max_new_tokens` 或更换工具调用更可靠的模型。"
        ),
    }

    def __init__(self, **kwargs):
        """Tool action init."""
        super().__init__(**kwargs)
        self._consecutive_empty_args = 0
        self._last_empty_tool: Optional[str] = None
        self._consecutive_parse_failures = 0

    @classmethod
    def detect_provider_error(cls, ai_message: Optional[str]) -> Optional[str]:
        """Return a provider-error kind when the model output is an error blob.

        Model adapters wrap upstream HTTP failures as plain text
        ("**LLMServer Generate Error...**: ..."), which the ReAct parser
        cannot parse — so the agent would retry identical failing calls.
        Returns the error kind (``email_verify``, ``rate_limit``, ``auth``,
        ``context_length``, ``not_found``, ``overloaded``) or ``None``.
        """
        if not ai_message or cls._PROVIDER_ERROR_MARKER not in ai_message:
            return None
        lowered = ai_message.lower()
        for kind, needles in cls._PROVIDER_ERROR_KIND_PATTERNS:
            if any(needle in lowered for needle in needles):
                return kind
        return "unknown"

    _PROVIDER_ERROR_FINAL_MESSAGE = {
        "email_verify": {
            "en": (
                "The AI provider refused the request: the TokenHarbor account "
                "behind this model has not verified its email address (error "
                "403 email_verification_required). No tool was run — open "
                "https://tokenharbor.ai/dashboard, verify the email (or "
                "request a new link), then try again."
            ),
            "fa": (
                "سرویس هوش مصنوعی درخواست را رد کرد: ایمیل حساب TokenHarbor "
                "هنوز تأیید نشده است (خطای 403). هیچ ابزاری اجرا نشد — وارد "
                "https://tokenharbor.ai/dashboard شوید، ایمیل را تأیید کنید "
                "(یا لینک جدید بخواهید) و دوباره تلاش کنید."
            ),
        },
        "rate_limit": {
            "en": (
                "The AI provider rate-limited the request (e.g. daily token "
                "quota exhausted). No tool was run. Wait for the quota window "
                "to reset or switch to a different model, then try again."
            ),
            "fa": (
                "سرویس هوش مصنوعی به‌خاطر سقف مصرف درخواست را رد کرد (مثلاً "
                "سهمیه روزانه تمام شده). هیچ ابزاری اجرا نشد. تا بازنشدن "
                "سهمیه صبر کنید یا مدل دیگری انتخاب کنید."
            ),
        },
        "auth": {
            "en": (
                "The AI provider rejected the API key (authentication error). "
                "No tool was run. Check the model's api_key in the server "
                "config, then try again."
            ),
            "fa": (
                "سرویس هوش مصنوعی کلید API را رد کرد (خطای احراز هویت). هیچ "
                "ابزاری اجرا نشد. کلید مدل را در کانفیگ سرور بررسی کنید."
            ),
        },
        "context_length": {
            "en": (
                "The request exceeded the model's context window. No tool was "
                "run. Try a shorter request or a model with a larger context "
                "window."
            ),
            "fa": (
                "درخواست از پنجره کانتکست مدل بزرگ‌تر بود. هیچ ابزاری اجرا "
                "نشد. درخواست کوتاه‌تری بفرستید یا مدلی با کانتکست بزرگ‌تر "
                "انتخاب کنید."
            ),
        },
        "not_found": {
            "en": (
                "The requested model was not found on the provider side "
                "(wrong model id or removed model). No tool was run. Check "
                "the model name in the server config."
            ),
            "fa": (
                "مدل درخواستی در سمت سرویس‌دهنده پیدا نشد (نام مدل اشتباه "
                "است یا حذف شده). هیچ ابزاری اجرا نشد. نام مدل را در کانفیگ "
                "سرور بررسی کنید."
            ),
        },
        "overloaded": {
            "en": (
                "The AI provider is temporarily overloaded or timed out. No "
                "tool was run. Wait a moment and try again."
            ),
            "fa": (
                "سرویس هوش مصنوعی موقتاً پرترافیک است یا timeout داد. هیچ "
                "ابزاری اجرا نشد. کمی صبر کنید و دوباره تلاش کنید."
            ),
        },
        "unknown": {
            "en": (
                "The AI provider returned an error and no tool was run. "
                "Details: {detail}"
            ),
            "fa": (
                "سرویس هوش مصنوعی خطا داد و هیچ ابزاری اجرا نشد. جزئیات: "
                "{detail}"
            ),
        },
    }

    def _provider_error_final_message(self, kind: str, raw: str) -> str:
        """Build the user-facing message for a provider error."""
        lang = (self.language or "en").lower()
        key = "fa" if lang.startswith("fa") else "en"
        entry = self._PROVIDER_ERROR_FINAL_MESSAGE.get(kind) or self._PROVIDER_ERROR_FINAL_MESSAGE["unknown"]
        template = entry.get(key) or entry["en"]
        detail = raw.strip()
        if len(detail) > 400:
            detail = detail[:400] + "…"
        try:
            return template.format(detail=detail)
        except Exception:
            return template

    def _provider_error_output(self, ai_message: str) -> ActionOutput:
        """Build a terminal ActionOutput for a provider error (fail fast)."""
        kind = self.detect_provider_error(ai_message) or "unknown"
        content = self._provider_error_final_message(kind, ai_message)
        return ActionOutput(
            is_exe_success=False,
            content=content,
            observations=content,
            have_retry=False,
            terminate=True,
        )

    _PARSE_FAILURE_FINAL_MESSAGE = {
        "en": (
            "I stopped because the model's replies could not be understood "
            "{count} times in a row (no Thought/Action step found). The last "
            "reply looked like an error or an empty response rather than a "
            "tool call. Details: {detail}"
        ),
        "fa": (
            "متوقف شدم چون پاسخ‌های مدل {count} بار پشت سر هم قابل‌فهم نبود "
            "(هیچ مرحله Thought/Action پیدا نشد). به‌نظر می‌رسد آخرین پاسخ "
            "به‌جای فراخوانی ابزار، یک خطا یا پاسخ خالی بوده است. جزئیات: "
            "{detail}"
        ),
    }

    def _parse_failure_final_message(self, raw: str) -> str:
        lang = (self.language or "en").lower()
        key = "fa" if lang.startswith("fa") else "en"
        detail = (raw or "").strip()
        if len(detail) > 300:
            detail = detail[:300] + "…"
        if not detail:
            detail = "empty model reply"
        return self._PARSE_FAILURE_FINAL_MESSAGE[key].format(
            count=self.MAX_CONSECUTIVE_PARSE_FAILURES, detail=detail
        )

    def _note_parse_failure(self, ai_message: str) -> Optional[ActionOutput]:
        """Trip after repeated unparseable model outputs.

        Each "No valid ReAct step" round currently returns fail+retry, so a
        model stuck emitting error blobs loops max_retry_count (30) times
        showing empty 思考中 steps. This breaker terminates instead.
        Returns a terminal ActionOutput, or None to keep retrying.
        """
        self._consecutive_parse_failures += 1
        logger.warning(
            "Unparseable ReAct output (%d/%d consecutive)",
            self._consecutive_parse_failures,
            self.MAX_CONSECUTIVE_PARSE_FAILURES,
        )
        if self._consecutive_parse_failures < self.MAX_CONSECUTIVE_PARSE_FAILURES:
            return None
        content = self._parse_failure_final_message(ai_message)
        return ActionOutput(
            is_exe_success=False,
            content=content,
            observations=content,
            have_retry=False,
            terminate=True,
        )

    def _reset_parse_failures(self) -> None:
        self._consecutive_parse_failures = 0

    @property
    def resource_need(self) -> Optional[ResourceType]:
        """Return the resource type needed for the action."""
        return None

    @staticmethod
    def _is_degenerate_args(args: Any) -> bool:
        """Return True when tool args carry no usable input.

        Covers ``None``/``{}``/``[]`` as well as dicts whose values are all
        blank (e.g. ``{"code": ""}`` produced by a truncated tool call).
        """
        if args is None:
            return True
        if isinstance(args, dict):
            if not args:
                return True
            for value in args.values():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if value == {} or value == []:
                    continue
                return False
            return True
        if isinstance(args, (list, str)):
            return len(args) == 0
        return False

    def _empty_args_final_message(self, tool_name: Optional[str]) -> str:
        """Build the user-facing message used when the breaker trips."""
        lang = (self.language or "en").lower()
        if lang.startswith("fa"):
            template = self._EMPTY_ARGS_FINAL_MESSAGE["fa"]
        elif lang.startswith("zh"):
            template = self._EMPTY_ARGS_FINAL_MESSAGE["zh"]
        else:
            template = self._EMPTY_ARGS_FINAL_MESSAGE["en"]
        return template.format(
            tool=tool_name or "the tool", count=self.MAX_CONSECUTIVE_EMPTY_ARGS
        )

    def _note_tool_invocation(
        self, tool_name: Optional[str], args: Any
    ) -> Optional[ActionOutput]:
        """Track empty-argument invocations and trip the breaker if needed.

        Returns a terminal :class:`ActionOutput` (``terminate=True``) once
        ``MAX_CONSECUTIVE_EMPTY_ARGS`` degenerate calls happen in a row, so
        the agent stops instead of looping forever. Any call carrying real
        arguments resets the counter. Returns ``None`` otherwise.
        """
        normalized = (tool_name or "").strip().lower()
        names = {part.strip() for part in normalized.split(",")}
        if "terminate" in names or not self._is_degenerate_args(args):
            self._consecutive_empty_args = 0
            self._last_empty_tool = None
            return None
        self._consecutive_empty_args += 1
        self._last_empty_tool = tool_name or self._last_empty_tool
        logger.warning(
            "Empty tool arguments for '%s' (%d/%d consecutive) — "
            "model likely emitted a truncated or malformed tool call",
            tool_name,
            self._consecutive_empty_args,
            self.MAX_CONSECUTIVE_EMPTY_ARGS,
        )
        if self._consecutive_empty_args < self.MAX_CONSECUTIVE_EMPTY_ARGS:
            return None
        content = self._empty_args_final_message(self._last_empty_tool)
        return ActionOutput(
            is_exe_success=True,
            content=content,
            observations=content,
            action=tool_name,
            terminate=True,
        )

    @classmethod
    def parse_action(
        cls,
        ai_message: str,
        default_action: "ReActAction",
        resource: Optional[Resource] = None,
        **kwargs,
    ) -> Optional["ReActAction"]:
        """Parse the action from the message.

        If you want skip the action, return None.
        """
        return default_action

    async def run(
        self,
        ai_message: str,
        resource: Optional[AgentResource] = None,
        rely_action_out: Optional[ActionOutput] = None,
        need_vis_render: bool = True,
        **kwargs,
    ) -> ActionOutput:
        """Perform the action."""

        # Fail fast on provider errors: the error arrives as plain text, so
        # parsing would find no step and the agent would retry the identical
        # broken model call ~30 times (30 empty 思考中 steps in the UI).
        if self.detect_provider_error(ai_message) is not None:
            return self._provider_error_output(ai_message)

        if "parser" in kwargs and isinstance(kwargs["parser"], ReActOutputParser):
            parser = kwargs["parser"]
        else:
            parser = ReActOutputParser()
        steps = parser.parse_current_step(ai_message)
        if len(steps) == 0:
            breaker_out = self._note_parse_failure(ai_message)
            if breaker_out is not None:
                return breaker_out
            raise ValueError("No valid ReAct step found in model output.")
        self._reset_parse_failures()
        if len(steps) > 1:
            logger.warning(
                "Model output contains %d steps, only the first will be executed.",
                len(steps),
            )
        step = steps[0]
        act_out = await self._do_run(ai_message, step, need_vis_render=need_vis_render)
        # A successful tool execution means the model is behaving again.
        if act_out.is_exe_success:
            self._reset_parse_failures()
        if not act_out.action:
            act_out.action = step.action
        if step.thought:
            act_out.thoughts = step.thought
        if step.phase:
            act_out.phase = step.phase
        if step.action_intention:
            act_out.action_intention = step.action_intention
        if step.action_reason:
            act_out.action_reason = step.action_reason
        if not act_out.action_input and step.action_input:
            if isinstance(step.action_input, str):
                act_out.action_input = step.action_input
            else:
                act_out.action_input = json.dumps(step.action_input, ensure_ascii=False)
        return act_out

    @staticmethod
    def _fallback_parse_args(
        tool_name: str,
        raw_input: Any,
        resource: Optional[Resource],
    ) -> Dict[str, Any]:
        """Infer tool args when JSON parsing fails.

        Strategy:
        1. For single-param tools: regex extract the value, or pass raw input.
        2. For multi-param tools: find each param key position and extract
           the string value between quote delimiters.
        3. Return empty dict as last resort.
        """
        if not resource or not tool_name or not raw_input:
            return {}

        tool_packs = ToolPack.from_resource(resource)
        if not tool_packs:
            return {}
        tool_pack: ToolPack = tool_packs[0]
        try:
            tl = tool_pack._get_execution_tool(tool_name)
        except Exception:
            return {}

        param_names = list(tl.args.keys()) if tl.args else []
        if not param_names:
            return {}

        raw_str = str(raw_input)

        def _unescape(s: str) -> str:
            return (
                s.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )

        if len(param_names) == 1:
            param_name = param_names[0]
            pattern = re.compile(
                r'["\']?' + re.escape(param_name) + r'["\']?\s*:\s*"(.*)"',
                re.DOTALL,
            )
            m = pattern.search(raw_str)
            if m:
                return {param_name: _unescape(m.group(1))}
            return {param_name: raw_str}

        # Multi-param: locate each "param_name": position in raw text,
        # then extract the quoted value following each key.
        key_positions: list[tuple[str, int]] = []
        for pname in param_names:
            pat = re.compile(
                r'["\']?' + re.escape(pname) + r'["\']?\s*:\s*',
            )
            m = pat.search(raw_str)
            if m:
                val_start = m.end()
                if val_start < len(raw_str) and raw_str[val_start] in ["'", '"']:
                    val_start += 1
                key_positions.append((pname, val_start))

        key_positions.sort(key=lambda x: x[1])

        result: Dict[str, Any] = {}
        for idx, (pname, val_start) in enumerate(key_positions):
            if idx + 1 < len(key_positions):
                next_pname = key_positions[idx + 1][0]
                next_start = key_positions[idx + 1][1]
                # Walk backwards from next key to find the boundary:
                pat_next = re.compile(
                    r'\s*,\s*["\']?' + re.escape(next_pname) + r'["\']?\s*:'
                )
                m_next = pat_next.search(raw_str, val_start)
                if m_next and m_next.start() < next_start:
                    segment = raw_str[val_start : m_next.start()]
                else:
                    segment = raw_str[val_start:next_start]
                    # Find last `",` or `" ,` pattern as value end
                    boundary = segment.rfind(",")
                    if boundary >= 0:
                        segment = segment[:boundary]

                # Strip trailing quotes
                segment = segment.rstrip()
                if segment.endswith('"') or segment.endswith("'"):
                    segment = segment[:-1]

                result[pname] = _unescape(segment)
            else:
                # Last param: take everything up to the last `"` before `}`
                remaining = raw_str[val_start:]
                # Strip trailing `"}` or `" }` etc.
                remaining = remaining.rstrip()
                while (
                    remaining.endswith("}")
                    or remaining.endswith('"')
                    or remaining.endswith("'")
                ):
                    remaining = remaining[:-1]
                    remaining = remaining.rstrip()

                result[pname] = _unescape(remaining)

            # Try to parse the value as JSON if it looks like an object or array
            if isinstance(result[pname], str):
                s_val = result[pname].strip()
                if (s_val.startswith("{") and s_val.endswith("}")) or (
                    s_val.startswith("[") and s_val.endswith("]")
                ):
                    try:
                        result[pname] = json.loads(s_val)
                    except Exception:
                        pass

        if result:
            return result
        return {}

    @staticmethod
    def _extract_html_interpreter_args(raw_input: str) -> Dict[str, Any]:
        """Robust extraction for html_interpreter {"html": ..., "title": ...}.

        HTML content typically contains many double-quotes (class="...",
        style="...") which break standard JSON parsing. This method uses
        a reverse-search strategy:
        1. Find the LAST occurrence of '"title"' followed by ':' (the actual
           JSON key, not an HTML <title> tag).
        2. Extract the title value from after that key.
        3. Everything between the first '"html"' key and the title key is
           the HTML content.
        """
        if not raw_input:
            return {}

        raw = raw_input.strip()

        # Step 1: Find the last occurrence of a title key pattern
        # Pattern: "title" : " (with optional quotes and whitespace)
        title_key_pattern = re.compile(
            r'["\']?title["\']?\s*:\s*["\']',
        )
        # Find ALL matches and use the last one (most likely the actual key)
        title_matches = list(title_key_pattern.finditer(raw))
        if not title_matches:
            # No title key found — treat entire input as html
            html_val_pattern = re.compile(
                r'["\']?html["\']?\s*:\s*["\']',
            )
            m = html_val_pattern.search(raw)
            if m:
                html_start = m.end()
                # Strip trailing "} patterns
                html_content = raw[html_start:]
                html_content = html_content.rstrip()
                while html_content and html_content[-1] in "\"}' ":
                    html_content = html_content[:-1]
                    html_content = html_content.rstrip()
                if html_content:
                    return {"html": html_content, "title": "Report"}
            return {}

        # Use the last title match
        last_title_match = title_matches[-1]
        title_val_start = last_title_match.end()

        # Step 2: Extract title value (short string, ends at quote + } )
        title_content = raw[title_val_start:]
        title_content = title_content.rstrip()
        # Strip trailing }"' characters
        while title_content and title_content[-1] in "\"}' ":
            title_content = title_content[:-1]
            title_content = title_content.rstrip()
        title_value = title_content.strip() or "Report"

        # Step 3: Extract HTML content
        html_val_pattern = re.compile(
            r'["\']?html["\']?\s*:\s*["\']',
        )
        html_m = html_val_pattern.search(raw)
        if not html_m:
            return {}

        html_start = html_m.end()
        # HTML ends just before the title key boundary
        # Walk backwards from title key to find the separator: , "title"
        title_key_start = last_title_match.start()
        html_end = title_key_start

        # Strip trailing separator: comma, whitespace, quotes
        html_content = raw[html_start:html_end]
        html_content = html_content.rstrip()
        if html_content.endswith(","):
            html_content = html_content[:-1].rstrip()
        # Strip trailing quote if present
        if html_content and html_content[-1] in "\"'":
            html_content = html_content[:-1]

        # Unescape common JSON escape sequences
        html_content = (
            html_content.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )

        if html_content:
            return {"html": html_content, "title": title_value}
        return {}

    async def _do_run(
        self,
        ai_message: str,
        parsed_step: ReActStep,
        need_vis_render: bool = True,
    ) -> ActionOutput:
        """Perform the action."""
        tool_args = {}
        name = parsed_step.action
        action_input = parsed_step.action_input
        action_input_str = action_input

        # Diagnostic logging for html_interpreter calls
        if name == "html_interpreter":
            input_preview = str(action_input)[:200] if action_input else "<empty>"
            logger.info(
                "html_interpreter called: action_input type=%s, len=%d, preview=%s",
                type(action_input).__name__,
                len(str(action_input)) if action_input else 0,
                input_preview,
            )

        if not name:
            terminal_content = str(action_input_str if action_input_str else ai_message)
            return ActionOutput(
                is_exe_success=True,
                content=terminal_content,
                observations=terminal_content,
                terminate=True,
            )

        try:
            # Try to parse the action input to dict
            if action_input and isinstance(action_input, str):
                tool_args = parse_or_raise_error(action_input)
            elif isinstance(action_input, dict) or isinstance(action_input, list):
                tool_args = action_input
                action_input_str = json.dumps(action_input, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            if parsed_step.action == "terminate":
                tool_args = {"output": action_input}
            elif name == "html_interpreter" and isinstance(action_input, str):
                # Special handling for html_interpreter: the HTML content
                # often contains unescaped quotes that break JSON parsing.
                # Use a robust extraction: find the last "title" key and
                # extract the html content between "html": and the title key.
                tool_args = self._extract_html_interpreter_args(action_input)
                logger.info(
                    "html_interpreter fallback extraction: html=%d chars, title=%s",
                    len(tool_args.get("html", "")) if tool_args else 0,
                    tool_args.get("title") if tool_args else None,
                )
                if not tool_args:
                    tool_args = self._fallback_parse_args(
                        name, action_input, self.resource
                    )
            else:
                # JSON parsing failed — try to infer args from the tool definition.
                # If the tool has exactly one required parameter, treat the raw
                # action_input as that parameter's value.
                tool_args = self._fallback_parse_args(name, action_input, self.resource)
            if not tool_args:
                logger.warning(f"Failed to parse the args: {action_input}")
        # Log resolved args for html_interpreter before execution
        if name == "html_interpreter":
            html_len = (
                len(tool_args.get("html", "")) if isinstance(tool_args, dict) else 0
            )
            fp = tool_args.get("file_path", "") if isinstance(tool_args, dict) else ""
            logger.info(
                "html_interpreter resolved: tool_args keys=%s, "
                "html_len=%d, file_path=%s",
                list(tool_args.keys())
                if isinstance(tool_args, dict)
                else type(tool_args).__name__,
                html_len,
                fp or "<none>",
            )
        act_out = await run_tool(
            name,
            tool_args,
            self.resource,
            self.render_protocol,
            need_vis_render=need_vis_render,
            raw_tool_input=action_input_str,
        )
        # Break out of empty-argument retry loops: repeated calls with no
        # usable args (each "succeeding" with e.g. "No code provided") would
        # otherwise spin until max_retry_count while burning context.
        breaker_out = self._note_tool_invocation(name, tool_args)
        if breaker_out is not None:
            act_out = breaker_out
        if not act_out.action_input:
            act_out.action_input = action_input_str
        return act_out
