from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.provider import Provider

from ..providers import BaseProvider
from ..schemas import GenerationResult, ImageResource, ProviderConfig

if TYPE_CHECKING:
    from ...main import BigBanana


class ProviderDispatcher:
    """提供商调度器，用于解析配置并实例化、调用适当的图像生成 Provider。"""

    def __init__(self, plugin: BigBanana) -> None:
        """调度提供商"""
        self.plugin = plugin

    async def dispatch(
        self,
        params: dict,
        image_list: list[ImageResource] | None,
    ) -> GenerationResult:
        """按配置顺序逐个尝试绘图提供商，返回统一的生成结果。"""
        logger.info(
            f"[BIG BANANA]正在生成图片，提示词: {params.get('prompt', '')[:60]}"
        )
        last_err = None
        # 读取提供商顺序列表，优先使用参数传入的，没有则使用默认
        provider_names = params.get(
            "providers", self.plugin.provider_config_manager.default_providers
        )

        if not provider_names:
            return GenerationResult(error_message="未配置可用的绘图提供商")

        for provider_name in provider_names:
            # 读取提供商配置
            provider_config = await self._get_provider_config(provider_name)
            if provider_config is None:
                last_err = f"未找到名为 {provider_name} 的模板提供商或原生聊天提供商"
                logger.error(f"[BIG BANANA] {last_err}，已跳过")
                continue

            if provider_config.capability != "image_generation":
                last_err = f"提供商 {provider_config.name} 不支持图片生成"
                logger.warning(f"[BIG BANANA] {last_err}，已跳过")
                continue

            # 检查提供商是否启用
            if not provider_config.enabled:
                last_err = f"提供商 {provider_config.name} 未启用"
                logger.info(f"[BIG BANANA] {last_err}，已跳过")
                continue

            # Truncate image_list if it exceeds provider's max_images (skip if -1)
            provider_image_list = image_list
            if (
                image_list
                and provider_config.max_images >= 0
                and len(image_list) > provider_config.max_images
            ):
                logger.warning(
                    f"[BIG BANANA] 提供商 {provider_config.name} 限制最大图片数为 {provider_config.max_images}，"
                    f"当前有 {len(image_list)} 张图片，已截断为 {provider_config.max_images} 张"
                )
                provider_image_list = image_list[: provider_config.max_images]

            # 使用统一 provider 注册表分发
            result = await self._dispatch_provider(
                provider_config,
                params=params,
                image_list=provider_image_list,
            )
            if result.error_message is None:
                return result
            last_err = result.error_message

        return GenerationResult(error_message=last_err)

    async def _dispatch_provider(
        self,
        provider_config: ProviderConfig,
        *,
        params: dict,
        image_list: list[ImageResource] | None,
    ) -> GenerationResult:
        """加载具体 provider 实例并执行生成。"""
        # 读取提供商类对象
        provider_cls = BaseProvider.get_provider_class(provider_config.provider_type)
        if provider_cls is None:
            # 提供商类型缺失属于开发层面的错误，这里直接返回结果。
            err = f"未找到类型为 {provider_config.provider_type} 的提供商实现"
            logger.error(f"[BIG BANANA] {err}")
            return GenerationResult(error_message=err)

        # 调用提供商实例生成图片
        try:
            # 这个实例化过程很轻，不复用单例，便于状态管理
            provider_inst = provider_cls(
                self.plugin,
                provider_config,
                params,
                image_list,
            )
            await provider_inst.initialize()
            # 调用提供商的生成图片方法
            provider_result = await provider_inst.generate_images()
            return provider_result
        except Exception as e:
            err = f"提供商 {provider_config.name} 发生内部错误"
            logger.error(f"[BIG BANANA] {err}: {e}", exc_info=True)
            return GenerationResult(error_message=err)

    async def _get_provider_config(
        self,
        provider_name: str,
    ) -> ProviderConfig | None:
        """读取 provider 配置"""
        # 先尝试从模板提供商中读取配置
        provider_config = self.plugin.provider_config_manager.provider_configs.get(
            provider_name
        )
        if provider_config is not None:
            return provider_config
        # 没有找到模板提供商，尝试从原生提供商查找
        provider = await self.plugin.context.provider_manager.get_provider_by_id(
            provider_name
        )
        # 查找到以后包装成ProviderConfig
        if isinstance(provider, Provider):
            return ProviderConfig(
                provider_type="native",
                name=provider_name,
            )
        return None
