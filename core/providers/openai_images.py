import json

from aiohttp import FormData

from .standard import StandardProvider


class OpenAIImagesProvider(StandardProvider):
    """OpenAI 官方 Images API 提供商"""

    provider_type = "OpenAI_Images"

    def _build_headers(self, api_key: str) -> dict[str, str]:
        """构建 Images 请求头。"""
        headers = {"Authorization": f"Bearer {api_key}"}
        if not self.image_list:
            headers["Content-Type"] = "application/json"
        return headers

    def _build_edit_body(self) -> FormData:
        """构建 Images edits multipart 请求体。"""
        # multipart 的 FormData，发送时可能被 aiohttp 消费掉，所以每次都重新构建
        data = {
            "model": self.provider_config.model,
            "prompt": self.params.get("prompt", "draw a picture"),
            "n": self.params.get("n", self.plugin.params_config.n),
            "size": self.determine_openai_size(),
        }
        is_gpt_image = self.provider_config.model.startswith(
            ("gpt-image", "chatgpt-image")
        )
        # gpt-image 专有的 moderation 参数
        if is_gpt_image:
            data["moderation"] = self.params.get(
                "moderation", self.plugin.params_config.moderation
            )
        if self.provider_config.stream:
            data["stream"] = "true"
            data["partial_images"] = self.params.get(
                "partial_images", self.plugin.params_config.partial_images
            )

        multipart = FormData()
        for key, val in data.items():
            if val is not None:
                # 在 multipart 表单中，如果 n 为 1 或默认值，省略该字段以避免部分中转站校验 FormData 字符串 "1" 报错
                if key == "n" and (val == 1 or val == "1"):
                    continue
                multipart.add_field(name=key, value=str(val))
        for index, image in enumerate(self.image_list, start=1):
            file_mime = image.mime.replace("image/jpg", "image/jpeg")
            file_ext = file_mime.split("/", 1)[-1].replace("jpeg", "jpg")
            multipart.add_field(
                name="image[]",
                value=image.bytes,
                filename=f"image_{index}.{file_ext}",
                content_type=file_mime,
            )
        return multipart

    def _build_body_context(self) -> dict | FormData:
        """构建 Images 请求体。"""
        if self.image_list:
            return self._build_edit_body()

        # 读取缓存
        if self._body_context_cache is not None:
            return self._body_context_cache

        context = {
            "model": self.provider_config.model,
            "prompt": self.params.get("prompt", "draw a picture"),
            "n": self.params.get("n", self.plugin.params_config.n),
            "size": self.determine_openai_size(),
        }
        is_gpt_image = self.provider_config.model.startswith(
            ("gpt-image", "chatgpt-image")
        )
        # 此参数仅支持dall-e-2 / dall-e-3
        if self.provider_config.model.startswith(("dall-e-2", "dall-e-3")):
            context["response_format"] = "b64_json"
        # gpt-image 专有的 moderation 参数
        if is_gpt_image:
            context["moderation"] = self.params.get(
                "moderation", self.plugin.params_config.moderation
            )
        if self.provider_config.stream:
            context["stream"] = True
            context["partial_images"] = self.params.get(
                "partial_images", self.plugin.params_config.partial_images
            )

        self._body_context_cache = context
        return context

    def _extract_result(
        self,
        result: dict,
    ) -> tuple[list[str], str | None]:
        """解析 Images 响应中的 base64 或 URL 图片来源。"""
        image_sources: list[str] = []
        for item in result.get("data", []):
            b64_data = item.get("b64_json")
            if b64_data:
                image_sources.append(b64_data)
                continue
            image_url = item.get("url")
            if image_url:
                image_sources.append(image_url)
        return image_sources, None

    def _extract_stream_result(
        self,
        stream_text: str,
    ) -> tuple[list[str], str | None]:
        """解析 Images SSE 响应中的最终图片。"""
        if stream_text.lstrip().startswith("{"):
            return self._extract_result(json.loads(stream_text))

        image_sources: list[str] = []
        reason = None
        for line in stream_text.splitlines():
            if not line.startswith("data: "):
                continue
            line_data = line[len("data: ") :].strip()
            if line_data == "[DONE]":
                continue
            try:
                event = json.loads(line_data)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type in ("image_generation.completed", "image_edit.completed"):
                b64_data = event.get("b64_json")
                if b64_data:
                    image_sources.append(b64_data)
                    continue
                image_url = event.get("url")
                if image_url:
                    image_sources.append(image_url)
            elif event_type == "error":
                reason = event.get("error", {}).get("message")
        return image_sources, reason

    def _build_api_url(self) -> str:
        """构建 Images 接口地址。"""
        endpoint = "edits" if self.image_list else "generations"
        url = (
            (self.provider_config.base_url or "https://api.openai.com/v1")
            .strip()
            .rstrip("/")
        )
        if url.endswith(("/images/generations", "/images/edits")):
            return f"{url.rsplit('/', 1)[0]}/{endpoint}"
        if url.endswith("/images"):
            return f"{url}/{endpoint}"
        if url.endswith("/v1"):
            return f"{url}/images/{endpoint}"
        return f"{url}/v1/images/{endpoint}"
