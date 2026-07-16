from __future__ import annotations

import asyncio
import json
import math
import random
from io import BytesIO
from typing import Any

from aiohttp import ClientSession, ClientTimeout, FormData
from PIL import Image

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource, ProviderCallResult
from .base import BaseProvider
from .utils import dedupe_images

RETRY_STATUS_CODES = (408, 500, 502, 503, 504)
NO_RETRY_STATUS_CODES = (0, 401, 402, 403, 422, 429)
"""0: 初始值，用于表示尚未拿到有效的 HTTP/API 状态码。"""


class StandardProvider(BaseProvider):
    """提供商的标准流程基类"""

    image_download_headers: dict[str, str] | None = None

    async def initialize(self) -> None:
        """初始化 HTTP 调用上下文。"""
        self.session = self.get_session()
        self.timeout = self.get_timeout()
        self.proxy = self.get_proxy()
        self._body_context_cache: dict | None = None

    def get_session(self) -> ClientSession:
        """获取 aiohttp 客户端会话。"""
        return self.plugin.http_manager.get_aiohttp_session()

    def get_timeout(self) -> ClientTimeout:
        """获取 aiohttp 可用的超时配置。"""
        return ClientTimeout(total=self.plugin.common_config.timeout)

    def get_proxy(self) -> str | None:
        """获取当前提供商可用的代理配置。"""
        if self.provider_config.enable_proxy:
            return self.plugin.common_config.proxy
        return None

    def should_retry(self, status: object) -> bool:
        """判断指定状态码是否适合继续重试。"""
        if not self.plugin.common_config.smart_retry:
            return True
        if status is None or status in NO_RETRY_STATUS_CODES:
            return False
        return status in RETRY_STATUS_CODES

    def determine_openai_size(self) -> str:
        """根据参数、提示词或参考图尺寸推导 OpenAI 图片输出大小。"""
        configured_size = self.params.get("size", self.plugin.params_config.size)
        if configured_size != "default":
            return configured_size

        prompt = self.params.get("prompt", "")
        for keywords, size in self.plugin.params_config.size_keyword_map.items():
            for keyword in keywords:
                if keyword in prompt:
                    return size

        if self.image_list:
            img = self.image_list[0]
            raw_bytes = img.bytes
            try:
                with Image.open(BytesIO(raw_bytes)) as img_obj:
                    w, h = img_obj.size

                if w > 3 * h:
                    w = 3 * h
                elif h > 3 * w:
                    h = 3 * w

                max_area = 8294400
                min_area = 655360
                max_edge = 3840

                scale = 1.0
                if w * h > max_area:
                    scale = math.sqrt(max_area / (w * h))
                elif w * h < min_area:
                    scale = math.sqrt(min_area / (w * h))

                w = int(w * scale)
                h = int(h * scale)

                if w > max_edge:
                    scale = max_edge / w
                    w = max_edge
                    h = int(h * scale)
                if h > max_edge:
                    scale = max_edge / h
                    h = max_edge
                    w = int(w * scale)

                w = max(16, round(w / 16) * 16)
                h = max(16, round(h / 16) * 16)

                if w > 3 * h:
                    w = 3 * h
                    w = max(16, round(w / 16) * 16)
                elif h > 3 * w:
                    h = 3 * w
                    h = max(16, round(h / 16) * 16)

                while w * h > max_area or max(w, h) > max_edge:
                    if w > h:
                        w -= 16
                    else:
                        h -= 16

                while w * h < min_area:
                    if w < h:
                        w += 16
                    else:
                        h += 16

                return f"{w}x{h}"
            except Exception as e:
                logger.warning(
                    f"[BIG BANANA] 获取参考图分辨率失败: {e}，将使用默认尺寸 auto"
                )

        return "auto"

    async def generate_images(self) -> GenerationResult:
        """按 Key 轮询和重试策略调度具体提供商生成图片。"""
        # 拷贝，防止打乱原列表
        keys = list(self.provider_config.keys) or [""]
        # 随机打乱key顺序
        random.shuffle(keys)
        # 读取重试次数
        max_retry = max(1, self.plugin.common_config.max_retry)
        # 上一次的错误信息
        last_err: str | None = None
        for key_index, api_key in enumerate(keys, start=1):
            # 同Key重试
            for attempt in range(1, max_retry + 1):
                # 根据配置选择流式或非流式调用
                if self.provider_config.stream:
                    call_result = await self._call_stream_api(api_key)
                else:
                    call_result = await self._call_api(api_key)
                # 如果有图片数据，返回结果
                if call_result.images:
                    return GenerationResult(images=dedupe_images(call_result.images))
                # 更新错误信息
                last_err = call_result.error_message or "响应中未包含图片数据"
                if call_result.status_code != 0:
                    last_err = f"HTTP {call_result.status_code}：{last_err}"
                # 如果重试次数已达上限，跳出循环
                if attempt >= max_retry:
                    break
                if not self.should_retry(call_result.status_code):
                    break
                # 符合重试条件
                logger.warning(
                    f"[BIG BANANA] 图片生成失败，正在重试 {self.provider_config.name} "
                    f"当前 Key({api_key[-8:] if api_key else '无 Key'}) "
                    f"({attempt}/{max_retry})"
                )

            if key_index < len(keys):
                logger.warning(
                    f"[BIG BANANA] 图片生成失败，当前 Key("
                    f"{api_key[-8:] if api_key else '无 Key'}) 不可用，"
                    f"切换到 {self.provider_config.name} 下一个 Key"
                )

        return GenerationResult(
            error_message=last_err or "图片生成失败：所有 Key 均已用尽或不可用"
        )

    async def _call_api(self, api_key: str) -> ProviderCallResult:
        """非流式请求"""
        response = None
        response_text = ""
        try:
            body_context = self._build_body_context()
            post_kwargs: dict[str, Any] = {
                "url": self._build_api_url(),
                "headers": self._build_headers(api_key),
                "proxy": self.proxy,
                "timeout": self.timeout,
            }
            if isinstance(body_context, FormData):
                post_kwargs["data"] = body_context
            else:
                post_kwargs["json"] = body_context

            async with self.session.post(**post_kwargs) as resp:
                response = resp
                response_text = await resp.text()
                result = json.loads(response_text)
                if resp.status == 200:
                    image_sources, reason = self._extract_result(result)
                    images = await self._build_images(image_sources)
                    if images:
                        return ProviderCallResult(images=images, status_code=200)
                    return self._missing_image_result(
                        reason,
                        response_text=response_text,
                    )
                # 解析错误原因
                err_msg = result.get("error", {}).get("message", "未知原因")
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {resp.status}，原因: {err_msg}"
                )
                return ProviderCallResult(
                    status_code=resp.status,
                    error_message=err_msg,
                )
        except asyncio.TimeoutError as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return ProviderCallResult(
                status_code=408,
                error_message="响应超时",
            )
        except json.JSONDecodeError as e:
            status_code = response.status if response is not None else 0
            resp_text = response_text[:1024] or "未知"
            logger.error(
                f"[BIG BANANA] JSON反序列化错误: {e}，状态码：{status_code}，响应内容：{resp_text}"
            )
            return ProviderCallResult(
                status_code=status_code,
                error_message="响应内容格式错误",
            )
        except Exception as e:
            logger.error(f"[BIG BANANA] 请求错误: {e}")
            return ProviderCallResult(error_message="程序错误")

    async def _call_stream_api(self, api_key: str) -> ProviderCallResult:
        """流式请求"""
        response = None
        response_text = ""
        try:
            body_context = self._build_body_context()
            post_kwargs: dict[str, Any] = {
                "url": self._build_api_url(),
                "headers": self._build_headers(api_key),
                "proxy": self.proxy,
                "timeout": self.timeout,
            }
            if isinstance(body_context, FormData):
                post_kwargs["data"] = body_context
            else:
                post_kwargs["json"] = body_context

            async with self.session.post(**post_kwargs) as resp:
                response = resp
                data = b""
                async for chunk in resp.content.iter_chunked(1024):
                    data += chunk
                response_text = data.decode("utf-8")
                if resp.status == 200:
                    image_sources, reason = self._extract_stream_result(response_text)
                    images = await self._build_images(image_sources)
                    if images:
                        return ProviderCallResult(images=images, status_code=200)
                    return self._missing_image_result(
                        reason,
                        response_text=response_text,
                    )
                # 解析错误原因
                err_msg = (
                    json.loads(response_text)
                    .get("error", {})
                    .get("message", "未知原因")
                )
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {resp.status}，原因: {err_msg}"
                )
                return ProviderCallResult(
                    status_code=resp.status,
                    error_message=err_msg,
                )
        except asyncio.TimeoutError as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return ProviderCallResult(
                status_code=408,
                error_message="响应超时",
            )
        except json.JSONDecodeError as e:
            status_code = response.status if response is not None else 0
            resp_text = response_text[:1024] or "未知"
            logger.error(
                f"[BIG BANANA] JSON反序列化错误: {e}，状态码：{status_code}，响应内容：{resp_text}"
            )
            return ProviderCallResult(
                status_code=status_code,
                error_message="响应内容格式错误",
            )
        except Exception as e:
            logger.error(f"[BIG BANANA] 请求错误: {e}")
            return ProviderCallResult(error_message="程序错误")

    def _build_api_url(self) -> str:
        """构建请求地址。使用默认调用流程的子类必须实现。"""
        raise NotImplementedError

    def _build_headers(self, api_key: str) -> dict[str, str]:
        """构建请求头。使用默认调用流程的子类必须实现。"""
        raise NotImplementedError

    def _build_body_context(self) -> dict | FormData:
        """构建请求体。使用默认调用流程的子类必须实现。"""
        raise NotImplementedError

    def _extract_result(
        self,
        result: dict,
    ) -> tuple[list[str], str | None]:
        """解析非流式响应中的图片来源和失败原因。使用默认调用流程的子类必须实现。"""
        raise NotImplementedError

    def _extract_stream_result(
        self,
        stream_text: str,
    ) -> tuple[list[str], str | None]:
        """解析流式响应中的图片来源和失败原因。使用默认调用流程的子类必须实现。"""
        raise NotImplementedError

    async def _build_images(self, image_sources: list[str]) -> list[ImageResource]:
        """把 base64 或 URL 图片来源转换为图片资源。"""
        images: list[ImageResource] = []
        image_urls: list[str] = []
        for source in image_sources:
            if source.startswith(("http://", "https://")):
                image_urls.append(source)
                continue
            image = await self.plugin.downloader.fetch_base64_image(
                source,
                convert=True,
                allow_gif=True,
            )
            if image:
                images.append(image)
            else:
                logger.warning("[BIG BANANA] 无法解析图片 base64")
        if image_urls:
            images.extend(
                await self.plugin.downloader.fetch_images(
                    image_urls,
                    use_proxy=self.provider_config.enable_proxy,
                    convert=True,
                    allow_gif=True,
                    headers=self.image_download_headers,
                )
            )
        return images
