import json

from curl_cffi.requests.exceptions import Timeout

from astrbot.api import logger

from .base import BaseProvider
from .data import ProviderConfig


class GeminiProvider(BaseProvider):
    """Gemini 提供商"""

    api_type: str = "Gemini"

    async def _call_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        """发起 Gemini 图片生成请求
        返回值: 元组(图片 base64 列表, 状态码, 人类可读的错误信息)
        """
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        url = f"{provider_config.api_url}/{provider_config.model}:generateContent"
        # 构建请求上下文
        gemini_context = self._build_gemini_context(
            provider_config.model,
            image_b64_list,
            params,
        )
        try:
            response = await self.session.post(
                url,
                headers=headers,
                json=gemini_context,
                proxy=self.def_common_config.proxy,
                timeout=self.def_common_config.timeout,
            )
            # 响应反序列化
            result = response.json()
            if response.status_code == 200:
                b64_images = []
                for item in result.get("candidates", []):
                    # 检查 finishReason 状态
                    finishReason = item.get("finishReason", "")
                    if finishReason == "STOP":
                        parts = item.get("content", {}).get("parts", [])
                        for part in parts:
                            if "inlineData" in part and "data" in part["inlineData"]:
                                data = part["inlineData"]
                                b64_images.append((data["mimeType"], data["data"]))
                    else:
                        logger.warning(
                            f"[BIG BANANA] 图片生成失败, 响应内容: {response.text[:1024]}"
                        )
                        return None, 200, f"图片生成失败，原因: {finishReason}"
                # 最后再检查是否有图片数据
                if not b64_images:
                    logger.warning(
                        f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}"
                    )
                    if result.get("promptFeedback", {}):
                        return (
                            None,
                            200,
                            f"请求被内容安全系统拦截，原因：{result.get('promptFeedback', {}).get('blockReason', '未获取到原因')}",
                        )
                    return None, 200, "响应中未包含图片数据"
                return b64_images, 200, None
            else:
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}"
                )
                err_msg = result.get("error", {}).get("message", "未知原因")
                return None, response.status_code, f"图片生成失败：{err_msg}"
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except json.JSONDecodeError as e:
            logger.error(
                f"[BIG BANANA] JSON反序列化错误: {e}，状态码：{response.status_code}，响应内容：{response.text[:1024]}"
            )
            return None, response.status_code, "图片生成失败：响应内容错误"
        except Exception as e:
            logger.error(f"[BIG BANANA] 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    async def _call_stream_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        """发起 Gemini 图片生成流式请求
        返回值: 元组(图片 base64 列表, 状态码, 人类可读的错误信息)
        """
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        url = f"{provider_config.api_url}/{provider_config.model}:streamGenerateContent?alt=sse"
        # 构建请求上下文
        gemini_context = self._build_gemini_context(
            model=provider_config.model, image_b64_list=image_b64_list, params=params
        )
        try:
            response = await self.session.post(
                url,
                headers=headers,
                json=gemini_context,
                proxy=self.def_common_config.proxy,
                timeout=self.def_common_config.timeout,
                stream=True,
            )
            # 处理流式响应
            streams = response.aiter_content(chunk_size=1024)
            # 读取完整内容
            data = b""
            async for chunk in streams:
                data += chunk
            result = data.decode("utf-8")
            if response.status_code == 200:
                b64_images = []
                for line in result.splitlines():
                    if line.startswith("data: "):
                        line_data = line[len("data: ") :].strip()
                        if line_data == "[DONE]":
                            break
                        try:
                            json_data = json.loads(line_data)
                            # 遍历 candidates，检查是否有图片数据
                            for item in json_data.get("candidates", []):
                                parts = item.get("content", {}).get("parts", [])
                                for part in parts:
                                    if (
                                        "inlineData" in part
                                        and "data" in part["inlineData"]
                                    ):
                                        data = part["inlineData"]
                                        b64_images.append(
                                            (data["mimeType"], data["data"])
                                        )
                        except json.JSONDecodeError:
                            continue
                if not b64_images:
                    logger.warning(
                        f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {result[:1024]}"
                    )
                    return None, 200, "响应中未包含图片数据"
                return b64_images, 200, None
            else:
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}, 响应内容: {result[:1024]}"
                )
                return None, response.status_code, f"图片生成失败：状态码 {response.status_code}"
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"[BIG BANANA] 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    def _build_gemini_context(
        self,
        model: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> dict:
        # 处理图片内容部分
        parts = []
        for mime, b64 in image_b64_list:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime,
                        "data": b64,
                    }
                }
            )

        # 处理响应内容的类型
        responseModalities = ["IMAGE"]
        if self.def_common_config.text_response:
            responseModalities.insert(0, "TEXT")

        # 构建请求上下文
        context = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": params.get("prompt", "anything")}, *parts],
                }
            ],
            "generationConfig": {
                "responseModalities": responseModalities,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "OFF",
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "OFF",
                },
            ],
        }

        # 处理图片宽高比参数
        aspect_ratio = params.get("aspect_ratio", self.def_prompt_config.aspect_ratio)
        if aspect_ratio != "default":
            context["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}

        # 以下参数仅 Gemini-3-Pro-Image-Preview 模型有效
        if "gemini-3" in model.lower():
            # 处理工具类
            if params.get("google_search", self.def_prompt_config.google_search):
                context["tools"] = [{"google_search": {}}]
            # 处理图片分辨率参数
            image_size = params.get("image_size", self.def_prompt_config.image_size)
            context["generationConfig"]["imageConfig"] = {"imageSize": image_size}

        return context
