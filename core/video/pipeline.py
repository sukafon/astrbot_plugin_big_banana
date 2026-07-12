from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource

if TYPE_CHECKING:
    from ...main import BigBanana


class VideoPipeline:
    """Prepare reference images and run a video provider."""

    def __init__(self, plugin: BigBanana) -> None:
        """Store the active plugin instance.

        Args:
            plugin: Active plugin instance.
        """
        self.plugin = plugin

    async def run(
        self,
        params: dict,
        image_list: list[ImageResource] | None,
    ) -> GenerationResult:
        """Prepare reference images and generate videos.

        Args:
            params: Resolved generation parameters.
            image_list: Optional input images.

        Returns:
            Generated videos or an error result.
        """
        if self.plugin.common_config.strip_metadata and image_list:
            cleaned_images: list[ImageResource] = []
            for image in image_list:
                stripped = ImageResource.strip_metadata(image.bytes)
                if stripped is None:
                    logger.warning("[BIG BANANA] 无法处理视频参考图，已移除")
                    continue
                image.bytes = stripped
                image._b64_cache = None
                cleaned_images.append(image)
            image_list[:] = cleaned_images

        result = await self.plugin.video_dispatcher.dispatch(params, image_list)
        if result.videos:
            return result
        return GenerationResult(
            error_message=result.error_message or "视频生成未返回视频 URL"
        )
