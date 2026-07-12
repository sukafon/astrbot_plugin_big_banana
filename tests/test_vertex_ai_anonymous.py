import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.providers import vertex_ai_anonymous
from core.providers.vertex_ai_anonymous import VertexAIAnonymousProvider
from core.schemas import ProviderCallResult, ProviderConfig


def build_provider() -> VertexAIAnonymousProvider:
    plugin = SimpleNamespace()
    config = ProviderConfig(
        provider_type="Vertex_AI_Anonymous",
        name="vertex_ai_anonymous",
    )
    provider = VertexAIAnonymousProvider(plugin, config, {"prompt": "test"})
    provider.max_refresh = 2
    provider.retry_before_switch = 3
    provider.retry_delay = 0
    provider._build_body_context = Mock(return_value={"variables": {}})
    return provider


def test_verify_failures_accumulate_across_token_refreshes(monkeypatch) -> None:
    provider = build_provider()
    provider._get_recaptcha_token = AsyncMock(
        side_effect=["token-1", "token-2", "token-3"]
    )
    provider._call_vertex_api = AsyncMock(
        side_effect=[
            ProviderCallResult(
                status_code=3,
                error_message="Failed to verify action",
            ),
            ProviderCallResult(
                status_code=3,
                error_message="Recaptcha token is invalid",
            ),
            ProviderCallResult(
                status_code=3,
                error_message="Failed to verify action",
            ),
            ProviderCallResult(
                status_code=3,
                error_message="Recaptcha token is invalid",
            ),
            ProviderCallResult(
                status_code=3,
                error_message="Failed to verify action",
            ),
        ]
    )
    warning = Mock()

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(vertex_ai_anonymous.logger, "warning", warning)

    result = asyncio.run(provider.generate_images())

    assert result.error_message == "Failed to verify action"
    verify_logs = [
        call.args[0]
        for call in warning.call_args_list
        if "验证失败次数" in call.args[0]
    ]
    assert verify_logs == [
        "[BIG BANANA] recaptcha_token 验证失败次数：1/3",
        "[BIG BANANA] recaptcha_token 验证失败次数：2/3",
        "[BIG BANANA] recaptcha_token 验证失败次数：3/3",
    ]


def test_stops_after_configured_token_refresh_limit(monkeypatch) -> None:
    provider = build_provider()
    provider.retry_before_switch = 10
    provider._get_recaptcha_token = AsyncMock(
        side_effect=["token-1", "token-2", "token-3"]
    )
    provider._call_vertex_api = AsyncMock(
        side_effect=[
            ProviderCallResult(
                status_code=3,
                error_message="Recaptcha token is invalid",
            )
        ]
        * 3
    )

    monkeypatch.setattr(vertex_ai_anonymous.logger, "warning", Mock())

    result = asyncio.run(provider.generate_images())

    assert result.error_message == "Recaptcha token is invalid"
    assert provider._get_recaptcha_token.await_count == 3
    assert provider._call_vertex_api.await_count == 3


def test_stops_after_configured_retry_count(monkeypatch) -> None:
    provider = build_provider()
    provider._get_recaptcha_token = AsyncMock(return_value="token-1")
    provider._call_vertex_api = AsyncMock(
        return_value=ProviderCallResult(
            status_code=429,
            error_message="Resource exhausted",
        )
    )

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    result = asyncio.run(provider.generate_images())

    assert result.error_message == "Resource exhausted"
    assert provider._call_vertex_api.await_count == 3
