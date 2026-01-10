import json
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import Timeout

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig

from .base import BaseProvider
from .data import CommonConfig, PromptConfig, ProviderConfig, VertexAIAnonymousConfig
from .downloader import Downloader
from .utils import random_string


class VertexAIAnonymousProvider(BaseProvider):
    """Vertex AI Anonymous 提供商"""

    api_type: str = "Vertex_AI_Anonymous"

    def __init__(
        self,
        config: AstrBotConfig,
        common_config: CommonConfig,
        prompt_config: PromptConfig,
        session: AsyncSession,
        downloader: Downloader,
    ):
        super().__init__(
            config,
            common_config,
            prompt_config,
            session,
            downloader=downloader,
        )
        self.vertex_ai_anonymous_config = VertexAIAnonymousConfig(
            **config.get("vertex_ai_anonymous_config", {})
        )

    async def generate_images(
        self,
        provider_config: ProviderConfig,
        params: dict,
        image_b64_list: list[tuple[str, str]] | None = None,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        body = self._build_vertex_ai_body(
            provider_config.model, params["prompt"], image_b64_list, params
        )
        recaptcha_token = await self._get_recaptcha_token()
        if recaptcha_token is None:
            return None, "获取 recaptcha_token 失败"
        err_msg = None
        for _ in range(self.vertex_ai_anonymous_config.max_retry):
            body["variables"]["recaptchaToken"] = recaptcha_token
            result, status, err_msg = await self._call_api(body)
            if result is not None:
                return result, None
            # 8:资源耗尽；3:Token失效/参数错误
            if status == 3:
                recaptcha_token = await self._get_recaptcha_token()
                if recaptcha_token is None:
                    logger.error("[BIG BANANA] 获取 recaptcha_token 失败次数达到上限")
                    return None, "获取 recaptcha_token 失败"
            if status == 999:
                # 这个提供商一但出现内容拦截，重试几乎没有意义，直接返回错误
                return None, err_msg
            logger.warning(
                f"[BIG BANANA] 图片生成失败，正在重试 Vertex AI Anonymous API ({_ + 1}/ {self.vertex_ai_anonymous_config.max_retry})"
            )
        else:
            return None, err_msg or "图片生成失败：重试达到上限。"

    async def _call_api(
        self, body: dict
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        headers = {
            "referer": "https://console.cloud.google.com/",
            "Content-Type": "application/json",
        }
        try:
            response = await self.session.post(
                url=f"{self.vertex_ai_anonymous_config.vertex_ai_anonymous_base_api}/v3/entityServices/AiplatformEntityService/schemas/AIPLATFORM_GRAPHQL:batchGraphql?key=AIzaSyCI-zsRP85UVOi0DjtiCwWBwQ1djDy741g&prettyPrint=false",
                headers=headers,
                json=body,
                timeout=self.def_common_config.timeout,
                impersonate="chrome131",
                proxy=self.def_common_config.proxy,
            )
            result = response.json()
            if response.status_code == 200:
                b64_images = []
                # 遍历每一个元素
                for elem in result:
                    # 从每一个元素中查找图片数据
                    for item in elem.get("results", []):
                        # 先检查错误
                        errors = item.get("errors", [])
                        for err in errors:
                            status = (
                                err.get("extensions", {})
                                .get("status", {})
                                .get("code", None)
                            )
                            err_msg = err.get("message", "")
                            logger.error(
                                f"[BIG BANANA] 图片生成失败，错误代码：{status}，错误原因：{err_msg}"
                            )
                            return None, status, err_msg
                        # 没有错误，应该是正常响应
                        for candidate in item.get("data", {}).get("candidates", []):
                            # 检查 finishReason 状态
                            finishReason = candidate.get("finishReason", "")
                            if finishReason == "STOP":
                                parts = candidate.get("content", {}).get("parts", [])
                                for part in parts:
                                    if "inlineData" in part and part["inlineData"].get(
                                        "data"
                                    ):
                                        data = part["inlineData"]
                                        b64_images.append(
                                            (data["mimeType"], data["data"])
                                        )
                            else:
                                logger.warning(
                                    f"[BIG BANANA] 图片生成失败, 响应内容: {response.text[:1024]}"
                                )
                                return None, 999, f"图片生成失败，原因: {finishReason}"
                # 最后再检查是否有图片数据
                if not b64_images:
                    logger.warning(
                        f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}"
                    )
                    return None, 999, "响应中未包含图片数据"
                return b64_images, None, None
            else:
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}"
                )
                return (
                    None,
                    None,
                    f"图片生成失败：状态码: {response.status_code}",
                )
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return None, None, "图片生成失败：响应超时"
        except json.JSONDecodeError as e:
            logger.error(
                f"[BIG BANANA] JSON反序列化错误: {e}，状态码：{response.status_code}，响应内容：{response.text[:1024]}"
            )
            return None, None, "图片生成失败：响应内容格式错误"
        except Exception as e:
            logger.error(f"[BIG BANANA] 程序错误: {e}")
            return None, None, "图片生成失败：程序错误"

    def _build_vertex_ai_body(
        self,
        model: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]] | None,
        params: dict,
    ) -> dict:
        # 处理图片内容部分
        parts = []
        if image_b64_list:
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
            "model": model,
            "contents": [{"parts": [{"text": prompt}, *parts], "role": "user"}],
            "generationConfig": {
                "temperature": 1,
                "topP": 0.95,
                "maxOutputTokens": 32768,
                "responseModalities": responseModalities,
                "imageConfig": {
                    "imageOutputOptions": {"mimeType": "image/png"},  # 这个修改是无效的
                    "personGeneration": "ALLOW_ALL",
                },
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
            "region": "global",
        }

        # 处理图片宽高比参数
        aspect_ratio = params.get("aspect_ratio", self.def_prompt_config.aspect_ratio)
        if aspect_ratio != "default":
            context["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}

        # 处理系统提示词
        if self.vertex_ai_anonymous_config.system_prompt:
            context["systemInstruction"] = {
                "parts": [{"text": self.vertex_ai_anonymous_config.system_prompt}]
            }

        # 以下参数仅 Gemini-3-Pro-Image-Preview 模型有效
        if "gemini-3" in model.lower():
            # 处理工具类
            if params.get("google_search", self.def_prompt_config.google_search):
                context["tools"] = [{"googleSearch": {}}]
            # 处理图片分辨率参数
            image_size = params.get("image_size", self.def_prompt_config.image_size)
            context["generationConfig"]["imageConfig"] = {"imageSize": image_size}

        # 组装完整Body
        body = {
            "querySignature": "2/l8eCsMMY49imcDQ/lwwXyL8cYtTjxZBF2dNqy69LodY=",
            "operationName": "StreamGenerateContentAnonymous",
            "variables": context,
        }

        return body

    async def _get_recaptcha_token(self) -> str | None:
        for _ in range(3):
            random_cb = random_string(10)
            anchor_url = f"{self.vertex_ai_anonymous_config.recaptcha_base_api}/recaptcha/enterprise/anchor?ar=1&k=6LdCjtspAAAAAMcV4TGdWLJqRTEk1TfpdLqEnKdj&co=aHR0cHM6Ly9jb25zb2xlLmNsb3VkLmdvb2dsZS5jb206NDQz&hl=zh-CN&v=jdMmXeCQEkPbnFDy9T04NbgJ&size=invisible&anchor-ms=20000&execute-ms=15000&cb={random_cb}"
            reload_url = f"{self.vertex_ai_anonymous_config.recaptcha_base_api}/recaptcha/enterprise/reload?k=6LdCjtspAAAAAMcV4TGdWLJqRTEk1TfpdLqEnKdj"
            recaptcha_token = await self._execute_recaptcha(anchor_url, reload_url)
            if recaptcha_token:
                logger.info("[BIG BANANA] 获取 recaptcha_token 成功")
                return recaptcha_token
            logger.warning("[BIG BANANA] 获取 recaptcha_token 失败，重试中...")
        return None

    async def _execute_recaptcha(self, anchor_url: str, reload_url: str) -> str | None:
        # 获取初始化recaptcha_token
        anchor_html = await self.session.get(
            anchor_url, impersonate="chrome131", proxy=self.def_common_config.proxy
        )
        soup = BeautifulSoup(anchor_html.text, "html.parser")
        token_element = soup.find("input", {"id": "recaptcha-token"})
        if token_element is None:
            logger.error("[BIG BANANA] anchor_html 未找到 recaptcha-token 元素")
            return None
        base_recaptcha_token = str(token_element.get("value"))
        # 发送reload请求获取最终token
        parsed = urlparse(anchor_url)
        params = parse_qs(parsed.query)
        payload = {
            "v": params["v"][0],
            "reason": "q",
            "k": params["k"][0],
            "c": base_recaptcha_token,
            "co": params["co"][0],
            "hl": params["hl"][0],
            "size": "invisible",
            "vh": "6581054572",
            "chr": "",
            "bg": "",  # 这个太长了，而且好像不需要
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        reload_response = await self.session.post(
            reload_url,
            data=payload,
            headers=headers,
            impersonate="chrome131",
            proxy=self.def_common_config.proxy,
        )
        # 解析响应内容
        match = re.search(r'rresp","(.*?)"', reload_response.text)
        if not match:
            logger.error("[BIG BANANA] 未找到 rresp")
            return None
        recaptcha_token = match.group(1)
        return recaptcha_token

    # 方法重定向
    async def _call_stream_api(
        self, **kwargs
    ) -> tuple[list[tuple[str, str]] | None, int | None, str | None]:
        return await self._call_api(**kwargs)
