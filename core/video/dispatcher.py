from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..providers import BaseVideoProvider
from ..schemas import GenerationResult, ImageResource, ProviderConfig

if TYPE_CHECKING:
    from ...main import BigBanana


class VideoProviderDispatcher:
    """Dispatch video generation to capability-compatible providers."""

    def __init__(self, plugin: BigBanana) -> None:
        """Store the active plugin instance.

        Args:
            plugin: Active plugin instance.
        """
        self.plugin = plugin

    async def dispatch(
        self,
        params: dict,
        image_list: list[ImageResource] | None,
    ) -> GenerationResult:
        """Try configured video providers in priority order.

        Args:
            params: Resolved generation parameters.
            image_list: Optional input images.

        Returns:
            The first successful video result or the final error.
        """
        logger.info(
            f"[BIG BANANA] 正在生成视频，提示词: {params.get('prompt', '')[:60]}"
        )
        provider_names = params.get(
            "providers",
            self.plugin.provider_config_manager.get_default_providers(
                "video_generation"
            ),
        )
        if not provider_names:
            return GenerationResult(error_message="未配置可用的视频生成提供商")

        last_error: str | None = None
        for provider_name in provider_names:
            provider_config = self.plugin.provider_config_manager.provider_configs.get(
                provider_name
            )
            if provider_config is None:
                last_error = f"未找到视频提供商 {provider_name}"
                logger.error(f"[BIG BANANA] {last_error}，已跳过")
                continue
            if provider_config.capability != "video_generation":
                last_error = f"提供商 {provider_name} 不支持视频生成"
                logger.warning(f"[BIG BANANA] {last_error}，已跳过")
                continue
            if not provider_config.enabled:
                last_error = f"视频提供商 {provider_name} 未启用"
                logger.info(f"[BIG BANANA] {last_error}，已跳过")
                continue

            provider_images = image_list
            if (
                image_list
                and provider_config.max_images >= 0
                and len(image_list) > provider_config.max_images
            ):
                provider_images = image_list[: provider_config.max_images]

            result = await self._dispatch_provider(
                provider_config,
                params=params,
                image_list=provider_images,
            )
            if result.videos:
                return result
            last_error = result.error_message or "视频生成未返回结果"

        return GenerationResult(error_message=last_error)

    async def _dispatch_provider(
        self,
        provider_config: ProviderConfig,
        *,
        params: dict,
        image_list: list[ImageResource] | None,
    ) -> GenerationResult:
        provider_cls = BaseVideoProvider.get_provider_class(
            provider_config.provider_type
        )
        if provider_cls is None:
            return GenerationResult(
                error_message=(
                    f"未找到类型为 {provider_config.provider_type} 的视频提供商实现"
                )
            )
        try:
            provider = provider_cls(
                self.plugin,
                provider_config,
                params,
                image_list,
            )
            return await provider.generate_videos()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                f"[BIG BANANA] 视频提供商 {provider_config.name} 发生内部错误: {exc}",
                exc_info=True,
            )
            return GenerationResult(
                error_message=f"视频提供商 {provider_config.name} 发生内部错误"
            )
