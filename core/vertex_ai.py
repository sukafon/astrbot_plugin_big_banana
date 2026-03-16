import json
from curl_cffi.requests.exceptions import Timeout
from astrbot.api import logger
from .base import BaseProvider
from .data import ProviderConfig

class VertexAIProvider(BaseProvider):
    """Vertex AI 提供商"""

    api_type: str = "Vertex_AI"

    async def _call_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        """发起 Vertex AI 图片生成请求"""
        headers = {
            "Content-Type": "application/json",
        }

        url = f"{provider_config.api_url}/{provider_config.model}:generateContent?key={api_key}"

        # 构建请求上下文
        vertex_context = self._build_vertex_context(
            provider_config.model,
            image_b64_list,
            params,
        )
        try:
            response = await self.session.post(
                url,
                headers=headers,
                json=vertex_context,
                proxy=self.def_common_config.proxy,
                timeout=self.def_common_config.timeout,
            )
            result = response.json()
            if response.status_code == 200:
                b64_images = []
                for item in result.get("candidates", []):
                    finishReason = item.get("finishReason", "")
                    if finishReason == "STOP":
                        parts = item.get("content", {}).get("parts", [])
                        for part in parts:
                            if "inlineData" in part and "data" in part["inlineData"]:
                                data = part["inlineData"]
                                b64_images.append((data["mimeType"], data["data"]))
                    else:
                        logger.warning(f"[BIG BANANA] Vertex AI 生成失败, 原因: {finishReason}")
                        return None, 200, f"图片生成失败，原因: {finishReason}"
                
                if not b64_images:
                    return None, 200, "响应中未包含图片数据"
                return b64_images, 200, None
            else:
                logger.error(f"[BIG BANANA] Vertex AI 请求失败，状态码: {response.status_code}")
                err_msg = result.get("error", {}).get("message", "未知原因")
                return None, response.status_code, f"图片生成失败：{err_msg}"
        except Timeout as e:
            logger.error(f"[BIG BANANA] Vertex AI 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"[BIG BANANA] Vertex AI 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    async def _call_stream_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        """发起 Vertex AI 流式请求"""
        headers = {
            "Content-Type": "application/json",
        }
        # 流式请求 URL 拼接
        url = f"{provider_config.api_url}/{provider_config.model}:streamGenerateContent?alt=sse&key={api_key}"
        
        vertex_context = self._build_vertex_context(
            model=provider_config.model, image_b64_list=image_b64_list, params=params
        )
        try:
            response = await self.session.post(
                url,
                headers=headers,
                json=vertex_context,
                proxy=self.def_common_config.proxy,
                timeout=self.def_common_config.timeout,
                stream=True,
            )
            streams = response.aiter_content(chunk_size=1024)
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
                            for item in json_data.get("candidates", []):
                                parts = item.get("content", {}).get("parts", [])
                                for part in parts:
                                    if "inlineData" in part and "data" in part["inlineData"]:
                                        data = part["inlineData"]
                                        b64_images.append((data["mimeType"], data["data"]))
                        except json.JSONDecodeError:
                            continue
                if not b64_images:
                    return None, 200, "响应中未包含图片数据"
                return b64_images, 200, None
            else:
                logger.error(f"[BIG BANANA] Vertex AI 流式失败，状态码: {response.status_code}")
                return None, response.status_code, f"失败状态码 {response.status_code}"
        except Timeout as e:
            return None, 408, "图片生成失败：响应超时"
        except Exception as e:
            return None, None, "图片生成失败：程序错误"

    def _build_vertex_context(
        self,
        model: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> dict:
        """构建发送给 Vertex AI 的数据负载"""
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

        context = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": params.get("prompt", "anything")}, *parts],
                }
            ]
        }
        return context