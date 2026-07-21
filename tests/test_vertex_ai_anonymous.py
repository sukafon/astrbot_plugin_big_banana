import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
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
    provider.max_retry = 3
    provider.retry_delay = 0
    provider._build_body_context = Mock(return_value={"variables": {}})
    return provider


def build_body_with_modalities(response_modalities: str) -> dict:
    plugin = SimpleNamespace(
        params_config=SimpleNamespace(
            aspect_ratio="default",
            google_search=False,
            image_size="1K",
        )
    )
    config = ProviderConfig(
        provider_type="Vertex_AI_Anonymous",
        name="vertex_ai_anonymous",
        model="gemini-3-pro-image-preview",
        raw_config={
            "response_modalities": response_modalities,
            "system_prompt": "",
        },
    )
    provider = VertexAIAnonymousProvider(plugin, config, {"prompt": "test"})
    provider._body_context_cache = None
    return provider._build_body_context()


def test_response_modalities_are_passed_to_vertex_variables() -> None:
    body = build_body_with_modalities("['TEXT','IMAGE']")

    assert body["variables"]["generationConfig"]["responseModalities"] == [
        "TEXT",
        "IMAGE",
    ]


def test_image_response_modality_is_passed_by_default() -> None:
    body = build_body_with_modalities("['IMAGE']")

    assert body["variables"]["generationConfig"]["responseModalities"] == ["IMAGE"]


def test_response_modalities_are_omitted_when_disabled() -> None:
    body = build_body_with_modalities("无")

    assert "responseModalities" not in body["variables"]["generationConfig"]


def test_initialize_reads_recaptcha_retry_settings() -> None:
    plugin = SimpleNamespace(
        http_manager=SimpleNamespace(get_curl_session=Mock(return_value=object())),
        common_config=SimpleNamespace(timeout=30, proxy=None),
    )
    config = ProviderConfig(
        provider_type="Vertex_AI_Anonymous",
        name="vertex_ai_anonymous",
        raw_config={
            "max_refresh": 2,
            "max_retry": 6,
            "retry_delay": 0,
        },
    )
    provider = VertexAIAnonymousProvider(plugin, config, {"prompt": "test"})

    asyncio.run(provider.initialize())

    assert provider.max_refresh == 2
    assert provider.max_retry == 6
    assert provider.retry_delay == 0
def test_verify_failures_reuse_token_until_retry_limit(monkeypatch) -> None:
    provider = build_provider()
    provider.max_retry = 2
    provider._get_recaptcha_token = AsyncMock(side_effect=["token-1", "token-2"])
    submitted_tokens = []

    async def call_vertex(body: dict) -> ProviderCallResult:
        submitted_tokens.append(body["variables"]["recaptchaToken"])
        if len(submitted_tokens) <= 4:
            return ProviderCallResult(
                status_code=3,
                error_message="Failed to verify action",
            )
        return ProviderCallResult(images=[Mock(bytes=b"image")], status_code=200)

    provider._call_vertex_api = AsyncMock(side_effect=call_vertex)
    warning = Mock()

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(vertex_ai_anonymous.logger, "warning", warning)

    result = asyncio.run(provider.generate_images())

    assert result.images
    assert submitted_tokens == ["token-1"] * 4 + ["token-2"]
    assert provider._get_recaptcha_token.await_count == 2
    verify_logs = [
        call.args[0]
        for call in warning.call_args_list
        if "recaptcha_token" in call.args[0]
    ]
    assert verify_logs == [
        "[BIG BANANA] recaptcha_token 首次验证失败 (不消耗重试次数)：0/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：1/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：2/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：3/2",
    ]


def test_verify_failures_stop_after_token_refresh_limit(monkeypatch) -> None:
    provider = build_provider()
    provider.max_retry = 2
    provider.max_refresh = 1
    provider._get_recaptcha_token = AsyncMock(side_effect=["token-1", "token-2"])
    submitted_tokens = []

    async def call_vertex(body: dict) -> ProviderCallResult:
        submitted_tokens.append(body["variables"]["recaptchaToken"])
        return ProviderCallResult(
            status_code=3,
            error_message="Failed to verify action",
        )

    provider._call_vertex_api = AsyncMock(side_effect=call_vertex)
    warning = Mock()

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(vertex_ai_anonymous.logger, "warning", warning)

    result = asyncio.run(provider.generate_images())

    assert result.error_message == "Failed to verify action"
    assert submitted_tokens == ["token-1"] * 4 + ["token-2"] * 4
    assert provider._get_recaptcha_token.await_count == 2
    assert provider._call_vertex_api.await_count == 8
    verify_logs = [
        call.args[0]
        for call in warning.call_args_list
        if "recaptcha_token" in call.args[0]
    ]
    assert verify_logs == [
        "[BIG BANANA] recaptcha_token 首次验证失败 (不消耗重试次数)：0/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：1/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：2/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：3/2",
        "[BIG BANANA] recaptcha_token 首次验证失败 (不消耗重试次数)：0/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：1/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：2/2",
        "[BIG BANANA] recaptcha_token 重试 (status=3) 次数：3/2",
        "[BIG BANANA] recaptcha_token 刷新次数达到上限",
    ]


def test_stops_after_configured_token_refresh_limit(monkeypatch) -> None:
    provider = build_provider()
    provider.max_retry = 0
    provider.max_refresh = 2
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


def test_non_recaptcha_error_returns_immediately() -> None:
    provider = build_provider()
    provider._get_recaptcha_token = AsyncMock(return_value="token-1")
    provider._call_vertex_api = AsyncMock(
        return_value=ProviderCallResult(
            status_code=429,
            error_message="Resource exhausted",
        )
    )

    result = asyncio.run(provider.generate_images())

    assert result.error_message == "Resource exhausted"
    assert provider._call_vertex_api.await_count == 1


def test_non_recaptcha_error_uses_neutral_fallback() -> None:
    provider = build_provider()
    provider._get_recaptcha_token = AsyncMock(return_value="token-1")
    provider._call_vertex_api = AsyncMock(
        return_value=ProviderCallResult(status_code=429)
    )

    result = asyncio.run(provider.generate_images())

    assert result.error_message == "图片生成失败"
    assert provider._call_vertex_api.await_count == 1


def test_status_8_refreshes_token_and_retries(monkeypatch) -> None:
    provider = build_provider()
    provider.max_retry = 0
    provider.max_refresh = 2
    provider._get_recaptcha_token = AsyncMock(side_effect=["token-1", "token-2"])

    submitted_tokens = []

    async def call_vertex(body: dict) -> ProviderCallResult:
        submitted_tokens.append(body["variables"]["recaptchaToken"])
        if len(submitted_tokens) == 1:
            return ProviderCallResult(
                status_code=8,
                error_message="Resource exhausted",
            )
        return ProviderCallResult(images=[Mock(bytes=b"image")], status_code=200)

    provider._call_vertex_api = AsyncMock(side_effect=call_vertex)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    result = asyncio.run(provider.generate_images())

    assert result.images
    assert submitted_tokens == ["token-1", "token-2"]
    assert provider._get_recaptcha_token.await_count == 2


