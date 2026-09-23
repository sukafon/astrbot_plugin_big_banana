from __future__ import annotations

import asyncio
import json
import random
import time
from typing import TYPE_CHECKING, Any

from aiohttp import ClientTimeout

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource, ProviderConfig, VideoResource
from .video_base import BaseVideoProvider

if TYPE_CHECKING:
    from ...main import BigBanana

_PENDING_STATUSES = {
    "pending",
    "processing",
    "queued",
    "submitted",
    "created",
    "in_progress",
}
_TERMINAL_FAILURE_STATUSES = {"failed", "expired", "cancelled", "canceled"}
_DONE_STATUSES = {"done", "success", "succeeded", "completed"}


def build_grok_video_api_url(
    base_url: str,
    request_id: str | None = None,
) -> str:
    """Build the xAI video generation or polling endpoint."""
    base_url = (base_url or "https://api.x.ai/v1").strip().rstrip("/")
    if base_url.endswith("/videos/generations"):
        api_root = base_url.removesuffix("/videos/generations")
    elif base_url.endswith("/videos"):
        api_root = base_url.removesuffix("/videos")
    elif base_url.endswith("/v1"):
        api_root = base_url
    else:
        api_root = f"{base_url}/v1"
    if request_id is None:
        return f"{api_root}/videos/generations"
    return f"{api_root}/videos/{request_id}"


class GrokVideosProvider(BaseVideoProvider):
    """xAI Grok Imagine asynchronous video generation provider."""

    provider_type = "Grok_Videos"

    def __init__(
        self,
        plugin: BigBanana,
        provider_config: ProviderConfig,
        params: dict[str, Any],
        image_list: list[ImageResource] | None = None,
    ) -> None:
        super().__init__(plugin, provider_config, params, image_list)
        self.proxy: str | None = (
            plugin.common_config.proxy if provider_config.enable_proxy else None
        )

    async def generate_videos(self) -> GenerationResult:
        keys = [key for key in self.provider_config.keys if key.strip()]
        if not keys:
            return GenerationResult(error_message="Grok 视频提供商未配置 API Key")
        random.shuffle(keys)

        body, body_error = self._build_body()
        if body_error:
            return GenerationResult(error_message=body_error)

        last_error = "Grok 视频任务创建失败"
        for api_key in keys:
            request_id, error = await self._create_job(api_key, body)
            if request_id:
                return await self._poll_job(api_key, request_id)
            if error:
                last_error = error
        return GenerationResult(error_message=last_error)

    def _build_body(self) -> tuple[dict[str, Any], str | None]:
        prompt = self.params.get("prompt", "").strip()
        if not prompt and not self.image_list:
            return {}, "Grok 视频生成至少需要提示词或一张参考图"

        body: dict[str, Any] = {
            "model": self.provider_config.model,
        }
        if prompt:
            body["prompt"] = prompt
        if self.image_list:
            # The xAI REST API accepts a public URL or a base64 data URI here.
            body["image"] = {"url": self.image_list[0].to_data_url()}

        duration_value = self.params.get(
            "duration", self.plugin.params_config.video_duration
        )
        if duration_value in (None, "", "default"):
            duration = None
        else:
            try:
                duration = int(duration_value)
            except (TypeError, ValueError):
                return {}, "Grok 视频 duration 必须是 1 到 15 之间的整数"
            if not 1 <= duration <= 15:
                return {}, "Grok 视频 duration 必须是 1 到 15 之间的整数"
        if duration is not None:
            body["duration"] = duration

        aspect_ratio = self.params.get(
            "aspect_ratio", self.plugin.params_config.video_aspect_ratio
        )
        if aspect_ratio not in (None, "", "default"):
            body["aspect_ratio"] = str(aspect_ratio)

        video_size = self.params.get("video_size", self.plugin.params_config.video_size)
        resolution_value = video_size
        if resolution_value not in (None, "", "default"):
            body["resolution"] = resolution_value

        generate_audio = self.params.get(
            "with_audio", self.plugin.params_config.video_with_audio
        )
        body["generate_audio"] = generate_audio

        return body, None

    async def _create_job(
        self,
        api_key: str,
        body: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        session = self.plugin.http_manager.get_aiohttp_session()
        generation_url = build_grok_video_api_url(self.provider_config.base_url)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with session.post(
                generation_url,
                headers=headers,
                json=body,
                proxy=self.proxy,
                timeout=ClientTimeout(total=self.plugin.common_config.timeout),
            ) as response:
                response_text = await response.text()
                try:
                    result = json.loads(response_text)
                except json.JSONDecodeError:
                    return None, "Grok 视频任务创建响应格式错误"
                if not isinstance(result, dict):
                    return None, "Grok 视频任务创建响应格式错误"
                if response.status < 200 or response.status >= 300:
                    return None, self._extract_error(result, response.status)
                request_id = result.get("request_id") or result.get("id")
                if not isinstance(request_id, str) or not request_id:
                    return None, "Grok 视频接口未返回 request_id"
                logger.info(f"[BIG BANANA] Grok 视频任务已创建: {request_id}")
                return request_id, None
        except asyncio.TimeoutError:
            return None, "Grok 视频任务创建超时"
        except Exception as exc:
            logger.error(f"[BIG BANANA] Grok 视频任务创建失败: {exc}")
            return None, "Grok 视频任务创建发生网络错误"

    async def _poll_job(self, api_key: str, request_id: str) -> GenerationResult:
        poll_interval = self.plugin.params_config.video_poll_interval
        job_timeout = self.plugin.params_config.video_job_timeout
        deadline = time.monotonic() + job_timeout
        consecutive_errors = 0

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))
            if time.monotonic() >= deadline:
                break
            try:
                result = await self._fetch_job(
                    api_key,
                    request_id,
                    timeout=deadline - time.monotonic(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(
                    f"[BIG BANANA] 查询 Grok 视频任务 {request_id} 失败: {exc}"
                )
                if consecutive_errors >= 3:
                    return GenerationResult(
                        error_message="Grok 视频任务状态连续查询失败"
                    )
                continue

            consecutive_errors = 0
            status = str(result.get("status", "")).casefold()
            if status in _PENDING_STATUSES:
                continue
            if status in _DONE_STATUSES:
                video = result.get("video")
                url = video.get("url") if isinstance(video, dict) else None
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return GenerationResult(
                        videos=[
                            VideoResource(
                                url=url,
                                download_enabled=self.provider_config.video_download_enabled,
                            )
                        ]
                    )
                return GenerationResult(
                    error_message="Grok 视频任务完成，但未返回视频 URL"
                )
            if status in _TERMINAL_FAILURE_STATUSES:
                return GenerationResult(
                    error_message=self._extract_error(result, 200)
                    or f"Grok 视频任务结束状态: {status or '空'}"
                )
            return GenerationResult(
                error_message=f"Grok 视频任务返回未知状态: {status or '空'}"
            )

        return GenerationResult(
            error_message=f"Grok 视频生成超过 {job_timeout} 秒仍未完成"
        )

    async def _fetch_job(
        self, api_key: str, request_id: str, *, timeout: float
    ) -> dict[str, Any]:
        session = self.plugin.http_manager.get_aiohttp_session()
        result_url = build_grok_video_api_url(
            self.provider_config.base_url,
            request_id=request_id,
        )
        headers = {"Authorization": f"Bearer {api_key}"}
        async with session.get(
            result_url,
            headers=headers,
            proxy=self.proxy,
            timeout=ClientTimeout(total=min(60, timeout)),
        ) as response:
            response_text = await response.text()
            result = json.loads(response_text)
            if not isinstance(result, dict):
                raise RuntimeError("Grok 视频状态响应不是 JSON 对象")
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(self._extract_error(result, response.status))
            return result

    @staticmethod
    def _extract_error(result: dict[str, Any], status_code: int) -> str:
        error = result.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail")
            if isinstance(message, str) and message:
                code = error.get("code")
                return f"{code}: {message}" if code else message
        elif isinstance(error, str) and error:
            return error
        message = result.get("message")
        if isinstance(message, str) and message:
            return message
        return f"Grok 视频接口请求失败，状态码: {status_code}"
