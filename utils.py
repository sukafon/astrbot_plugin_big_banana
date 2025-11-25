import base64
from io import BytesIO

from curl_cffi import AsyncSession, ProxySpec, requests
from curl_cffi.requests.exceptions import Timeout
from PIL import Image

from astrbot.api import logger


class Utils:
    def __init__(
        self,
        main_provider: dict,
        network_config: dict,
        def_params: dict,
        max_retry: int,
    ):
        # 解构主提供商配置
        self.api_url = main_provider.get(
            "api_url", "https://generativelanguage.googleapis.com/v1beta/models"
        )
        self.model = main_provider.get("model", "gemini-2.5-flash-image")
        # 初始化异步HTTP会话
        proxy = network_config.get("proxy", None)
        self.proxies: ProxySpec | None = (
            {"http": proxy, "https": proxy} if proxy else None
        )
        self.session = AsyncSession(
            impersonate="chrome136", timeout=network_config.get("timeout", 600)
        )

        # 默认参数
        self.api_type = main_provider.get("api_type", "Gemini")
        self.image_size = def_params.get("image_size", "1K")
        self.aspect_ratio = def_params.get("aspect_ratio", "default")
        self.google_search = def_params.get("google_search", False)
        self.only_image_response = def_params.get("only_image_response", False)
        self.max_retry = max_retry

        # Imghippo 图床配置
        self.imghippo_key = main_provider.get("imghippo_key", "").encode("utf-8")

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
            response = await self.session.get(url)
            content = self._handle_image(response.content)
            return content, None
        except (
            requests.exceptions.SSLError,
            requests.exceptions.CertificateVerifyError,
        ):
            # 关闭SSL验证
            response = await self.session.get(url, verify=False)
            content = self._handle_image(response.content)
            return content, None
        except Timeout as e:
            logger.error(f"网络请求超时: {e}")
            return None, "下载图片失败：网络请求超时，请检查网络连通性"
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            return None, "下载图片失败"

    async def fetch_images(self, image_urls: list[str]) -> list[tuple[str, str]]:
        image_b64_list = []
        for url in image_urls:
            content, error = await self._download_image(url)
            if error or content is None:
                continue
            image_b64_list.append(content)
        return image_b64_list

    def _build_gemini_context(
        self, prompt: str, image_b64_list: list[tuple[str, str]], params: dict
    ) -> dict:
        parts: list[dict] = [{"text": prompt}]
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
        if not params.get(
            "only_image_response", self.only_image_response
        ) or params.get("google_search", self.google_search):
            responseModalities.insert(0, "TEXT")
        # 处理图片分辨率参数
        image_size = params.get("image_size", self.image_size)
        # 构建请求上下文
        context = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": responseModalities,
                "imageConfig": {"imageSize": image_size},
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE",
                },
            ],
        }
        # 处理工具类
        if params.get("google_search", self.google_search):
            context["tools"] = [{"google_search": {}}]
        # 处理图片宽高比参数
        aspect_ratio = params.get("aspect_ratio", self.aspect_ratio)
        if aspect_ratio != "default":
            context["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}
        return context

    async def _call_gemini_api(
        self,
        api_key: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]],
        params: dict,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        url = f"{self.api_url}/{self.model}:generateContent"
        gemini_context = self._build_gemini_context(prompt, image_b64_list, params)
        try:
            response = await self.session.post(
                url, headers=headers, json=gemini_context, proxies=self.proxies
            )
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
                if not b64_images:
                    logger.error(
                        f"请求成功，但未返回图片数据, 响应内容: {response.text}"
                    )
                    return None, "响应中未包含图片数据"
                return b64_images, None
            else:
                logger.error(
                    f"图片生成失败，状态码: {response.status_code}, 响应内容: {response.text}"
                )
                return None, result.get("error", {}).get("message", "图片生成失败")
        except Exception as e:
            logger.error(f"请求错误: {e}")
            return None, "图片生成失败"

    def _build_openai_context(
        self, prompt: str, image_b64_list: list[tuple[str, str]]
    ) -> dict:
        images_content = []
        for mime, b64 in image_b64_list:
            images_content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )
        context = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}, *images_content],
                }
            ],
        }
        return context

    async def _call_openai_api(
        self, api_key: str, prompt: str, image_b64_list: list[tuple[str, str]]
    ):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        openai_context = self._build_openai_context(prompt, image_b64_list)
        try:
            response = await self.session.post(
                self.api_url, headers=headers, json=openai_context, proxies=self.proxies
            )
            result = response.json()
            if response.status_code == 200:
                pass
            else:
                logger.error(
                    f"图片生成失败，状态码: {response.status_code}, 响应内容: {response.text}"
                )
                return None, result.get("error", {}).get("message", "图片生成失败")
        except Exception as e:
            logger.error(f"请求错误: {e}")
            return None, "图片生成失败"

    async def generate_images(
        self,
        api_key: str,
        prompt: str,
        image_b64_list: list[tuple[str, str]] = [],
        params: dict = {},
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        for _ in range(self.max_retry):
            if self.api_type == "Gemini":
                image_b64, err = await self._call_gemini_api(
                    api_key=api_key,
                    prompt=prompt,
                    image_b64_list=image_b64_list,
                    params=params,
                )
            else:
                # 不同平台的OpenAI规范接口返回的响应不太统一，留待以后再做。
                logger.error(f"不支持的API类型: {self.api_type}")
                return None, "❌ 不支持的API类型"
            if err is None:
                return image_b64, None
            logger.warning(f"图片生成失败，当前Key重试次数: {_ + 1}")
        return None, f"❌ {err}"

    async def close(self):
        await self.session.close()
