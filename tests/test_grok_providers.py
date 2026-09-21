import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.providers import grok_videos
from core.providers.grok_images import GrokImagesProvider
from core.providers.grok_videos import (
    GrokVideosProvider,
    build_grok_video_api_url,
)
from core.schemas import ImageResource, ProviderConfig


def build_image_provider(
    *, image_list: list[ImageResource] | None = None
) -> GrokImagesProvider:
    plugin = SimpleNamespace(
        params_config=SimpleNamespace(image_size="default"),
    )
    config = ProviderConfig(
        provider_type="grok_images",
        capability="image_generation",
        enabled=True,
        name="Grok_Images",
        keys=["test-key"],
        base_url="https://api.x.ai/v1",
        model="grok-imagine-image-2.0",
        raw_config={
            "resolution": "2k",
            "quality": "medium",
            "response_format": "url",
        },
    )
    provider = GrokImagesProvider(
        plugin,
        config,
        {"prompt": "A cat in the rain", "n": 2, "aspect_ratio": "16:9"},
        image_list,
    )
    provider._body_context_cache = None
    return provider


def build_video_provider(
    *, image_list: list[ImageResource] | None = None
) -> GrokVideosProvider:
    plugin = SimpleNamespace(
        common_config=SimpleNamespace(timeout=300, proxy=None),
        params_config=SimpleNamespace(
            video_size="default",
            video_duration=10,
            video_aspect_ratio="16:9",
            video_with_audio=True,
            video_poll_interval=1,
            video_job_timeout=30,
        ),
    )
    config = ProviderConfig(
        provider_type="grok_videos",
        capability="video_generation",
        enabled=True,
        name="Grok_Videos",
        keys=["test-key"],
        base_url="https://api.x.ai/v1",
        model="grok-imagine-video-1.5",
        raw_config={},
    )
    return GrokVideosProvider(
        plugin,
        config,
        {"prompt": "A paper boat drifting downstream", "video_size": "720p"},
        image_list,
    )


def test_builds_grok_image_generation_request() -> None:
    provider = build_image_provider()

    body = provider._build_body_context()

    assert body == {
        "model": "grok-imagine-image-2.0",
        "prompt": "A cat in the rain",
        "n": 2,
        "aspect_ratio": "16:9",
        "resolution": "2k",
        "quality": "medium",
        "response_format": "url",
    }
    assert provider._build_api_url() == "https://api.x.ai/v1/images/generations"

    provider.provider_config.base_url = "http://127.0.0.1:8317"
    assert provider._build_api_url() == (
        "http://127.0.0.1:8317/v1/images/generations"
    )


def test_builds_grok_multi_image_edit_request() -> None:
    provider = build_image_provider(
        image_list=[
            ImageResource("image/png", b"first"),
            ImageResource("image/jpeg", b"second"),
        ]
    )

    body = provider._build_body_context()

    assert provider._build_api_url() == "https://api.x.ai/v1/images/edits"
    assert "images" in body
    assert len(body["images"]) == 2
    assert body["images"][0]["type"] == "image_url"
    assert body["images"][0]["url"].startswith("data:image/png;base64,")
    assert "n" not in body
    assert "image" not in body


def test_preserves_empty_image_model_for_reverse_proxy() -> None:
    provider = build_image_provider()
    provider.provider_config.model = ""

    body = provider._build_body_context()

    assert body["model"] == ""


def test_builds_grok_video_request() -> None:
    provider = build_video_provider(
        image_list=[ImageResource("image/png", b"first-frame")]
    )

    body, error = provider._build_body()

    assert error is None
    assert body["model"] == "grok-imagine-video-1.5"
    assert body["prompt"] == "A paper boat drifting downstream"
    assert body["duration"] == 10
    assert body["aspect_ratio"] == "16:9"
    assert body["resolution"] == "720p"
    assert body["generate_audio"] is True
    assert body["image"]["url"].startswith("data:image/png;base64,")


def test_preserves_empty_model_for_reverse_proxy() -> None:
    provider = build_video_provider()
    provider.provider_config.model = ""

    body, error = provider._build_body()

    assert error is None
    assert body["model"] == ""


def test_builds_grok_video_api_urls_from_supported_base_urls() -> None:
    assert (
        build_grok_video_api_url("http://127.0.0.1:8317")
        == "http://127.0.0.1:8317/v1/videos/generations"
    )
    assert (
        build_grok_video_api_url("https://api.x.ai/v1")
        == "https://api.x.ai/v1/videos/generations"
    )
    assert (
        build_grok_video_api_url(
            "https://proxy.example/v1/videos/generations",
            request_id="request-id",
        )
        == "https://proxy.example/v1/videos/request-id"
    )


def test_grok_video_polling_returns_video_url(monkeypatch) -> None:
    provider = build_video_provider()
    provider._fetch_job = AsyncMock(
        side_effect=[
            {"status": "pending"},
            {
                "status": "done",
                "video": {"url": "https://example.com/grok.mp4"},
            },
        ]
    )

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    result = asyncio.run(provider._poll_job("test-key", "request-id"))

    assert result.error_message is None
    assert [video.url for video in result.videos] == [
        "https://example.com/grok.mp4"
    ]
    assert provider._fetch_job.await_count == 2


def test_grok_video_polling_does_not_fetch_after_deadline(monkeypatch) -> None:
    provider = build_video_provider()
    provider.plugin.params_config.video_poll_interval = 10
    provider.plugin.params_config.video_job_timeout = 1
    provider._fetch_job = AsyncMock()
    slept: list[float] = []
    clock = iter([0.0, 0.0, 0.0, 1.0])

    async def record_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(
        grok_videos,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock)),
    )

    result = asyncio.run(provider._poll_job("test-key", "request-id"))

    assert result.error_message == "Grok 视频生成超过 1 秒仍未完成"
    assert slept == [1.0]
    provider._fetch_job.assert_not_awaited()


def test_grok_video_rejects_invalid_duration() -> None:
    provider = build_video_provider()
    provider.params["duration"] = 16

    _body, error = provider._build_body()

    assert error == "Grok 视频 duration 必须是 1 到 15 之间的整数"
