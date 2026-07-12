from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource
from .saver import ImageSaver

if TYPE_CHECKING:
    from ...main import BigBanana


class DrawingPipeline:
    """绘图管道"""

    def __init__(self, plugin: BigBanana) -> None:
        """初始化绘图组件。"""
        self.plugin = plugin
        self.image_saver = ImageSaver()

    async def run(
        self, params: dict, image_list: list[ImageResource] | None
    ) -> GenerationResult:
        """负责生成、上传/保存和错误收尾"""
        # 抹除输入图片的隐私元数据
        if self.plugin.common_config.strip_metadata and image_list:
            cleaned_list = []
            for img in image_list:
                stripped = ImageResource.strip_metadata(img.bytes)
                if stripped is None:
                    logger.warning("[BIG BANANA] 无法处理图片，已移除该参考图")
                    continue
                img.bytes = stripped
                img._b64_cache = None
                cleaned_list.append(img)
            image_list[:] = cleaned_list

        # 调度底层提供商生成图片
        dispatch_result = await self.plugin.dispatcher.dispatch(
            params=params, image_list=image_list
        )

        # 检查错误
        if not dispatch_result.images:
            err = dispatch_result.error_message
            if not err:
                err = "图片生成失败：响应中未包含图片数据"
                logger.error(err)
            return GenerationResult(error_message=err)

        url_mode = params.get("url", self.plugin.params_config.url)
        r2_save = self.plugin.save_images.r2_save
        uploaded_urls: list[str | None] = [None] * len(dispatch_result.images)

        # URL 返回与 R2 归档共用一次上传，避免同时启用时重复保存。
        if url_mode or r2_save:
            if self.plugin.image_hoster.is_enabled():
                uploaded_urls = await self.plugin.image_hoster.upload_images(
                    dispatch_result.images
                )
                if r2_save:
                    saved_count = sum(url is not None for url in uploaded_urls)
                    if saved_count == len(dispatch_result.images):
                        logger.info(
                            f"[BIG BANANA] 已保存 {saved_count} 张图片到 R2 图床"
                        )
                    else:
                        logger.warning(
                            f"[BIG BANANA] 共生成 {len(dispatch_result.images)} 张图片，"
                            f"其中 {saved_count} 张成功保存到 R2 图床"
                        )
            elif r2_save:
                logger.warning(
                    "[BIG BANANA] 已启用 R2 图床保存，但图床配置未启用或不完整，"
                    "已跳过上传"
                )

        # URL 模式
        if url_mode:
            # 每张图片优先使用图床 URL；上传失败时使用提供商原始 URL。
            result_urls: list[str] = []
            for image, uploaded_url in zip(dispatch_result.images, uploaded_urls):
                if uploaded_url:
                    result_urls.append(uploaded_url)
                elif isinstance(image.url, str) and image.url.startswith(
                    ("http://", "https://")
                ):
                    result_urls.append(image.url)

            if result_urls:
                if len(result_urls) < len(dispatch_result.images):
                    logger.warning(
                        f"[BIG BANANA] 共生成 {len(dispatch_result.images)} 张图片，"
                        f"其中 {len(result_urls)} 张取得了可用 URL，将返回现有结果"
                    )
                return GenerationResult(
                    images=dispatch_result.images,
                    urls=result_urls,
                )

            # 没有获得可用url，返回错误
            return GenerationResult(
                error_message=dispatch_result.error_message
                or "当前结果无法转换为可访问 URL，请检查图床配置或提供商返回的结果"
            )

        # 本地保存生成的图片
        if self.plugin.save_images.local_save:
            self.image_saver.save_images_to_local(
                dispatch_result.images, self.plugin.save_dir
            )

        return GenerationResult(images=dispatch_result.images)
