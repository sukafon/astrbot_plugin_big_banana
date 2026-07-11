import asyncio
import json
from urllib.request import ProxyHandler, Request, build_opener

from curl_cffi.requests.exceptions import Timeout

from astrbot.api import logger

from .base import BaseProvider
from .data import ProviderConfig
from .downloader import Downloader
from .openai_images import OpenAIImagesProvider


class AgnesImagesProvider(BaseProvider):
    """Agnes Images API 提供商"""

    api_type: str = "Agnes_Images"

    async def _call_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        size = OpenAIImagesProvider._determine_size(params, image_b64_list)
        payload = self._build_payload(provider_config.model, params, image_b64_list, size)
        try:
            response = await self.session.post(
                url=self._build_api_url(provider_config.api_url),
                headers=headers,
                json=payload,
                timeout=self.def_common_config.timeout,
                proxy=self.def_common_config.proxy,
            )
            result = response.json()
            if response.status_code == 200:
                images_result, err = await self._parse_images_response(result)
                if images_result or (params.get("url", False) and self.last_result_urls):
                    return images_result, 200, None
                logger.warning(
                    f"[BIG BANANA] Agnes Images 请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}"
                )
                return None, 200, err or "响应中未包含图片数据"

            logger.error(
                f"[BIG BANANA] Agnes Images 图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}"
            )
            return (
                None,
                response.status_code,
                self._extract_error_message(result)
                or f"图片生成失败: 状态码 {response.status_code}",
            )
        except Timeout as e:
            logger.error(f"[BIG BANANA] Agnes Images 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except json.JSONDecodeError as e:
            logger.error(
                f"[BIG BANANA] Agnes Images JSON反序列化错误: {e}，状态码：{response.status_code}，响应内容：{response.text[:1024]}"
            )
            return None, response.status_code, "图片生成失败：响应内容格式错误"
        except Exception as e:
            logger.error(f"[BIG BANANA] Agnes Images 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    async def _call_stream_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        logger.warning(
            "[BIG BANANA] Agnes_Images 暂不支持流式响应，将自动回退为非流式请求"
        )
        return await self._call_api(
            provider_config=provider_config,
            api_key=api_key,
            image_b64_list=image_b64_list,
            params=params,
        )

    @staticmethod
    def _build_api_url(api_url: str) -> str:
        return f"{api_url.rstrip('/')}/generations"

    @staticmethod
    def _build_payload(
        model: str,
        params: dict,
        image_b64_list: list[tuple[str, str]],
        size: str,
    ) -> dict:
        payload = {
            "model": model,
            "prompt": params.get("prompt", "anything"),
            "n": params.get("n", 1),
            "size": size,
        }
        if image_b64_list:
            payload["extra_body"] = {
                "image": [f"data:{mime};base64,{b64}" for mime, b64 in image_b64_list],
                "response_format": "url",
            }
        return payload

    async def _parse_images_response(
        self,
        result: dict,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        image_result: list[tuple[str, str]] = []
        image_urls: list[str] = []
        for item in result.get("data", []):
            if not isinstance(item, dict):
                continue
            b64_data = item.get("b64_json")
            if isinstance(b64_data, str) and b64_data:
                image_result.append(("image/png", b64_data))
                continue
            image_url = item.get("url")
            if isinstance(image_url, str) and image_url:
                image_urls.append(image_url)
        self.last_result_urls = list(image_urls)
        if image_urls:
            image_result.extend(await self._fetch_images_from_urls(image_urls))
        if image_result:
            return image_result, None
        return None, self._extract_error_message(result)

    async def _fetch_images_from_urls(
        self, image_urls: list[str]
    ) -> list[tuple[str, str]]:
        image_result: list[tuple[str, str]] = []
        for image_url in image_urls:
            content = await self._download_image_from_url(image_url)
            if content is not None:
                image_result.append(content)
        return image_result

    async def _download_image_from_url(self, image_url: str) -> tuple[str, str] | None:
        try:
            image_bytes = await asyncio.to_thread(
                self._download_image_bytes_with_urllib, image_url
            )
            if not image_bytes:
                return None
            return await asyncio.to_thread(Downloader._handle_image, image_bytes)
        except Exception as e:
            logger.error(
                f"[BIG BANANA] Agnes Images 下载图片失败: {image_url}，错误信息：{e}"
            )
            return None

    def _download_image_bytes_with_urllib(self, image_url: str) -> bytes | None:
        opener = build_opener(self._build_proxy_handler())
        request = Request(
            image_url,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        )
        with opener.open(request, timeout=30) as response:
            image_bytes = response.read()
        if not image_bytes or len(image_bytes) > 50 * 1024 * 1024:
            return None
        return image_bytes

    def _build_proxy_handler(self) -> ProxyHandler:
        proxy = self.def_common_config.proxy
        if proxy:
            return ProxyHandler({"http": proxy, "https": proxy})
        return ProxyHandler({})

    @staticmethod
    def _extract_error_message(result: dict) -> str | None:
        error = result.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message[:200]
        return None
