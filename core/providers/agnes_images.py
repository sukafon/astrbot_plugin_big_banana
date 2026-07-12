import json
from typing import Any

from .standard import StandardProvider


class AgnesImagesProvider(StandardProvider):
    """Agnes Images API 提供商"""

    provider_type = "Agnes_Images"
    image_download_headers = {
        "Accept-Encoding": "identity",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    def _build_headers(self, api_key: str) -> dict[str, str]:
        """构建 Agnes Images 请求头。"""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _build_body_context(self) -> dict:
        """构建 Agnes Images 请求体。"""
        if self._body_context_cache is not None:
            return self._body_context_cache

        context: dict[str, Any] = {
            "model": self.provider_config.model,
            "prompt": self.params.get("prompt", "draw a picture"),
            "size": self.determine_openai_size(),
            "extra_body": {"response_format": "url"},
        }
        if context["size"] == "auto":
            context["size"] = "1024x768"

        if self.image_list:
            context["extra_body"]["image"] = [
                image.to_data_url() for image in self.image_list
            ]

        self._body_context_cache = context
        return context

    def _extract_result(
        self,
        result: dict,
    ) -> tuple[list[str], str | None]:
        """解析 Agnes Images 响应中的图片来源。"""
        image_sources: list[str] = []
        for item in result.get("data", []):
            b64_data = item.get("b64_json")
            if b64_data:
                image_sources.append(b64_data)
                continue
            image_url = item.get("url")
            if image_url:
                image_sources.append(image_url)
        return image_sources, self._extract_error_message(result)

    def _extract_stream_result(
        self,
        stream_text: str,
    ) -> tuple[list[str], str | None]:
        """Agnes Images 返回普通 JSON。"""
        return self._extract_result(json.loads(stream_text))

    def _build_api_url(self) -> str:
        """构建 Agnes Images 接口地址。"""
        url = (
            (self.provider_config.base_url or "https://apihub.agnes-ai.com/v1")
            .strip()
            .rstrip("/")
        )
        if url.endswith("/images/generations"):
            return url
        if url.endswith("/images"):
            return f"{url}/generations"
        if url.endswith("/v1"):
            return f"{url}/images/generations"
        return f"{url}/v1/images/generations"

    def _extract_error_message(self, result: dict) -> str | None:
        """从 Agnes Images 响应中提取错误信息。"""
        error = result.get("error")
        if error:
            message = error.get("message")
            if message:
                return message
        return None
