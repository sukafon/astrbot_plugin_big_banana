import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.providers.zhipu_videos import ZhipuVideosProvider
from core.schemas import ProviderConfig


def build_provider(params: dict | None = None) -> ZhipuVideosProvider:
    plugin = SimpleNamespace(
        common_config=SimpleNamespace(timeout=300, proxy=None),
    )
    config = ProviderConfig(
        provider_type="Zhipu_Videos",
        capability="video_generation",
        enabled=True,
        name="zhipu_videos",
        keys=["test-key"],
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="cogvideox-flash",
        raw_config={
            "quality": "speed",
            "fps": 30,
            "with_audio": False,
            "watermark_enabled": True,
            "poll_interval": 1,
            "job_timeout": 30,
        },
    )
    return ZhipuVideosProvider(
        plugin,
        config,
        params or {"prompt": "A cat runs through the rain."},
    )


def test_builds_cogvideox_flash_request() -> None:
    provider = build_provider(
        {
            "prompt": "A paper plane takes off.",
            "quality": "quality",
            "fps": 60,
            "with_audio": True,
            "watermark_enabled": False,
            "size": "1920x1080",
        }
    )

    body, error = provider._build_body()

    assert error is None
    assert body["model"] == "cogvideox-flash"
    assert body["prompt"] == "A paper plane takes off."
    assert body["quality"] == "quality"
    assert body["fps"] == 60
    assert body["with_audio"] is True
    assert body["watermark_enabled"] is False
    assert body["size"] == "1920x1080"
    assert body["request_id"].startswith("big-banana-")


def test_converts_string_fps_to_integer() -> None:
    provider = build_provider({"prompt": "test", "fps": "60"})

    body, error = provider._build_body()

    assert error is None
    assert body["fps"] == 60
    assert isinstance(body["fps"], int)


def test_polls_until_video_is_ready(monkeypatch) -> None:
    provider = build_provider()
    provider._fetch_job = AsyncMock(
        side_effect=[
            {"task_status": "PROCESSING"},
            {
                "task_status": "SUCCESS",
                "video_result": [
                    {
                        "url": "https://example.com/video.mp4",
                        "cover_image_url": "https://example.com/cover.jpg",
                    }
                ],
            },
        ]
    )

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    result = asyncio.run(provider._poll_job("test-key", "task-id"))

    assert result.error_message is None
    assert len(result.videos) == 1
    assert result.videos[0].url == "https://example.com/video.mp4"
    assert provider._fetch_job.await_count == 2


def test_rejects_invalid_flash_parameters() -> None:
    provider = build_provider({"prompt": "test", "fps": 24})

    _body, error = provider._build_body()

    assert error == "fps 仅支持 30 或 60"
