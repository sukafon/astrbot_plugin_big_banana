import asyncio
import json
import random
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests.exceptions import Timeout

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource, ProviderCallResult
from .base import BaseProvider
from .utils import dedupe_images, parse_response_modalities

GRAPHQL_API_KEY = "AIzaSyCI-zsRP85UVOi0DjtiCwWBwQ1djDy741g"
GRAPHQL_QUERY_SIGNATURE = "2/l8eCsMMY49imcDQ/lwwXyL8cYtTjxZBF2dNqy69LodY="
STREAM_OPERATION_NAME = "StreamGenerateContentAnonymous"  # 这个修改无效
RECAPTCHA_SITE_KEY = "6LdCjtspAAAAAMcV4TGdWLJqRTEk1TfpdLqEnKdj"
RECAPTCHA_VERSION = "jdMmXeCQEkPbnFDy9T04NbgJ"
RECAPTCHA_CO = "aHR0cHM6Ly9jb25zb2xlLmNsb3VkLmdvb2dsZS5jb206NDQz"


class VertexAIAnonymousProvider(BaseProvider):
    """Vertex AI Anonymous 提供商"""

    provider_type = "Vertex_AI_Anonymous"

    async def initialize(self) -> None:
        """初始化匿名 Vertex AI 所需的 curl 会话上下文。"""
        self.session = self.plugin.http_manager.get_curl_session()
        self.timeout = self.plugin.common_config.timeout
        self.proxy = (
            self.plugin.common_config.proxy
            if self.provider_config.enable_proxy
            else None
        )
        raw_config = self.provider_config.raw_config
        # 防止负数
        self.max_refresh = max(0, int(raw_config.get("max_refresh", 5)))
        self.max_retry = max(0, int(raw_config.get("max_retry", 3)))
        self.retry_delay = raw_config.get("retry_delay", 1)
        self._body_context_cache: dict | None = None

    async def generate_images(self) -> GenerationResult:
        """图片生成"""
        # 获取 recaptcha_token
        recaptcha_token = await self._get_recaptcha_token()
        if recaptcha_token is None:
            return GenerationResult(error_message="获取 recaptcha_token 失败")

        # 构建请求体
        body = self._build_body_context()

        # 刷新 Token 次数统计
        token_refresh_count = 0
        # 当前 Token 验证失败尝试次数
        same_token_retry_count = 0
        # 当前 Token 是否为首次验证失败
        is_first_verify_failure = True

        while True:
            # 填充 recaptcha_token
            body["variables"]["recaptchaToken"] = recaptcha_token
            # 调用接口
            call_result = await self._call_vertex_api(body)

            # 如果成功获取到图片，直接返回
            if call_result.images:
                return GenerationResult(images=dedupe_images(call_result.images))

            # 未拿到有效上游状态码时不重试
            if call_result.status_code == 0:
                return GenerationResult(error_message=call_result.error_message)

            status = call_result.status_code
            err_msg = call_result.error_message

            # 5：通常是模型不存在、无权限等不可恢复错误，直接返回
            if status == 5:
                return GenerationResult(error_message=err_msg)

            # 判断是否为可重试的状态码 (3 或 8)
            if status in (3, 8):
                is_verify_failure = bool(
                    status == 3 and err_msg and "Failed to verify action" in err_msg
                )
                # 原地重试，不消耗重试次数
                if is_verify_failure and is_first_verify_failure:
                    # 每个 recaptchaToken 第一次返回 "Failed to verify action"，不消耗重试次数
                    is_first_verify_failure = False
                    # logger.warning(
                    #     f"[BIG BANANA] recaptcha_token 首次验证失败 (不消耗重试次数)："
                    #     f"{same_token_retry_count}/{self.max_retry}"
                    # )
                    if self.retry_delay > 0:
                        await asyncio.sleep(self.retry_delay)
                    continue

                # 往后无论是代码 3 还是代码 8，都消耗重试次数
                same_token_retry_count += 1
                logger.info(
                    f"[BIG BANANA] recaptcha_token 重试 (status={status}) 次数："
                    f"{same_token_retry_count}/{self.max_retry}"
                )
                if same_token_retry_count <= self.max_retry:
                    if self.retry_delay > 0:
                        await asyncio.sleep(self.retry_delay)
                    continue

                # 当同一 Token 的重试次数超过 max_retry，尝试刷新 recaptchaToken
                if token_refresh_count >= self.max_refresh:
                    logger.warning("[BIG BANANA] recaptcha_token 刷新次数达到上限")
                    return GenerationResult(error_message=err_msg)

                token_refresh_count += 1
                logger.info(
                    f"[BIG BANANA] recaptcha_token 重试次数达到上限，正在刷新..."
                    f"({token_refresh_count}/{self.max_refresh})"
                )
                if self.retry_delay > 0:
                    await asyncio.sleep(self.retry_delay)

                recaptcha_token = await self._get_recaptcha_token()
                if recaptcha_token is None:
                    logger.error("[BIG BANANA] 获取 recaptcha_token 失败次数达到上限")
                    return GenerationResult(error_message="获取 recaptcha_token 失败")

                same_token_retry_count = 0
                is_first_verify_failure = True
                continue

            # 其他未单独处理的状态码直接返回
            return GenerationResult(error_message=err_msg or "图片生成失败")

    async def _call_vertex_api(self, body: dict) -> ProviderCallResult:
        """调用匿名 Vertex AI GraphQL 接口并解析图片结果。"""
        response = None
        response_text = ""
        try:
            response = await self.session.post(
                url=self._build_api_url(),
                headers={
                    "referer": "https://console.cloud.google.com/",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout,
                impersonate="chrome131",
                proxy=self.proxy,
            )
            response_text = response.text
            result = json.loads(response_text)
            if response.status_code != 200:
                err_msg = "未知原因"
                if isinstance(result, dict):
                    err_msg = result.get("error", {}).get("message", "未知原因")
                logger.error(
                    f"[BIG BANANA] 图片生成失败，状态码: {response.status_code}，"
                    f"原因: {err_msg}"
                )
                return ProviderCallResult(
                    status_code=response.status_code,
                    error_message=err_msg,
                )

            image_sources, status_code, reason = self._extract_result(result)
            images: list[ImageResource] = []
            for source in image_sources:
                image = await self.plugin.downloader.fetch_base64_image(
                    source,
                    convert=True,
                    allow_gif=True,
                )
                if image:
                    images.append(image)
            if images:
                return ProviderCallResult(images=images, status_code=200)

            return self._missing_image_result(
                reason=reason,
                response_text=response_text,
                status_code=status_code or 0,
            )
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {e}")
            return ProviderCallResult(
                status_code=408,
                error_message="图片生成失败：响应超时",
            )
        except json.JSONDecodeError as e:
            status_code = response.status_code if response is not None else 0
            resp_text = response_text[:1024] or "未知"
            logger.error(
                f"[BIG BANANA] JSON反序列化错误: {e}，状态码："
                f"{status_code}，响应内容：{resp_text}"
            )
            return ProviderCallResult(
                status_code=status_code,
                error_message="图片生成失败：响应内容格式错误",
            )
        except Exception as e:
            logger.error(f"[BIG BANANA] 程序错误: {e}")
            return ProviderCallResult(
                error_message="图片生成失败：程序错误",
            )

    def _build_body_context(self) -> dict:
        """构建请求体"""
        if self._body_context_cache is not None:
            return self._body_context_cache

        parts = []
        for image in self.image_list:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": image.mime,
                        "data": image.base64,
                    }
                }
            )

        response_modalities = parse_response_modalities(
            self.provider_config.raw_config.get("response_modalities", '["IMAGE"]')
        )

        context: dict[str, Any] = {
            "model": self.provider_config.model,
            "contents": [
                {
                    "parts": [
                        {"text": self.params.get("prompt", "draw a picture")},
                        *parts,
                    ],
                    "role": "user",
                }
            ],
            "generationConfig": {
                "temperature": 1,
                "topP": 0.95,
                "maxOutputTokens": 32768,
                "imageConfig": {
                    "imageOutputOptions": {"mimeType": "image/png"},  # 这个修改似乎无效
                    "personGeneration": "ALLOW_ALL",
                },
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
            ],
            "region": "global",
        }

        if response_modalities:
            context["generationConfig"]["responseModalities"] = response_modalities

        image_config = context["generationConfig"]["imageConfig"]
        aspect_ratio = self.params.get(
            "aspect_ratio", self.plugin.params_config.aspect_ratio
        )
        if aspect_ratio != "default":
            image_config["aspectRatio"] = aspect_ratio

        system_prompt = self.provider_config.raw_config["system_prompt"]
        if system_prompt:
            context["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        if "gemini-3" in self.provider_config.model.lower():
            if self.params.get(
                "google_search", self.plugin.params_config.google_search
            ):
                context["tools"] = [{"googleSearch": {}}]
            image_size = self.params.get(
                "image_size", self.plugin.params_config.image_size
            )
            image_config["imageSize"] = image_size

        self._body_context_cache = {
            "querySignature": GRAPHQL_QUERY_SIGNATURE,
            "operationName": STREAM_OPERATION_NAME,
            "variables": context,
        }
        return self._body_context_cache

    def _extract_result(
        self, result: list[dict]
    ) -> tuple[list[str], int | None, str | None]:
        """解析响应中的图片和失败原因"""
        image_sources: list[str] = []
        text_parts: list[str] = []
        for elem in result:
            for item in elem.get("results", []):
                for err in item.get("errors", []):
                    return self._extract_error(err)

                for candidate in item.get("data", {}).get("candidates", []):
                    finish_reason = candidate.get("finishReason", "")
                    if finish_reason and finish_reason != "STOP":
                        logger.warning(
                            f"[BIG BANANA] 图片生成失败, 响应内容: {str(result)[:1024]}"
                        )
                        return (
                            image_sources,
                            None,
                            f"图片生成失败，原因: {finish_reason}",
                        )
                    for part in candidate.get("content", {}).get("parts", []):
                        text = part.get("text")
                        if text:
                            text_parts.append(text)
                        data_base64 = part.get("inlineData", {}).get("data")
                        if data_base64:
                            image_sources.append(data_base64)
        if text_parts:
            self.text_response_parts.extend(text_parts)
        return image_sources, None, None

    def _extract_error(self, err: dict) -> tuple[list[str], int | None, str | None]:
        """解析错误"""
        status = err.get("extensions", {}).get("status", {}).get("code")
        err_msg = err.get("message", "")
        logger.debug(
            f"[BIG BANANA] 图片生成失败，错误代码：{status}，错误原因：{err_msg}"
        )
        return [], status, err_msg

    def _missing_image_result(
        self,
        reason: str | None = None,
        *,
        response_text: str = "",
        status_code: int = 200,
    ) -> ProviderCallResult:
        logger.debug(
            f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {response_text[:1024] or '无'}"
        )
        message = reason or "响应中未包含图片数据"
        if (
            not reason
            and self.plugin.preference_config.send_text_when_no_image
            and self.text_response_parts
        ):
            message = "".join(self.text_response_parts).strip() or message
        return ProviderCallResult(
            status_code=status_code,
            error_message=message,
        )

    def _build_api_url(self) -> str:
        """构建接口地址"""
        base_url = (
            self.provider_config.base_url
            or "https://cloudconsole-pa.clients6.google.com"
        ).rstrip("/")
        return (
            f"{base_url}/v3/entityServices/AiplatformEntityService/schemas/"
            f"AIPLATFORM_GRAPHQL:batchGraphql?key={GRAPHQL_API_KEY}&prettyPrint=false"
        )

    async def _get_recaptcha_token(self) -> str | None:
        """尝试获取可用的 reCAPTCHA 令牌"""
        recaptcha_base_api = self.provider_config.raw_config[
            "recaptcha_base_api"
        ].rstrip("/")
        for _ in range(3):
            random_cb = random_string(10)
            anchor_url = (
                f"{recaptcha_base_api}/recaptcha/enterprise/anchor?ar=1"
                f"&k={RECAPTCHA_SITE_KEY}&co={RECAPTCHA_CO}&hl=zh-CN"
                f"&v={RECAPTCHA_VERSION}&size=invisible"
                f"&anchor-ms=20000&execute-ms=15000&cb={random_cb}"
            )
            reload_url = (
                f"{recaptcha_base_api}/recaptcha/enterprise/reload"
                f"?k={RECAPTCHA_SITE_KEY}"
            )
            recaptcha_token = await self._execute_recaptcha(anchor_url, reload_url)
            if recaptcha_token:
                logger.info("[BIG BANANA] 获取 recaptcha_token 成功")
                return recaptcha_token
            logger.warning("[BIG BANANA] 获取 recaptcha_token 失败，重试中...")
        return None

    async def _execute_recaptcha(self, anchor_url: str, reload_url: str) -> str | None:
        """执行 anchor/reload 流程解析最终 reCAPTCHA 响应"""
        anchor_html = await self.session.get(
            anchor_url,
            impersonate="chrome131",
            proxy=self.proxy,
            timeout=self.timeout,
        )
        soup = BeautifulSoup(anchor_html.text, "html.parser")
        token_element = soup.find("input", {"id": "recaptcha-token"})
        if token_element is None:
            logger.error("[BIG BANANA] anchor_html 未找到 recaptcha-token 元素")
            return None
        base_recaptcha_token = str(token_element.get("value"))

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
            "bg": "",
        }
        reload_response = await self.session.post(
            reload_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome131",
            proxy=self.proxy,
            timeout=self.timeout,
        )

        match = re.search(r'rresp","(.*?)"', reload_response.text)
        if not match:
            logger.error("[BIG BANANA] 未找到 rresp")
            return None
        return match.group(1)


def random_string(length: int) -> str:
    """生成指定长度的随机小写字母数字字符串。"""
    return "".join(
        random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(length)
    )
