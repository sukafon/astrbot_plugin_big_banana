import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from core.providers.standard import StandardProvider
from core.schemas import CommonConfig, ImageResource, ProviderCallResult, ProviderConfig


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
    plugin = SimpleNamespace(downloader=downloader, common_config=CommonConfig())
    provider = StandardProvider(
        plugin,
        ProviderConfig(name="openai-images"),
        {},
    )

    await provider._build_images(["https://example.com/result.gif"])

    kwargs = downloader.fetch_images.await_args.kwargs
    assert kwargs["convert"] is True
    assert kwargs["allow_gif"] is True
    assert kwargs["restrict_private_network"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sources",
    [
        [
            "https://example.com/first.png",
            "base64-second",
            "https://example.com/third.png",
        ],
        ["base64-first", "https://example.com/second.png", "base64-third"],
        ["https://example.com/first.png", "https://example.com/second.png"],
        ["base64-first", "base64-second"],
        [],
    ],
)
async def test_loaded_images_preserve_source_order(sources):
    async def fetch_urls(urls, **kwargs):
        return [ImageResource("image/png", url.encode()) for url in urls]

    async def fetch_base64(source, **kwargs):
        return ImageResource("image/png", source.encode())

    plugin = SimpleNamespace(
        common_config=CommonConfig(),
        downloader=SimpleNamespace(
            fetch_images=AsyncMock(side_effect=fetch_urls),
            fetch_base64_image=AsyncMock(side_effect=fetch_base64),
        ),
    )
    images = await StandardProvider(plugin, ProviderConfig(), {})._build_images(sources)

    assert [image.bytes.decode() for image in images] == sources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_source", ["https://example.com/first.png", "base64-second"]
)
async def test_failed_sources_do_not_move_the_remaining_images(failed_source):
    sources = [
        "https://example.com/first.png",
        "base64-second",
        "https://example.com/third.png",
    ]

    async def fetch_urls(urls, **kwargs):
        return [
            ImageResource("image/png", source.encode())
            for source in urls
            if source != failed_source
        ]

    async def fetch_base64(source, **kwargs):
        return (
            None
            if source == failed_source
            else ImageResource("image/png", source.encode())
        )

    plugin = SimpleNamespace(
        common_config=CommonConfig(),
        downloader=SimpleNamespace(
            fetch_images=AsyncMock(side_effect=fetch_urls),
            fetch_base64_image=AsyncMock(side_effect=fetch_base64),
        ),
    )
    images = await StandardProvider(plugin, ProviderConfig(), {})._build_images(sources)

    assert [image.bytes.decode() for image in images] == [
        source for source in sources if source != failed_source
    ]


@pytest.mark.asyncio
async def test_mixed_sources_still_load_concurrently():
    sources = [
        "https://example.com/first.png",
        "base64-second",
        "https://example.com/third.png",
    ]
    started = set()
    release = asyncio.Event()

    async def fetch_urls(urls, **kwargs):
        started.update(urls)
        if len(started) == len(sources):
            release.set()
        await release.wait()
        return [ImageResource("image/png", source.encode()) for source in urls]

    async def fetch_base64(source, **kwargs):
        started.add(source)
        if len(started) == len(sources):
            release.set()
        await release.wait()
        return ImageResource("image/png", source.encode())

    plugin = SimpleNamespace(
        common_config=CommonConfig(),
        downloader=SimpleNamespace(
            fetch_images=AsyncMock(side_effect=fetch_urls),
            fetch_base64_image=AsyncMock(side_effect=fetch_base64),
        ),
    )
    images = await asyncio.wait_for(
        StandardProvider(plugin, ProviderConfig(), {})._build_images(sources), timeout=2
    )

    assert started == set(sources)
    assert [image.bytes.decode() for image in images] == sources
