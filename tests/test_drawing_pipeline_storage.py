import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.drawing.pipeline import DrawingPipeline
from core.schemas import GenerationResult, ImageResource


def build_image(url: str | None = None) -> ImageResource:
    return ImageResource(mime="image/png", data_bytes=b"image-bytes", url=url)


def build_plugin(
    *,
    r2_save: bool,
    hoster_enabled: bool = True,
    uploaded_urls: list[str | None] | None = None,
) -> SimpleNamespace:
    image_hoster = SimpleNamespace(
        is_enabled=Mock(return_value=hoster_enabled),
        upload_images=AsyncMock(
            return_value=uploaded_urls or ["https://cdn.example.com/image.png"]
        ),
    )
    return SimpleNamespace(
        common_config=SimpleNamespace(strip_metadata=False),
        params_config=SimpleNamespace(url=False),
        save_images=SimpleNamespace(local_save=False, r2_save=r2_save),
        dispatcher=SimpleNamespace(
            dispatch=AsyncMock(
                return_value=GenerationResult(images=[build_image()])
            )
        ),
        image_hoster=image_hoster,
        save_dir=None,
    )


def test_r2_save_uploads_without_url_mode() -> None:
    plugin = build_plugin(r2_save=True)
    pipeline = DrawingPipeline(plugin)

    result = asyncio.run(pipeline.run({"url": False}, []))

    plugin.image_hoster.upload_images.assert_awaited_once()
    assert len(result.images) == 1
    assert result.urls == []
    assert result.error_message is None


def test_r2_save_and_url_mode_share_one_upload() -> None:
    plugin = build_plugin(r2_save=True)
    pipeline = DrawingPipeline(plugin)

    result = asyncio.run(pipeline.run({"url": True}, []))

    plugin.image_hoster.upload_images.assert_awaited_once()
    assert result.urls == ["https://cdn.example.com/image.png"]


def test_incomplete_hoster_does_not_block_normal_image_result() -> None:
    plugin = build_plugin(r2_save=True, hoster_enabled=False)
    pipeline = DrawingPipeline(plugin)

    result = asyncio.run(pipeline.run({"url": False}, []))

    plugin.image_hoster.upload_images.assert_not_awaited()
    assert len(result.images) == 1
    assert result.error_message is None
