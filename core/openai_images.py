import base64
import json
import math
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

    @staticmethod
    def _determine_size(params: dict, image_b64_list: list[tuple[str, str]]) -> str:
        if "size" in params:
            return params["size"]

        prompt = params.get("prompt", "")
        if "横屏" in prompt:
            return "1536x1024"
        if "竖屏" in prompt or "手机" in prompt:
            return "1024x1536"

        if image_b64_list:
            mime, b64_data = image_b64_list[0]
            raw_bytes = base64.b64decode(b64_data)
            try:
                with Image.open(BytesIO(raw_bytes)) as img:
                    w, h = img.size

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

    async def _call_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        headers = {"Authorization": f"Bearer {api_key}"}
        size = self._determine_size(params, image_b64_list)
        is_minimax = (
            "minimax" in provider_config.api_url.lower()
            or "minimax" in provider_config.api_name.lower()
            or (provider_config.model and "image-01" in provider_config.model.lower())
        )

        model_lower = (provider_config.model or "").lower()
        is_known_not_to_support_edits = False
        if any(
            keyword in model_lower
            for keyword in [
                "dall-e-3",
                "flux",
                "sd",
                "cogview",
                "playground",
                "imagen",
                "mj",
                "midjourney",
            ]
        ):
            is_known_not_to_support_edits = True

        try:
            response = None
            if image_b64_list and not is_minimax:
                if is_known_not_to_support_edits:
                    logger.error(
                        f"[BIG BANANA] OpenAI Images 请求失败: 当前模型 {provider_config.model} 不支持图片编辑/图生图，且不可回退"
                    )
                    return (
                        None,
                        400,
                        f"当前模型 {provider_config.model} 不支持图片编辑/图生图",
                    )

                try:
                    response = await self._post_image_edits(
                        provider_config=provider_config,
                        headers=headers,
                        image_b64_list=image_b64_list,
                        params=params,
                        size=size,
                    )
                except Timeout as e:
                    logger.error(
                        f"[BIG BANANA] OpenAI Images /images/edits 请求超时: {e}"
                    )
                    return None, 408, f"图片编辑请求超时: {e}"
                except Exception as e:
                    logger.error(
                        f"[BIG BANANA] OpenAI Images /images/edits 请求异常: {e}"
                    )
                    return None, 502, f"图片编辑网络请求异常: {e}"

                if response.status_code != 200:
                    err_msg = ""
                    try:
                        err_data = response.json()
                        err_msg = self._extract_error_message(err_data) or ""
                    except Exception:
                        pass

                    text_to_check = err_msg or response.text or ""
                    logger.error(
                        f"[BIG BANANA] OpenAI Images /images/edits 请求失败 (状态码 {response.status_code})。原因: {text_to_check}"
                    )
                    return (
                        None,
                        response.status_code,
                        text_to_check or f"图片编辑失败: 状态码 {response.status_code}",
                    )

            if response is None:
                # 默认返回格式
                req_format = "b64_json"
                if params.get("url", False):
                    req_format = "url"

                if is_minimax:
                    minimax_format = "base64" if req_format == "b64_json" else "url"
                    json_payload = {
                        "model": provider_config.model,
                        "prompt": params.get("prompt", "anything"),
                        "response_format": minimax_format,
                        "number_of_images": params.get("n", 1),
                    }
                    aspect_ratio = params.get("aspect_ratio", "1:1")
                    if aspect_ratio and aspect_ratio != "default":
                        json_payload["aspect_ratio"] = aspect_ratio

                    if image_b64_list:
                        subject_refs = []
                        for mime, b64 in image_b64_list:
                            subject_refs.append(
                                {
                                    "type": "character",
                                    "image_file": f"data:{mime};base64,{b64}",
                                }
                            )
                        json_payload["subject_reference"] = subject_refs
                else:
                    json_payload = {
                        "model": provider_config.model,
                        "prompt": params.get("prompt", "anything"),
                        "n": params.get("n", 1),
                        "size": size,
                        "response_format": req_format,
                    }

                response = await self.session.post(
                    url=self._build_api_url(
                        provider_config.api_url, "generations", is_minimax=is_minimax
                    ),
                    headers={**headers, "Content-Type": "application/json"},
                    json=json_payload,
                    timeout=self.def_common_config.timeout,
                    proxy=self.def_common_config.proxy,
                )

            result = response.json()
            if response.status_code == 200:
                # 检查 Minimax 业务错误 (Minimax 错误时依然返回 200)
                base_resp = result.get("base_resp")
                if isinstance(base_resp, dict):
                    biz_code = base_resp.get("status_code", 0)
                    if biz_code != 0:
                        err_msg = base_resp.get("status_msg", "未知错误")
                        logger.error(
                            f"[BIG BANANA] Minimax 业务错误，错误代码：{biz_code}，原因：{err_msg}"
                        )
                        return None, 400, f"图片生成失败: {err_msg}"

                # 1. 尝试解析 Base64 结果
                images_result, err = self._parse_images_response(result)
                if images_result:
                    return images_result, 200, None

                # 2. 尝试从响应中获取并下载 URL 结果
                urls = []
                data = result.get("data")
                if isinstance(data, list):
                    for item in data:
                        url = item.get("url")
                        if url:
                            urls.append(url)
                elif isinstance(data, dict):
                    if "images" in data and isinstance(data["images"], list):
                        for item in data["images"]:
                            url = item.get("url")
                            if url:
                                urls.append(url)
                    elif "image_urls" in data and isinstance(data["image_urls"], list):
                        for url in data["image_urls"]:
                            if url:
                                urls.append(url)
                    elif "url" in data:
                        urls.append(data["url"])

                if urls:
                    self.last_result_urls = list(urls)
                    if params.get("url", False):
                        return [], 200, None
                    try:
                        fetched = await self.downloader.fetch_images(urls)
                        if fetched:
                            return fetched, 200, None
                    except Exception as e:
                        logger.error(f"[BIG BANANA] 下载图片 URL 失败: {e}")

                logger.warning(
                    f"[BIG BANANA] OpenAI/Minimax Images 请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}"
                )
                return None, 200, err or "响应中未包含图片数据"

            logger.error(
                f"[BIG BANANA] OpenAI/Minimax Images 图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}"
            )
            return (
                None,
                response.status_code,
                self._extract_error_message(result)
                or f"图片生成失败: 状态码 {response.status_code}",
            )
        except Timeout as e:
            logger.error(f"[BIG BANANA] OpenAI/Minimax Images 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except json.JSONDecodeError as e:
            logger.error(
                f"[BIG BANANA] OpenAI/Minimax Images JSON反序列化错误: {e}，状态码：{response.status_code}，响应内容：{response.text[:1024]}"
            )
            return None, response.status_code, "图片生成失败：响应内容格式错误"
        except Exception as e:
            logger.error(f"[BIG BANANA] OpenAI/Minimax Images 请求错误: {e}")
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
        size: str,
    ):
        logger.info(
            f"[BIG BANANA] OpenAI Images 正在请求 /images/edits，上传参考图 {len(image_b64_list)} 张"
        )
        is_xai = (
            "x.ai" in provider_config.api_url.lower()
            or "grok" in (provider_config.model or "").lower()
        )

        if is_xai:
            mime, b64_data = image_b64_list[0]
            data_url = f"data:{mime};base64,{b64_data}"
            json_payload = {
                "model": provider_config.model,
                "prompt": params.get("prompt", "anything"),
                "image": {"url": data_url, "type": "image_url"},
            }
            if "n" in params:
                json_payload["n"] = params["n"]
            if size and size != "auto":
                json_payload["size"] = size

            return await self.session.post(
                url=self._build_api_url(provider_config.api_url, "edits"),
                headers={**headers, "Content-Type": "application/json"},
                json=json_payload,
                timeout=self.def_common_config.timeout,
                proxy=self.def_common_config.proxy,
            )

        data = {
            "model": provider_config.model,
            "prompt": params.get("prompt", "anything"),
            "n": params.get("n", 1),
            "size": size,
        }
        moderation = params.get("moderation", self.def_prompt_config.moderation)
        model_lower = (provider_config.model or "").lower()
        is_gpt_image = any(k in model_lower for k in ["gpt-image", "chatgpt-image"])
        if moderation and (is_gpt_image or moderation == "low"):
            data["moderation"] = moderation
        multipart = CurlMime()
        for key, val in data.items():
            if val is not None:
                multipart.addpart(
                    name=key,
                    data=str(val).encode("utf-8"),
                )
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
                multipart=multipart,
                http_version="v1",
                timeout=self.def_common_config.timeout,
                proxy=self.def_common_config.proxy,
            )
        finally:
            multipart.close()

    @staticmethod
    def _build_api_url(api_url: str, endpoint: str, is_minimax: bool = False) -> str:
        if is_minimax or "minimax" in api_url.lower():
            from urllib.parse import urlparse

            try:
                parsed = urlparse(api_url)
                if parsed.netloc:
                    return f"{parsed.scheme}://{parsed.netloc}/v1/image_generation"
            except Exception:
                pass
            return "https://api.minimaxi.com/v1/image_generation"
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
            logger.warning(
                f"[BIG BANANA] OpenAI Images 输入图片归一化失败，将尝试使用原始字节: {e}"
            )
            if mime in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
                ext = mime.split("/")[-1].replace("jpg", "jpeg")
                return (
                    f"image_{index}.{ext}",
                    raw_bytes,
                    mime.replace("image/jpg", "image/jpeg"),
                )
            raise ValueError(
                "图片生成失败：存在不支持的输入图片格式，无法上传到 OpenAI Images API"
            )

    @staticmethod
    def _parse_images_response(
        result: dict,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        image_result: list[tuple[str, str]] = []
        data = result.get("data")
        # 处理 Minimax base64 响应: {"data": {"image_base64": ["..."]}}
        if isinstance(data, dict) and "image_base64" in data:
            for b64 in data["image_base64"]:
                if b64:
                    image_result.append(("image/png", b64))
        # 处理 OpenAI 标准 base64 响应: {"data": [{"b64_json": "..."}]}
        elif isinstance(data, list):
            for item in data:
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
        # Minimax 错误格式
        base_resp = result.get("base_resp")
        if isinstance(base_resp, dict):
            status_msg = base_resp.get("status_msg")
            if isinstance(status_msg, str):
                return status_msg[:200]
        return None
