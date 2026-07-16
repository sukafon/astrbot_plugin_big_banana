from astrbot.api import logger
from astrbot.api.message_components import Image
from astrbot.api.provider import Provider
from astrbot.core.provider.entities import LLMResponse

from ..schemas import (
    GenerationResult,
    ImageResource,
    ProviderCallResult,
)
from .base import BaseProvider
from .utils import dedupe_images, extract_markdown_images


class NativeProvider(BaseProvider):
    """通过 AstrBot 原生 provider 路由生成图片。"""

    provider_type = "native"

    async def generate_images(
        self,
    ) -> GenerationResult:
        """原生 provider 自行处理内部调度，这里只等待单次调用结果。"""
        if self.provider_config.stream:
            call_result = await self._call_stream_api()
        else:
            call_result = await self._call_api()

        if call_result.images:
            return GenerationResult(images=dedupe_images(call_result.images))

        return GenerationResult(
            error_message=call_result.error_message or "响应中未包含图片数据"
        )

    async def _call_api(self) -> ProviderCallResult:
        try:
            resp = await self.plugin.context.llm_generate(
                chat_provider_id=self.provider_config.name,
                prompt=self.params.get("prompt"),
                image_urls=[image.to_data_url() for image in self.image_list],
            )
        except Exception as e:
            err = f"原生提供商 {self.provider_config.name} 请求异常"
            logger.error(f"[BIG BANANA] {err}: {e}", exc_info=True)
            return ProviderCallResult(error_message=err)

        images = await self._extract_result(resp)
        if images:
            return ProviderCallResult(images=images)

        return self._missing_image_result()

    async def _call_stream_api(self) -> ProviderCallResult:
        try:
            prov = await self.plugin.context.provider_manager.get_provider_by_id(
                self.provider_config.name
            )
            if not prov or not isinstance(prov, Provider):
                return ProviderCallResult(
                    error_message=f"原生提供商 {self.provider_config.name} 不存在"
                )

            images: list[ImageResource] = []
            async for resp in prov.text_chat_stream(
                prompt=self.params.get("prompt", ""),
                image_urls=[image.to_data_url() for image in self.image_list],
            ):
                images.extend(await self._extract_result(resp))

        except Exception as e:
            err = f"原生提供商 {self.provider_config.name} 流式请求异常"
            logger.error(f"[BIG BANANA] {err}: {e}", exc_info=True)
            return ProviderCallResult(error_message=err)

        if images:
            return ProviderCallResult(images=images)

        return self._missing_image_result()

    async def _extract_result(
        self,
        response: LLMResponse,
    ) -> list[ImageResource]:
        """解析图片结果，优先解析 result_chain，再从 completion_text 兜底提取 Markdown 图片。"""
        result_chain = response.result_chain
        images: list[ImageResource] = []

        if result_chain is not None:
            # 从消息链中查找图片。Gemini 原生 provider 的 base64 图片在 file 字段。
            for comp in result_chain.chain:
                if isinstance(comp, Image):
                    image_ref = comp.url or comp.file
                    if not image_ref:
                        logger.warning(
                            "[BIG BANANA] 无法处理图片：消息链中的图片引用 comp.url or comp.file 为空"
                        )
                        continue
                    fetched = await self.plugin.downloader.fetch_image(
                        image_ref,
                        convert=True,
                        allow_gif=True,
                    )
                    if fetched:
                        images.append(fetched)
                    else:
                        logger.warning(f"[BIG BANANA] 无法处理图片：{image_ref[:256]}")

        # 如果消息链没有图片，尝试从 completion_text 中查找 Markdown 图片
        completion_text = response.completion_text or ""
        if not images:
            base64_sources, image_urls = extract_markdown_images(completion_text)
            for base64_source in base64_sources:
                image = await self.plugin.downloader.fetch_base64_image(
                    base64_source,
                    convert=True,
                    allow_gif=True,
                )
                if image:
                    images.append(image)
                else:
                    logger.warning("[BIG BANANA] 无法解析 Markdown 图片 base64")
            for image_url in image_urls:
                fetched = await self.plugin.downloader.fetch_image(
                    image_url,
                    use_proxy=self.provider_config.enable_proxy,
                    convert=True,
                    allow_gif=True,
                )
                if fetched:
                    images.append(fetched)
                else:
                    logger.warning(
                        f"[BIG BANANA] 无法处理 Markdown 图片：{image_url[:256]}"
                    )
            if not images and not base64_sources and not image_urls and completion_text:
                self.text_response_parts.append(completion_text)

        return images
