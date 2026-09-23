import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import astrbot.api.message_components as Comp
from core.providers.native import NativeProvider
from core.providers.standard import StandardProvider
from core.schemas import CommonConfig, ImageResource, ProviderConfig


@pytest.mark.parametrize("allow_private", [False, True])
def test_standard_provider_uses_global_url_policy(allow_private: bool) -> None:
    downloader = SimpleNamespace(fetch_images=AsyncMock(return_value=[]))
    plugin = SimpleNamespace(
        common_config=CommonConfig(allow_private_provider_urls=allow_private),
        downloader=downloader,
    )
    provider = StandardProvider(plugin, ProviderConfig(), {})

    asyncio.run(provider._build_images(["https://example.com/generated.png"]))

    assert (
        downloader.fetch_images.await_args.kwargs["restrict_private_network"]
        is not allow_private
    )


@pytest.mark.parametrize("allow_private", [False, True])
def test_native_provider_uses_global_url_policy(allow_private: bool) -> None:
    downloader = SimpleNamespace(
        fetch_image=AsyncMock(return_value=ImageResource("image/png", b"image"))
    )
    plugin = SimpleNamespace(
        common_config=CommonConfig(allow_private_provider_urls=allow_private),
        downloader=downloader,
    )
    provider = NativeProvider(plugin, ProviderConfig(), {})
    response = SimpleNamespace(
        result_chain=SimpleNamespace(
            chain=[Comp.Image.fromURL("https://example.com/generated.png")]
        ),
        completion_text="",
    )

    images = asyncio.run(provider._extract_result(response))

    assert len(images) == 1
    assert (
        downloader.fetch_image.await_args.kwargs["restrict_private_network"]
        is not allow_private
    )


def test_native_markdown_image_uses_global_url_policy() -> None:
    downloader = SimpleNamespace(
        fetch_image=AsyncMock(return_value=ImageResource("image/png", b"image"))
    )
    plugin = SimpleNamespace(common_config=CommonConfig(), downloader=downloader)
    provider = NativeProvider(plugin, ProviderConfig(), {})
    response = SimpleNamespace(
        result_chain=None,
        completion_text="![generated](https://example.com/generated.png)",
    )

    images = asyncio.run(provider._extract_result(response))

    assert len(images) == 1
    assert downloader.fetch_image.await_args.kwargs["restrict_private_network"] is True
