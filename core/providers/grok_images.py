from __future__ import annotations

import json
from typing import Any

from .standard import StandardProvider


class GrokImagesProvider(StandardProvider):
    """xAI Grok Imagine image generation and editing provider."""

    provider_type = "Grok_Images"

    def _build_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _build_body_context(self) -> dict[str, Any]:
        """Build the JSON body expected by the xAI Imagine image API."""
        if self._body_context_cache is not None:
            return self._body_context_cache

        raw_config = self.provider_config.raw_config
        context: dict[str, Any] = {
            "model": self.provider_config.model,
            "prompt": self.params.get("prompt", "draw a picture"),
        }

        n = self.params.get("n", raw_config.get("n", 1))
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 1
        # xAI documents `n` for image generation; image edits return one
        # edited result and should not receive this generation-only field.
        if not self.image_list and n > 1:
            context["n"] = min(n, 10)

        aspect_ratio = self.params.get(
            "aspect_ratio", raw_config.get("aspect_ratio", "default")
        )
        if aspect_ratio not in (None, "", "default"):
            context["aspect_ratio"] = aspect_ratio

        resolution_value = self.params.get(
            "resolution", raw_config.get("resolution", "")
        )
        if resolution_value not in (None, "", "default"):
            normalized_resolution = str(resolution_value).lower()
        else:
            image_size = self.params.get(
                "image_size", self.plugin.params_config.image_size
            )
            normalized_resolution = (
                image_size.lower() if image_size in {"1K", "2K"} else ""
            )
        if normalized_resolution in {"1k", "2k"}:
            context["resolution"] = normalized_resolution

        quality = self.params.get("quality", raw_config.get("quality", "auto"))
        if quality in {"low", "medium", "auto"}:
            context["quality"] = quality

        response_format = raw_config.get("response_format", "url")
        if response_format in {"url", "b64_json"}:
            context["response_format"] = response_format

        if self.image_list:
            image_sources = [
                {"type": "image_url", "url": image.to_data_url()}
                for image in self.image_list
            ]
            if len(image_sources) == 1:
                context["image"] = image_sources[0]
            else:
                # xAI uses a separate `images` field for multi-image edits.
                context["images"] = image_sources

        self._body_context_cache = context
        return context

    def _extract_result(
        self,
        result: dict,
    ) -> tuple[list[str], str | None]:
        image_sources: list[str] = []
        data = result.get("data", [])
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                source = item.get("url") or item.get("b64_json")
                if isinstance(source, str) and source:
                    image_sources.append(source)
        return image_sources, self._extract_error(result)

    def _extract_stream_result(
        self,
        stream_text: str,
    ) -> tuple[list[str], str | None]:
        """Handle a normal JSON response as well as OpenAI-style SSE relays."""
        if stream_text.lstrip().startswith("{"):
            return self._extract_result(json.loads(stream_text))

        image_sources: list[str] = []
        reason: str | None = None
        for line in stream_text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: ") :].strip()
            if payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event_sources, event_error = self._extract_result(event)
                image_sources.extend(event_sources)
                reason = event_error or reason
        return image_sources, reason

    def _build_api_url(self) -> str:
        endpoint = "edits" if self.image_list else "generations"
        url = (self.provider_config.base_url or "https://api.x.ai/v1").strip().rstrip(
            "/"
        )
        if url.endswith(("/images/generations", "/images/edits")):
            return f"{url.rsplit('/', 1)[0]}/{endpoint}"
        if url.endswith("/images"):
            return f"{url}/{endpoint}"
        if url.endswith("/v1"):
            return f"{url}/images/{endpoint}"
        return f"{url}/v1/images/{endpoint}"

    @staticmethod
    def _extract_error(result: dict) -> str | None:
        error = result.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail")
            if isinstance(message, str) and message:
                code = error.get("code")
                return f"{code}: {message}" if code else message
        elif isinstance(error, str) and error:
            return error
        message = result.get("message")
        return message if isinstance(message, str) and message else None
