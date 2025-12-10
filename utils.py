import base64
import json
import re
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from curl_cffi import AsyncSession, ProxySpec, requests
from curl_cffi.requests.exceptions import Timeout
from PIL import Image

from astrbot.api import logger


class Utils:
    def __init__(
        self,
        retry_config: dict,
        def_params: dict,
        proxy: str,
        vertex_ai_anonymous_config: dict,
    ):
        # 初始化异步HTTP会话
        self.proxies: ProxySpec | None = (
            {"http": proxy, "https": proxy} if proxy else None
        )
        self.timeout = retry_config.get("timeout", 300)
        self.session = AsyncSession(timeout=self.timeout)

        # 默认参数
        self.image_size = def_params.get("image_size", "1K")
        self.aspect_ratio = def_params.get("aspect_ratio", "default")
        self.google_search = def_params.get("google_search", False)
        self.text_response = def_params.get("text_response", False)
        self.max_retry = retry_config.get("max_retry", 2)

        # Vertex AI Anonymous配置
        self.recaptcha_base_api = vertex_ai_anonymous_config.get(
            "recaptcha_base_api", "https://www.google.com"
        )
        self.vertex_ai_anonymous_base_api = vertex_ai_anonymous_config.get(
            "vertex_ai_anonymous_base_api",
            "https://cloudconsole-pa.clients6.google.com",
        )
        self.vertex_ai_anonymous_max_retry = vertex_ai_anonymous_config.get(
            "max_retry", 5
        )
        # 默认Key是公开的，并非密钥泄露问题
        self.vertex_ai_anonymous_api_key = vertex_ai_anonymous_config.get(
            "api_key", "AIzaSyCI-zsRP85UVOi0DjtiCwWBwQ1djDy741g"
        )

    def _handle_image(self, image_bytes: bytes) -> tuple[str, str]:
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                fmt = (img.format or "").upper()
                # 如果不是 GIF，直接返回原图
                if fmt != "GIF":
                    mime = f"image/{fmt.lower()}"
                    b64 = base64.b64encode(image_bytes).decode("utf-8")
                    return (mime, b64)
                # 处理 GIF
                buf = BytesIO()
                # 取第一帧
                img.seek(0)
                img = img.convert("RGBA")
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return ("image/png", b64)
        except Exception as e:
            logger.warning(f"GIF 处理失败，返回原图: {e}")
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            return ("image/gif", b64)

    async def _download_image(
        self, url: str
    ) -> tuple[tuple[str, str] | None, str | None]:
        try:
            response = await self.session.get(url, timeout=30)
            content = self._handle_image(response.content)
            return content, None
        except (
            requests.exceptions.SSLError,
            requests.exceptions.CertificateVerifyError,
        ):
            # 关闭SSL验证
            response = await self.session.get(url, timeout=30, verify=False)
            content = self._handle_image(response.content)
            return content, None
        except Timeout as e:
            logger.error(f"网络请求超时: {url}\n{e}")
            return None, "下载图片失败：请求超时"
        except Exception as e:
            logger.error(f"下载图片失败: {url}\n{e}")
            return None, "下载图片失败"

    async def fetch_images(self, image_urls: list[str]) -> list[tuple[str, str]]:
        image_b64_list = []
        for url in image_urls:
            content = None
            error = None
            # 增加重试逻辑
            for _ in range(self.max_retry):
                content, error = await self._download_image(url)
                if not error and content is not None:
                    break  # 成功就跳出循环
            if error or content is None:
                continue
            image_b64_list.append(content)
        return image_b64_list

    def _build_gemini_context(
        self,
        model: str,
        prompt: str,
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
        if params.get("text_response", self.text_response):
            responseModalities.insert(0, "TEXT")

        # 构建请求上下文
        context = {
            "contents": [{"parts": [{"text": prompt}, *parts]}],
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
        aspect_ratio = params.get("aspect_ratio", self.aspect_ratio)
        if aspect_ratio != "default":
            context["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}

        # 以下参数仅 Gemini-3-Pro-Image-Preview 模型有效
        if "gemini-3" in model or "Gemini-3" in model:
            # 处理工具类
            if params.get("google_search", self.google_search):
                context["tools"] = [{"google_search": {}}]
            # 处理图片分辨率参数
            image_size = params.get("image_size", self.image_size)
            context["generationConfig"]["imageConfig"] = {"imageSize": image_size}

        return context

    def _build_vertex_ai_body(
        self,
        model: str,
        prompt: str,
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
        if params.get("text_response", self.text_response):
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
                    "imageOutputOptions": {"mimeType": "image/png"},
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
        aspect_ratio = params.get("aspect_ratio", self.aspect_ratio)
        if aspect_ratio != "default":
            context["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}

        # 以下参数仅 Gemini-3-Pro-Image-Preview 模型有效
        if "gemini-3" in model or "Gemini-3" in model:
            # 处理工具类
            if params.get("google_search", self.google_search):
                context["tools"] = [{"googleSearch": {}}]
            # 处理图片分辨率参数
            image_size = params.get("image_size", self.image_size)
            context["generationConfig"]["imageConfig"] = {"imageSize": image_size}

        body = {
            "querySignature": "2/l8eCsMMY49imcDQ/lwwXyL8cYtTjxZBF2dNqy69LodY=",
            "operationName": "StreamGenerateContentAnonymous",
            "variables": context,
        }

        return body

    async def _call_gemini_stream_api(
        self,
        api_url: str,
        api_key: str,
        model: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        url = f"{api_url}/{model}:streamGenerateContent?alt=sse"
        # 构建请求上下文
        gemini_context = self._build_gemini_context(
            model, prompt, image_b64_list, params
        )
        try:
            response = await self.session.post(
                url,
                headers=headers,
                json=gemini_context,
                proxies=self.proxies,
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
                            # 遍历 json_data，检查是否有图片
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
                        f"请求成功，但未返回图片数据, 响应内容: {result[:1024]}..."
                    )
                    return None, "响应中未包含图片数据"
                return b64_images, None
            else:
                logger.error(
                    f"图片生成失败，状态码: {response.status_code}, 响应内容: {result[:1024]}..."
                )
                return None, "响应中未包含图片数据"
        except Timeout as e:
            logger.error(f"网络请求超时: {e}")
            return None, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"请求错误: {e}")
            return None, "图片生成失败：程序错误"

    async def _call_gemini_api(
        self,
        api_url: str,
        api_key: str,
        model: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        url = f"{api_url}/{model}:generateContent"
        # 构建请求上下文
        gemini_context = self._build_gemini_context(
            model, prompt, image_b64_list, params
        )
        try:
            response = await self.session.post(
                url, headers=headers, json=gemini_context, proxies=self.proxies
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
                            f"图片生成失败, 响应内容: {response.text[:1024]}..."
                        )
                        return None, f"图片生成失败，原因: {finishReason}"
                # 最后再检查是否有图片数据
                if not b64_images:
                    logger.warning(
                        f"请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}..."
                    )
                    if result.get("promptFeedback", {}):
                        return (
                            None,
                            f"请求被内容安全系统拦截，原因：{result.get('promptFeedback', {}).get('blockReason', '未获取到原因')}",
                        )
                    return None, "响应中未包含图片数据"
                return b64_images, None
            else:
                logger.error(
                    f"图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}..."
                )
                err_msg = result.get("error", {}).get("message", "未知原因")
                return None, f"图片生成失败：{err_msg}"
        except Timeout as e:
            logger.error(f"网络请求超时: {e}")
            return None, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"请求错误: {e}")
            return None, "图片生成失败：程序错误"

    def _build_openai_chat_context(
        self,
        model: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]],
        stream: bool = False,
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
                    "content": [{"type": "text", "text": prompt}, *images_content],
                }
            ],
            "stream": stream,
        }
        return context

    async def _call_openai_stream_api(
        self,
        api_url: str,
        api_key: str,
        model: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]],
    ):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # 构建请求上下文
        openai_context = self._build_openai_chat_context(
            model, prompt, image_b64_list, True
        )
        try:
            # 发送请求
            response = await self.session.post(
                url=api_url,
                headers=headers,
                json=openai_context,
                proxies=self.proxies,
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
                        f"请求成功，但未返回图片数据, 响应内容: {result[:1024]}..."
                    )
                    return None, reasoning_content or "响应中未包含图片数据"
                # 下载图片并转换为 base64（有时会出现连接被重置的错误，不知道什么原因，海外机也一样）
                b64_images += await self.fetch_images(images_url)
                if not b64_images:
                    return None, "图片下载失败"
                return b64_images, None
            else:
                logger.error(
                    f"图片生成失败，状态码: {response.status_code}, 响应内容: {result[:1024]}..."
                )
                return None, "响应中未包含图片数据"
        except Timeout as e:
            logger.error(f"网络请求超时: {e}")
            return None, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"请求错误: {e}")
            return None, "图片生成失败：程序错误"

    async def _call_openai_api(
        self,
        api_url: str,
        api_key: str,
        model: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]],
    ):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # 构建请求上下文
        openai_context = self._build_openai_chat_context(
            model, prompt, image_b64_list, False
        )
        try:
            # 发送请求
            response = await self.session.post(
                url=api_url, headers=headers, json=openai_context, proxies=self.proxies
            )
            # 响应反序列化
            result = response.json()
            if response.status_code == 200:
                b64_images = []
                images_url = []
                for item in result.get("choices", []):
                    # 检查 finish_reason 状态
                    finish_reason = item.get("finish_reason", "")
                    if finish_reason == "stop":
                        content = item.get("message", {}).get("content", "")
                        match = re.search(r"!\[.*?\]\((.*?)\)", content)
                        if match:
                            img_src = match.group(1)
                            if img_src.startswith("data:image/"):  # base64
                                header, base64_data = img_src.split(",", 1)
                                mime = header.split(";")[0].replace("data:", "")
                                b64_images.append((mime, base64_data))
                            else:  # URL
                                images_url.append(img_src)
                    else:
                        logger.warning(
                            f"图片生成失败, 响应内容: {response.text[:1024]}..."
                        )
                        return None, f"图片生成失败: {finish_reason}"
                # 最后再检查是否有图片数据
                if not images_url and not b64_images:
                    logger.warning(
                        f"请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}..."
                    )
                    return None, "响应中未包含图片数据"
                # 下载图片并转换为 base64
                b64_images += await self.fetch_images(images_url)
                if not b64_images:
                    return None, "图片下载失败"
                return b64_images, None
            else:
                logger.error(
                    f"图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}..."
                )
                return None, f"图片生成失败: 状态码 {response.status_code}"
        except Timeout as e:
            logger.error(f"网络请求超时: {e}")
            return None, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"请求错误: {e}")
            return None, "图片生成失败：程序错误"

    def _random_string(self, length: int) -> str:
        import random
        import string

        letters = string.ascii_letters + string.digits
        return "".join(random.choice(letters) for _ in range(length))

    async def _execute_recaptcha(self, anchor_url: str, reload_url: str) -> str | None:
        # 获取初始化recaptcha_token
        anchor_html = await self.session.get(
            anchor_url, impersonate="chrome", proxies=self.proxies
        )
        soup = BeautifulSoup(anchor_html.text, "html.parser")
        token_element = soup.find("input", {"id": "recaptcha-token"})
        if token_element is None:
            logger.error("anchor_html 未找到 recaptcha-token 元素")
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
            impersonate="chrome",
            proxies=self.proxies,
        )
        # 解析响应内容
        match = re.search(r'rresp","(.*?)"', reload_response.text)
        if not match:
            logger.error("未找到 rresp")
            return None
        recaptcha_token = match.group(1)
        return recaptcha_token

    async def _get_recaptcha_token(self) -> str | None:
        # 个人认为验证码问题不是频繁重试能解决的，保守设置3次
        for _ in range(3):
            random_cb = self._random_string(10)
            anchor_url = f"{self.recaptcha_base_api}/recaptcha/enterprise/anchor?ar=1&k=6LdCjtspAAAAAMcV4TGdWLJqRTEk1TfpdLqEnKdj&co=aHR0cHM6Ly9jb25zb2xlLmNsb3VkLmdvb2dsZS5jb206NDQz&hl=zh-CN&v=jdMmXeCQEkPbnFDy9T04NbgJ&size=invisible&anchor-ms=20000&execute-ms=15000&cb={random_cb}"
            reload_url = f"{self.recaptcha_base_api}/recaptcha/enterprise/reload?k=6LdCjtspAAAAAMcV4TGdWLJqRTEk1TfpdLqEnKdj"
            recaptcha_token = await self._execute_recaptcha(anchor_url, reload_url)
            if recaptcha_token:
                logger.info("获取 recaptcha_token 成功")
                return recaptcha_token
            logger.warning("获取 recaptcha_token 失败，重试中...")
        return None

    async def _call_vertex_ai_anonymous_api(
        self, body: dict
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        headers = {
            "referer": "https://console.cloud.google.com/",
            "Content-Type": "application/json",
        }
        try:
            response = await self.session.post(
                url=f"{self.vertex_ai_anonymous_base_api}/v3/entityServices/AiplatformEntityService/schemas/AIPLATFORM_GRAPHQL:batchGraphql?key={self.vertex_ai_anonymous_api_key}&prettyPrint=false",
                headers=headers,
                json=body,
                impersonate="chrome",
                proxies=self.proxies,
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
                            logger.error(
                                f"图片生成失败，错误代码 {status}，错误原因 {err.get('message', '')}"
                            )
                            return None, status
                        # 没有错误，应该是正常响应
                        for candidate in item.get("data", {}).get("candidates", []):
                            # 检查 finishReason 状态
                            finishReason = candidate.get("finishReason", "")
                            if finishReason == "STOP":
                                parts = candidate.get("content", {}).get("parts", [])
                                for part in parts:
                                    if (
                                        "inlineData" in part
                                        and "data" in part["inlineData"]
                                    ):
                                        data = part["inlineData"]
                                        b64_images.append(
                                            (data["mimeType"], data["data"])
                                        )
                            else:
                                logger.warning(
                                    f"图片生成失败, 响应内容: {response.text[:1024]}..."
                                )
                                return None, f"图片生成失败，原因: {finishReason}"
                # 最后再检查是否有图片数据
                if not b64_images:
                    logger.warning(
                        f"请求成功，但未返回图片数据, 响应内容: {response.text[:1024]}..."
                    )
                    return None, "响应中未包含图片数据"
                return b64_images, None
            else:
                logger.error(
                    f"图片生成失败，状态码: {response.status_code}, 响应内容: {response.text[:1024]}..."
                )
                return None, f"图片生成失败：状态码: {response.status_code}"
        except Timeout as e:
            logger.error(f"网络请求超时: {e}")
            return None, "图片生成失败：响应超时"
        except Exception as e:
            logger.error(f"请求错误: {e}")
            return None, "图片生成失败：程序错误"

    async def _dispatch_vertex_ai_api(
        self, model, prompt, image_b64_list, params
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        body = self._build_vertex_ai_body(model, prompt, image_b64_list, params)
        recaptcha_token = await self._get_recaptcha_token()
        if recaptcha_token is None:
            return None, "获取 recaptcha_token 失败"
        for _ in range(self.vertex_ai_anonymous_max_retry):
            body["variables"]["recaptchaToken"] = recaptcha_token
            result, err_status = await self._call_vertex_ai_anonymous_api(body)
            if result is not None:
                return result, None
            # 8:资源耗尽；3:Token失效
            # if err_status == 8:
            #     continue  # 资源耗尽，不需要重新获取 token
            if err_status == 3:
                recaptcha_token = await self._get_recaptcha_token()
                if recaptcha_token is None:
                    return None, "获取 recaptcha_token 失败"
            logger.warning(
                f"图片生成失败，正在重试 Vertex AI Anonymous API ({_ + 1}/ {self.vertex_ai_anonymous_max_retry})"
            )
        else:
            return None, "图片生成失败：重试达到上限。"

    async def generate_images(
        self,
        api_type: str,
        stream: bool,
        api_url: str,
        model: str,
        api_key: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]] = [],
        params: dict = {},
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        for _ in range(self.max_retry):
            if api_type == "Gemini":
                if stream:
                    image_b64, err = await self._call_gemini_stream_api(
                        api_url=api_url,
                        model=model,
                        api_key=api_key,
                        prompt=prompt,
                        image_b64_list=image_b64_list,
                        params=params,
                    )
                else:
                    image_b64, err = await self._call_gemini_api(
                        api_url=api_url,
                        model=model,
                        api_key=api_key,
                        prompt=prompt,
                        image_b64_list=image_b64_list,
                        params=params,
                    )
            elif api_type == "OpenAI_Chat":
                if stream:
                    image_b64, err = await self._call_openai_stream_api(
                        api_url=api_url,
                        model=model,
                        api_key=api_key,
                        prompt=prompt,
                        image_b64_list=image_b64_list,
                    )
                else:
                    image_b64, err = await self._call_openai_api(
                        api_url=api_url,
                        model=model,
                        api_key=api_key,
                        prompt=prompt,
                        image_b64_list=image_b64_list,
                    )
            elif api_type == "Vertex_AI_Anonymous":
                image_b64, err = await self._dispatch_vertex_ai_api(
                    model=model,
                    prompt=prompt,
                    image_b64_list=image_b64_list,
                    params=params,
                )
                if image_b64 is None:
                    return None, err
            else:
                logger.error(f"不支持的API类型: {api_type}")
                return None, "❌ 不支持的API类型"
            if image_b64:
                return image_b64, None
            logger.warning(f"图片生成失败，正在重试 ({_ + 1}/ {self.max_retry})")
        return None, f"❌ {err}"

    async def close(self):
        await self.session.close()
