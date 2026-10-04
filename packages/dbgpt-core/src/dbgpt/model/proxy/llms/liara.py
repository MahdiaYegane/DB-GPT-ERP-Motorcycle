import os
from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Type, Union, cast

from dbgpt.core import ModelMetadata
from dbgpt.core.awel.flow import (
    TAGS_ORDER_HIGH,
    ResourceCategory,
    auto_register_resource,
)
from dbgpt.model.proxy.llms.proxy_model import ProxyModel, parse_model_request
from dbgpt.util.i18n_utils import _

from ..base import (
    AsyncGenerateStreamFunction,
    GenerateStreamFunction,
    register_proxy_model_adapter,
)
from .chatgpt import OpenAICompatibleDeployModelParameters, OpenAILLMClient

if TYPE_CHECKING:
    from httpx._types import ProxiesTypes
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    ClientType = Union[AsyncAzureOpenAI, AsyncOpenAI]


# Workspace (service) ID of `dbgpt` on Liara AI console.
# It is part of the base URL path: /api/{workspace_id}/v1
_LIARA_DEFAULT_WORKSPACE_ID = "6aba44bb5d4e25a451926448"
_LIARA_DEFAULT_API_BASE = (
    f"https://ai.liara.ir/api/{_LIARA_DEFAULT_WORKSPACE_ID}/v1"
)
_LIARA_DEFAULT_MODEL = "xiaomi/mimo-v2.5"

# Display names used in DB-GPT configs/UI (with a "liara" tag so they are
# distinguishable from same-named models on other providers) mapped to the
# real model ids sent to the Liara API.
_LIARA_DISPLAY_TO_REAL_MODEL = {
    "xiaomi/mimo-v2.5 (liara)": "xiaomi/mimo-v2.5",
    "xiaomi/mimo-v2.5-pro (liara)": "xiaomi/mimo-v2.5-pro",
    "deepseek/deepseek-v4.1-flash (liara)": "deepseek/deepseek-v4.1-flash",
    "z-ai/glm-5.3-flash (liara)": "z-ai/glm-5.3-flash",
    "minimax/minimax-m3 (liara)": "minimax/minimax-m3",
    "qwen/qwen3.7-flash (liara)": "qwen/qwen3.7-flash",
    "deepseek/deepseek-v4-pro (liara)": "deepseek/deepseek-v4-pro",
}


def resolve_liara_model_id(model: Optional[str]) -> str:
    """Resolve a DB-GPT display name to the real Liara model id."""
    if not model:
        return _LIARA_DEFAULT_MODEL
    return _LIARA_DISPLAY_TO_REAL_MODEL.get(model, model)


@auto_register_resource(
    label=_("Liara Proxy LLM"),
    category=ResourceCategory.LLM_CLIENT,
    tags={"order": TAGS_ORDER_HIGH},
    description=_("Liara AI (Iran) proxy LLM configuration. OpenAI-compatible."),
    documentation_url="https://docs.liara.ir/ai/quick-start/",
    show_in_ui=False,
)
@dataclass
class LiaraDeployModelParameters(OpenAICompatibleDeployModelParameters):
    """Deploy model parameters for Liara AI.

    Liara AI is OpenAI-compatible. Auth is ``Authorization: Bearer <API_KEY>``
    against ``https://ai.liara.ir/api/{workspace_id}/v1``.
    """

    provider: str = "proxy/liara"

    api_base: Optional[str] = field(
        default="${env:LIARA_API_BASE:-https://ai.liara.ir/api/"
        "6aba44bb5d4e25a451926448/v1}",
        metadata={
            "help": _(
                "The base url of the Liara AI API "
                "(https://ai.liara.ir/api/{workspace_id}/v1). "
                "Override with LIARA_API_BASE env var for another workspace."
            ),
        },
    )

    api_key: Optional[str] = field(
        default="${env:LIARA_API_KEY}",
        metadata={
            "help": _("The API key of the Liara AI API (LIARA_API_KEY)."),
            "tags": "privacy",
        },
    )


async def liara_generate_stream(
    model: ProxyModel, tokenizer, params, device, context_len=2048
):
    client: LiaraLLMClient = cast(LiaraLLMClient, model.proxy_llm_client)
    request = parse_model_request(params, client.default_model, stream=True)
    async for r in client.generate_stream(request):
        yield r


class LiaraLLMClient(OpenAILLMClient):
    """Liara AI LLM Client.

    Liara AI exposes an OpenAI-compatible ``/chat/completions`` API, so we
    inherit from OpenAILLMClient.

    Connection (tested 2026-09-28, service ``dbgpt``):
      - Base URL: ``https://ai.liara.ir/api/6aba44bb5d4e25a451926448/v1``
      - Auth: ``Authorization: Bearer $LIARA_API_KEY`` (no ``x-teamid`` needed)
      - Team ID (``6aba3e24c22e616a1d93ace8``) must NOT be used in the URL path
        (returns ``403 access denied``).

    Docs: https://docs.liara.ir/ai/quick-start/
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        api_type: Optional[str] = None,
        api_version: Optional[str] = None,
        model: Optional[str] = _LIARA_DEFAULT_MODEL,
        proxies: Optional["ProxiesTypes"] = None,
        timeout: Optional[int] = 240,
        model_alias: Optional[str] = _LIARA_DEFAULT_MODEL,
        context_length: Optional[int] = None,
        openai_client: Optional["ClientType"] = None,
        openai_kwargs: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        api_base = (
            api_base
            or os.getenv("LIARA_API_BASE")
            or _LIARA_DEFAULT_API_BASE
        )
        api_key = api_key or os.getenv("LIARA_API_KEY")
        model = model or _LIARA_DEFAULT_MODEL

        if not context_length:
            # Context windows from https://ai.liara.ir/v1/models (2026-09-29)
            if "mimo-v2.5" in model:
                context_length = 1050000
            elif "glm-5.3" in model:
                context_length = 1310720
            elif "minimax-m3" in model or "deepseek-v4" in model:
                context_length = 1048576
            elif "qwen3.7" in model:
                context_length = 1000000
            elif "deepseek" in model:
                context_length = 1048576
            else:
                context_length = 32 * 1024

        if not api_key:
            raise ValueError(
                "Liara AI API key is required, please set 'LIARA_API_KEY' in "
                "environment variable or pass it to the client."
            )

        super().__init__(
            api_key=api_key,
            api_base=api_base,
            api_type=api_type,
            api_version=api_version,
            model=model,
            proxies=proxies,
            timeout=timeout,
            model_alias=model_alias,
            context_length=context_length,
            openai_client=openai_client,
            openai_kwargs=openai_kwargs,
            **kwargs,
        )

    @property
    def default_model(self) -> str:
        model = self._model
        if not model:
            model = _LIARA_DEFAULT_MODEL
        return resolve_liara_model_id(model)

    def _build_request(self, request, stream: Optional[bool] = False) -> Dict[str, Any]:
        # Always send the real Liara model id: the incoming request may carry
        # the UI display name (e.g. "xiaomi/mimo-v2.5 (liara)").
        payload = super()._build_request(request, stream)
        payload["model"] = resolve_liara_model_id(payload.get("model"))
        return payload

    @classmethod
    def param_class(cls) -> Type[LiaraDeployModelParameters]:
        """Get the deploy model parameters class."""
        return LiaraDeployModelParameters

    @classmethod
    def new_client(
        cls,
        model_params: LiaraDeployModelParameters,
        default_executor: Optional[Executor] = None,
    ) -> "LiaraLLMClient":
        """Create a new client with the model parameters."""
        return cls(
            api_key=model_params.api_key,
            api_base=model_params.api_base,
            api_type=model_params.api_type,
            api_version=model_params.api_version,
            model=model_params.real_provider_model_name,
            proxy=model_params.http_proxy,
            model_alias=model_params.real_provider_model_name,
            context_length=model_params.context_length,
        )

    @classmethod
    def generate_stream_function(
        cls,
    ) -> Optional[Union[GenerateStreamFunction, AsyncGenerateStreamFunction]]:
        """Get the generate stream function."""
        return liara_generate_stream


register_proxy_model_adapter(
    LiaraLLMClient,
    supported_models=[
        ModelMetadata(
            model="xiaomi/mimo-v2.5",
            context_length=1050000,
            max_output_length=32 * 1024,
            description="Xiaomi MiMo-V2.5 via Liara AI (text+image+audio+video → text)",
            link="https://docs.liara.ir/ai/xiaomi/",
            function_calling=True,
        ),
        ModelMetadata(
            model="xiaomi/mimo-v2.5-pro",
            context_length=1050000,
            max_output_length=32 * 1024,
            description="Xiaomi MiMo-V2.5-Pro via Liara AI (text → text)",
            link="https://docs.liara.ir/ai/xiaomi/",
            function_calling=True,
        ),
        ModelMetadata(
            model="deepseek/deepseek-v4.1-flash",
            context_length=1048576,
            max_output_length=32 * 1024,
            description="DeepSeek V4.1 Flash via Liara AI (text+image → text)",
            link="https://docs.liara.ir/ai/deepseek/",
            function_calling=True,
        ),
        ModelMetadata(
            model="z-ai/glm-5.3-flash",
            context_length=1310720,
            max_output_length=943718,
            description="Z.ai GLM 5.3 Flash via Liara AI (text+image+video → text)",
            link="https://docs.liara.ir/ai/quick-start/",
            function_calling=True,
        ),
        ModelMetadata(
            model="minimax/minimax-m3",
            context_length=1048576,
            max_output_length=512000,
            description="MiniMax M3 via Liara AI (text+image+video → text)",
            link="https://docs.liara.ir/ai/quick-start/",
            function_calling=True,
        ),
        ModelMetadata(
            model="qwen/qwen3.7-flash",
            context_length=1000000,
            max_output_length=65536,
            description="Qwen 3.7 Flash via Liara AI (text+image+video → text)",
            link="https://docs.liara.ir/ai/quick-start/",
            function_calling=True,
        ),
        ModelMetadata(
            model="deepseek/deepseek-v4-pro",
            context_length=1048576,
            max_output_length=384000,
            description="DeepSeek V4 Pro 0423 via Liara AI (text → text)",
            link="https://docs.liara.ir/ai/deepseek/",
            function_calling=True,
        ),
    ],
)
