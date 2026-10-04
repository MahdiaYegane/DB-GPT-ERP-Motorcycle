"""Tests for Liara AI proxy LLM client (proxy/liara)."""

import os
from unittest.mock import patch

from dbgpt.model.proxy.llms.liara import LiaraLLMClient


class TestLiaraModelRegistration:
    """Liara provider/model adapter resolution."""

    def test_supported_models_contains_all_three(self):
        from dbgpt.model.adapter.base import get_model_adapter

        for name in [
            "xiaomi/mimo-v2.5",
            "xiaomi/mimo-v2.5-pro",
            "deepseek/deepseek-v4.1-flash",
            "z-ai/glm-5.3-flash",
            "minimax/minimax-m3",
            "qwen/qwen3.7-flash",
            "deepseek/deepseek-v4-pro",
        ]:
            adapter = get_model_adapter("proxy/liara", name)
            assert adapter is not None

        adapter = get_model_adapter("proxy/liara", "xiaomi/mimo-v2.5")
        metadata = {m.model: m for m in adapter.supported_models()}
        assert metadata["xiaomi/mimo-v2.5"].context_length == 1050000
        assert metadata["xiaomi/mimo-v2.5-pro"].context_length == 1050000
        assert metadata["deepseek/deepseek-v4.1-flash"].context_length == 1048576
        assert metadata["xiaomi/mimo-v2.5"].function_calling is True
        assert metadata["z-ai/glm-5.3-flash"].context_length == 1310720
        assert metadata["minimax/minimax-m3"].context_length == 1048576
        assert metadata["qwen/qwen3.7-flash"].context_length == 1000000
        assert metadata["deepseek/deepseek-v4-pro"].context_length == 1048576
        assert metadata["deepseek/deepseek-v4-pro"].function_calling is True


class TestLiaraDisplayNameMapping:
    """UI display names ('... (liara)') map to real Liara model ids."""

    @patch.dict(os.environ, {"LIARA_API_KEY": "test-key"})
    def test_display_name_resolves_to_real_model_id(self):
        from dbgpt.core import ModelMessage, ModelRequest
        from dbgpt.model.proxy.llms.liara import resolve_liara_model_id

        assert (
            resolve_liara_model_id("xiaomi/mimo-v2.5 (liara)") == "xiaomi/mimo-v2.5"
        )
        assert (
            resolve_liara_model_id("xiaomi/mimo-v2.5-pro (liara)")
            == "xiaomi/mimo-v2.5-pro"
        )
        assert (
            resolve_liara_model_id("deepseek/deepseek-v4.1-flash (liara)")
            == "deepseek/deepseek-v4.1-flash"
        )
        assert resolve_liara_model_id("z-ai/glm-5.3-flash (liara)") == (
            "z-ai/glm-5.3-flash"
        )
        assert (
            resolve_liara_model_id("minimax/minimax-m3 (liara)")
            == "minimax/minimax-m3"
        )
        assert (
            resolve_liara_model_id("qwen/qwen3.7-flash (liara)")
            == "qwen/qwen3.7-flash"
        )
        assert (
            resolve_liara_model_id("deepseek/deepseek-v4-pro (liara)")
            == "deepseek/deepseek-v4-pro"
        )
        # Plain ids pass through unchanged
        assert resolve_liara_model_id("xiaomi/mimo-v2.5") == "xiaomi/mimo-v2.5"

        client = LiaraLLMClient(
            model="xiaomi/mimo-v2.5 (liara)",
            openai_client=_FakeOpenAIClient(),
        )
        assert client.default_model == "xiaomi/mimo-v2.5"

        request = ModelRequest(
            model="deepseek/deepseek-v4.1-flash (liara)",
            messages=[ModelMessage(role="user", content="hi")],
        )
        payload = client._build_request(request)
        assert payload["model"] == "deepseek/deepseek-v4.1-flash"


class TestLiaraContextLength:
    """Liara model context length defaults."""

    @patch.dict(os.environ, {"LIARA_API_KEY": "test-key"})
    def test_mimo_uses_one_million_context(self):
        client = LiaraLLMClient(
            model="xiaomi/mimo-v2.5-pro",
            openai_client=_FakeOpenAIClient(),
        )
        assert client.context_length == 1050000

    @patch.dict(os.environ, {"LIARA_API_KEY": "test-key"})
    def test_deepseek_flash_context(self):
        client = LiaraLLMClient(
            model="deepseek/deepseek-v4.1-flash",
            openai_client=_FakeOpenAIClient(),
        )
        assert client.context_length == 1048576

    @patch.dict(os.environ, {"LIARA_API_KEY": "test-key"})
    def test_all_catalog_context_defaults(self):
        context_lengths = {
            "xiaomi/mimo-v2.5": 1050000,
            "xiaomi/mimo-v2.5-pro": 1050000,
            "deepseek/deepseek-v4.1-flash": 1048576,
            "z-ai/glm-5.3-flash": 1310720,
            "minimax/minimax-m3": 1048576,
            "qwen/qwen3.7-flash": 1000000,
            "deepseek/deepseek-v4-pro": 1048576,
        }

        for model, expected_context_length in context_lengths.items():
            client = LiaraLLMClient(model=model, openai_client=_FakeOpenAIClient())
            assert client.context_length == expected_context_length

    @patch.dict(os.environ, {"LIARA_API_KEY": "test-key"})
    def test_new_client_infers_context_when_not_configured(self):
        from dbgpt.model.proxy.llms.liara import LiaraDeployModelParameters

        params = LiaraDeployModelParameters(
            name="qwen/qwen3.7-flash",
            provider="proxy/liara",
            api_key="test-key",
            api_base="https://ai.liara.ir/api/test-workspace/v1",
        )

        client = LiaraLLMClient.new_client(params)

        assert client.context_length == 1000000

    @patch.dict(os.environ, {"LIARA_API_KEY": "test-key"})
    def test_default_base_url_contains_workspace(self):
        client = LiaraLLMClient(
            model="xiaomi/mimo-v2.5",
            openai_client=_FakeOpenAIClient(),
        )
        assert "ai.liara.ir/api/" in client._init_params.api_base
        assert client._init_params.api_base.endswith("/v1")


class _FakeOpenAIClient:
    default_headers = {}
