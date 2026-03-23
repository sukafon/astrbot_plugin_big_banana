import json
import re

from curl_cffi.requests.exceptions import Timeout

from astrbot.api import logger

from .base import BaseProvider
from .data import ProviderConfig


class OpenAIChatProvider(BaseProvider):
    """OpenAI Chat 提供商"""

    api_type: str = "OpenAI_Chat"

    async def _call_api(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        """发起 OpenAI 图片生成请求
        返回值: 元组(图片 base64 列表, 状态码, 人类可读的错误信息)
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # 构建请求上下文
        openai_context = self._build_openai_chat_context(
            provider_config.model, image_b64_list, params
        )
        try:
            # 发送请求
            response = await self.session.post(
                url=provider_config.api_url,
                headers=headers,
                json=openai_context,
                timeout=self.def_common_config.timeout,
                proxy=self.def_common_config.proxy,
            )
            # 响应反序列化
            result = response.json()
            if response.status_code == 200:
                b64_images = []
                images_url = []
                finish_reason = None
                for item in result.get("choices", []):
                    content = item.get("message", {}).get("content", "")
                    match = re.search(r"!\[.*?\]\((.*?)\)", content)
                    finish_reason = item.get("finish_reason", "")[:100]
                    if match:
                        img_src = match.group(1)
                        if img_src.startswith("data:image/"):  # base64
                            header, base64_data = img_src.split(",", 1)
                            mime = header.split(";")[0].replace("data:", "")
                            b64_images.append((mime, base64_data))
                        else:  # URL
                            images_url.append(img_src)
                # 最后再检查是否有图片数据
                if not images_url and not b64_images:
                    logger.warning(
                        f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}"
                    )
                    return None, 200, finish_reason or "响应中未包含图片数据"
                # 下载图片并转换为 base64
                b64_images += await self.downloader.fetch_images(images_url)
                if not b64_images:
                    return None, 200, "图片下载或者处理失败"
                return b64_images, 200, None
            else:
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}"
                )
                return (
                    None,
                    response.status_code,
                    f"图片生成失败: 状态码 {response.status_code}",
                )
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except json.JSONDecodeError as e:
            logger.error(
                f"[BIG BANANA] JSON反序列化错误: {e}，状态码：{response.status_code}，响应内容：{response.text[:1024]}"
            )
            return None, response.status_code, "图片生成失败：响应内容格式错误"
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
        """发起 OpenAI 图片生成流式请求
        返回值: 元组(图片 base64 列表, 状态码, 人类可读的错误信息)
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # 构建请求上下文
        openai_context = self._build_openai_chat_context(
            provider_config.model, image_b64_list, params
        )
        try:
            # 发送请求
            response = await self.session.post(
                url=provider_config.api_url,
                headers=headers,
                json=openai_context,
                proxy=self.def_common_config.proxy,
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
                images_url = []
                reasoning_content = ""
                for line in result.splitlines():
                    if line.startswith("data: "):
                        line_data = line[len("data: ") :].strip()
                        if line_data == "[DONE]":
                            break
                        try:
                            json_data = json.loads(line_data)
                            # 遍历 json_data，检查是否有图片
                            for item in json_data.get("choices", []):
                                content = item.get("delta", {}).get("content", "")
                                match = re.search(r"!\[.*?\]\((.*?)\)", content)
                                if match:
                                    img_src = match.group(1)
                                    if img_src.startswith("data:image/"):  # base64
                                        header, base64_data = img_src.split(",", 1)
                                        mime = header.split(";")[0].replace("data:", "")
                                        b64_images.append((mime, base64_data))
                                    else:  # URL
                                        images_url.append(img_src)
                                else:  # 尝试查找失败的原因或者纯文本返回结果
                                    reasoning_content += item.get("delta", {}).get(
                                        "reasoning_content", ""
                                    )
                        except json.JSONDecodeError:
                            continue
                if not images_url and not b64_images:
                    logger.warning(
                        f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {result[:1024]}"
                    )
                    return None, 200, reasoning_content or "响应中未包含图片数据"
                # 下载图片并转换为 base64（有时会出现连接被重置的错误，不知道什么原因，国外服务器也一样）
                b64_images += await self.downloader.fetch_images(images_url)
                if not b64_images:
                    return None, 200, "图片下载或者处理失败"
                return b64_images, 200, None
            else:
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}, 响应内容: {result[:1024]}"
                )
                return None, response.status_code, "响应中未包含图片数据"
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return None, 408, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"[BIG BANANA] 请求错误: {e}")
            return None, None, "图片生成失败：程序错误"

    def _build_openai_chat_context(
        self,
        model: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> dict:
        images_content = []
        for mime, b64 in image_b64_list:
            images_content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )
        context = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": params.get("prompt", "anything")},
                        *images_content,
                    ],
                }
            ],
            "stream": params.get("stream", False),
        }
        return context
