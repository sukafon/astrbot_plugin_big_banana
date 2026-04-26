import base64
import json
from io import BytesIO

from curl_cffi import CurlMime
from curl_cffi.requests.exceptions import Timeout
from PIL import Image

from astrbot.api import logger

from .base import BaseProvider
from .data import ProviderConfig


class OpenAIImagesProvider(BaseProvider):
    """OpenAI 官方 Images API 提供商"""

    api_type: str = "OpenAI_Images"

    async def _call_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            if image_b64_list:
                response = await self._post_image_edits(
                    provider_config=provider_config,
                    headers=headers,
                    image_b64_list=image_b64_list,
                    params=params,
                )
            else:
                response = await self.session.post(
                    url=self._build_api_url(provider_config.api_url, "generations"),
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "model": provider_config.model,
                        "prompt": params.get("prompt", "anything"),
                    },
                    timeout=self.def_common_config.timeout,
                    proxy=self.def_common_config.proxy,
                )

            result = response.json()
            if response.status_code == 200:
                images_result, err = self._parse_images_response(result)
                if images_result:
                    return images_result, 200, None
                logger.warning(
                    f"[BIG BANANA] OpenAI Images 请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}"
                )
                return None, 200, err or "响应中未包含图片数据"

            logger.error(
                f"[BIG BANANA] OpenAI Images 图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}"
            )
            return (
                None,
                response.status_code,
                self._extract_error_message(result)
                or f"图片生成失败: 状态码 {response.status_code}",
            )
        except ValueError as e:
            logger.error(f"[BIG BANANA] OpenAI Images 输入图片处理失败: {e}")
            return None, None, str(e)
        except Timeout as e:
            logger.error(f"[BIG BANANA] OpenAI Images 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except json.JSONDecodeError as e:
            logger.error(
                f"[BIG BANANA] OpenAI Images JSON反序列化错误: {e}，状态码：{response.status_code}，响应内容：{response.text[:1024]}"
            )
            return None, response.status_code, "图片生成失败：响应内容格式错误"
        except Exception as e:
            logger.error(f"[BIG BANANA] OpenAI Images 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    async def _call_stream_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        logger.warning(
            "[BIG BANANA] OpenAI_Images 暂不支持流式响应，将自动回退为非流式请求"
        )
        return await self._call_api(
            provider_config=provider_config,
            api_key=api_key,
            image_b64_list=image_b64_list,
            params=params,
        )

    async def _post_image_edits(
        self,
        provider_config: ProviderConfig,
        headers: dict,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ):
        logger.info(
            f"[BIG BANANA] OpenAI Images 正在请求 /images/edits，上传参考图 {len(image_b64_list)} 张"
        )
        data = {
            "model": provider_config.model,
            "prompt": params.get("prompt", "anything"),
        }
        multipart = CurlMime()
        for index, (mime, b64_data) in enumerate(image_b64_list, start=1):
            file_name, image_bytes, file_mime = self._normalize_image_payload(
                mime, b64_data, index
            )
            multipart.addpart(
                name="image",
                content_type=file_mime,
                filename=file_name,
                data=image_bytes,
            )
        try:
            return await self.session.post(
                url=self._build_api_url(provider_config.api_url, "edits"),
                headers=headers,
                data=data,
                multipart=multipart,
                timeout=self.def_common_config.timeout,
                proxy=self.def_common_config.proxy,
            )
        finally:
            multipart.close()

    @staticmethod
    def _build_api_url(api_url: str, endpoint: str) -> str:
        return f"{api_url.rstrip('/')}/{endpoint}"

    @staticmethod
    def _normalize_image_payload(
        mime: str, b64_data: str, index: int
    ) -> tuple[str, bytes, str]:
        raw_bytes = base64.b64decode(b64_data)
        try:
            with Image.open(BytesIO(raw_bytes)) as img:
                if getattr(img, "is_animated", False):
                    img.seek(0)
                img = img.convert("RGB")
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=100)
                return f"image_{index}.jpg", buf.getvalue(), "image/jpeg"
        except Exception as e:
            logger.warning(f"[BIG BANANA] OpenAI Images 输入图片归一化失败，将尝试使用原始字节: {e}")
            if mime in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
                ext = mime.split("/")[-1].replace("jpg", "jpeg")
                return f"image_{index}.{ext}", raw_bytes, mime.replace("image/jpg", "image/jpeg")
            raise ValueError("图片生成失败：存在不支持的输入图片格式，无法上传到 OpenAI Images API")

    @staticmethod
    def _parse_images_response(
        result: dict,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        image_result: list[tuple[str, str]] = []
        for item in result.get("data", []):
            b64_data = item.get("b64_json")
            if b64_data:
                image_result.append(("image/png", b64_data))
        if image_result:
            return image_result, None
        return None, OpenAIImagesProvider._extract_error_message(result)

    @staticmethod
    def _extract_error_message(result: dict) -> str | None:
        error = result.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message[:200]
        return None
