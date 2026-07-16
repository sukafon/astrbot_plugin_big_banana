from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from core.providers.standard import StandardProvider
from core.schemas import ProviderCallResult, ProviderConfig


@pytest.mark.asyncio
async def test_http_status_code_is_included_in_frontend_error() -> None:
    plugin = SimpleNamespace(
        common_config=SimpleNamespace(max_retry=1, smart_retry=True),
    )
    provider = StandardProvider(
        plugin,
        ProviderConfig(name="openai-images", keys=["test-key"]),
        {},
    )
    provider._call_api = AsyncMock(
        return_value=ProviderCallResult(
            status_code=404,
            error_message="Invalid URL (POST /v1/images/edits)",
        )
    )

    result = await provider.generate_images()

    assert result.error_message == "HTTP 404：Invalid URL (POST /v1/images/edits)"


@pytest.mark.asyncio
async def test_non_http_error_is_left_unchanged() -> None:
    plugin = SimpleNamespace(
        common_config=SimpleNamespace(max_retry=1, smart_retry=True),
    )
    provider = StandardProvider(
        plugin,
        ProviderConfig(name="openai-images", keys=["test-key"]),
        {},
    )
    provider._call_api = AsyncMock(
        return_value=ProviderCallResult(error_message="程序错误")
    )

    result = await provider.generate_images()

    assert result.error_message == "程序错误"


@pytest.mark.asyncio
async def test_http_200_is_included_when_response_has_no_image() -> None:
    plugin = SimpleNamespace(
        common_config=SimpleNamespace(max_retry=1, smart_retry=True),
    )
    provider = StandardProvider(
        plugin,
        ProviderConfig(name="openai-images", keys=["test-key"]),
        {},
    )
    provider._call_api = AsyncMock(
        return_value=ProviderCallResult(
            status_code=200,
            error_message="响应中未包含图片数据",
        )
    )

    result = await provider.generate_images()

    assert result.error_message == "HTTP 200：响应中未包含图片数据"


@pytest.mark.asyncio
async def test_output_urls_preserve_gif_format() -> None:
    downloader = SimpleNamespace(fetch_images=AsyncMock(return_value=[]))
    plugin = SimpleNamespace(downloader=downloader)
    provider = StandardProvider(
        plugin,
        ProviderConfig(name="openai-images"),
        {},
    )

    await provider._build_images(["https://example.com/result.gif"])

    kwargs = downloader.fetch_images.await_args.kwargs
    assert kwargs["convert"] is True
    assert kwargs["allow_gif"] is True
