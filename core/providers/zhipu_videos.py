from __future__ import annotations

import asyncio
import base64
import json
import random
import time
import uuid
from io import BytesIO

from aiohttp import ClientTimeout
from PIL import Image

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource, VideoResource
from .video_base import BaseVideoProvider

_PENDING_STATUSES = {"PROCESSING", "PENDING", "SUBMITTED", "CREATED"}
_MAX_REFERENCE_BYTES = 5 * 1024 * 1024


class ZhipuVideosProvider(BaseVideoProvider):
    """Zhipu asynchronous video generation provider."""

    provider_type = "Zhipu_Videos"

    async def generate_videos(self) -> GenerationResult:
        """Create a Zhipu job and wait for its result.

        Returns:
            Generated video resources or a provider error.
        """
        keys = list(self.provider_config.keys)
        if not keys:
            return GenerationResult(error_message="智谱视频提供商未配置 API Key")
        random.shuffle(keys)

        body, body_error = self._build_body()
        if body_error:
            return GenerationResult(error_message=body_error)

        last_error = "智谱视频任务创建失败"
        for api_key in keys:
            task_id, error = await self._create_job(api_key, body)
            if task_id:
                return await self._poll_job(api_key, task_id)
            if error:
                last_error = error

        return GenerationResult(error_message=last_error)

    def _build_body(self) -> tuple[dict, str | None]:
        prompt = self.params.get("prompt", "").strip()
        if len(prompt) > 512:
            return {}, "CogVideoX-Flash 的提示词不能超过 512 个字符"

        body: dict = {
            "model": self.provider_config.model or "cogvideox-flash",
            "request_id": f"big-banana-{uuid.uuid4().hex}",
        }
        if prompt:
            body["prompt"] = prompt

        if self.image_list:
            image_source, error = self._build_reference_image(self.image_list[0])
            if error:
                return {}, error
            body["image_url"] = image_source

        if "prompt" not in body and "image_url" not in body:
            return {}, "CogVideoX-Flash 至少需要提示词或一张参考图"

        raw_config = self.provider_config.raw_config
        quality = self.params.get("quality", raw_config.get("quality", "speed"))
        if quality not in {"speed", "quality"}:
            return {}, "quality 仅支持 speed 或 quality"
        body["quality"] = quality

        size = self.params.get("size", raw_config.get("size", "default"))
        if size and size != "default":
            body["size"] = size

        fps = self.params.get("fps", raw_config.get("fps", 30))
        if fps not in {30, 60}:
            return {}, "fps 仅支持 30 或 60"
        body["fps"] = fps

        body["with_audio"] = self.params.get(
            "with_audio", raw_config.get("with_audio", False)
        )
        body["watermark_enabled"] = self.params.get(
            "watermark_enabled",
            raw_config.get("watermark_enabled", True),
        )
        return body, None

    @staticmethod
    def _build_reference_image(
        image: ImageResource,
    ) -> tuple[str | None, str | None]:
        data_bytes = image.bytes
        mime = image.mime.lower()
        if (
            mime in {"image/jpeg", "image/jpg", "image/png"}
            and len(data_bytes) <= _MAX_REFERENCE_BYTES
        ):
            return f"data:{mime};base64,{base64.b64encode(data_bytes).decode()}", None

        try:
            with Image.open(BytesIO(data_bytes)) as source:
                converted = source.convert("RGB")
                for quality in (90, 80, 70):
                    output = BytesIO()
                    converted.save(
                        output, format="JPEG", quality=quality, optimize=True
                    )
                    jpeg_bytes = output.getvalue()
                    if len(jpeg_bytes) <= _MAX_REFERENCE_BYTES:
                        payload = base64.b64encode(jpeg_bytes).decode()
                        return f"data:image/jpeg;base64,{payload}", None
        except Exception as exc:
            logger.warning(f"[BIG BANANA] 视频参考图转换失败: {exc}")
            return None, "视频参考图无法转换为 CogVideoX 支持的格式"

        return None, "视频参考图超过 CogVideoX-Flash 的 5MB 限制"

    async def _create_job(
        self, api_key: str, body: dict
    ) -> tuple[str | None, str | None]:
        session = self.plugin.http_manager.get_aiohttp_session()
        timeout = ClientTimeout(total=self.plugin.common_config.timeout)
        try:
            async with session.post(
                self._generation_url(),
                headers=self._headers(api_key),
                json=body,
                proxy=self._proxy(),
                timeout=timeout,
            ) as response:
                response_text = await response.text()
                result = json.loads(response_text)
                if response.status != 200:
                    return None, self._extract_error(result, response.status)
                task_id = result.get("id")
                if not isinstance(task_id, str) or not task_id:
                    return None, "智谱视频接口未返回任务 ID"
                logger.info(f"[BIG BANANA] 智谱视频任务已创建: {task_id}")
                return task_id, None
        except asyncio.TimeoutError:
            return None, "智谱视频任务创建超时"
        except json.JSONDecodeError:
            return None, "智谱视频任务创建响应格式错误"
        except Exception as exc:
            logger.error(f"[BIG BANANA] 智谱视频任务创建失败: {exc}")
            return None, "智谱视频任务创建发生网络错误"

    async def _poll_job(self, api_key: str, task_id: str) -> GenerationResult:
        raw_config = self.provider_config.raw_config
        poll_interval = max(1.0, raw_config.get("poll_interval", 5))
        job_timeout = max(
            poll_interval,
            raw_config.get("job_timeout", 900),
        )
        deadline = time.monotonic() + job_timeout
        consecutive_errors = 0

        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            try:
                result = await self._fetch_job(api_key, task_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(f"[BIG BANANA] 查询智谱视频任务 {task_id} 失败: {exc}")
                if consecutive_errors >= 3:
                    return GenerationResult(
                        error_message="智谱视频任务状态连续查询失败"
                    )
                continue

            consecutive_errors = 0
            status = result.get("task_status", "")
            if not isinstance(status, str):
                return GenerationResult(
                    error_message="智谱视频任务返回了无效的状态字段"
                )
            status = status.upper()
            if status in _PENDING_STATUSES:
                continue
            if status == "FAIL":
                return GenerationResult(
                    error_message=self._extract_error(result, 200) or "智谱视频生成失败"
                )
            if status == "SUCCESS":
                videos = []
                for item in result.get("video_result", []):
                    if not isinstance(item, dict):
                        continue
                    url = item.get("url")
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        videos.append(VideoResource(url=url))
                if videos:
                    return GenerationResult(videos=videos)
                return GenerationResult(
                    error_message="智谱视频任务成功，但未返回视频 URL"
                )
            return GenerationResult(
                error_message=f"智谱视频任务返回未知状态: {status or '空'}"
            )

        return GenerationResult(
            error_message=f"智谱视频生成超过 {job_timeout} 秒仍未完成"
        )

    async def _fetch_job(self, api_key: str, task_id: str) -> dict:
        session = self.plugin.http_manager.get_aiohttp_session()
        async with session.get(
            self._result_url(task_id),
            headers=self._headers(api_key),
            proxy=self._proxy(),
            timeout=ClientTimeout(total=60),
        ) as response:
            response_text = await response.text()
            result = json.loads(response_text)
            if response.status != 200:
                raise RuntimeError(self._extract_error(result, response.status))
            return result

    def _api_root(self) -> str:
        url = (
            (self.provider_config.base_url or "https://open.bigmodel.cn/api/paas/v4")
            .strip()
            .rstrip("/")
        )
        if url.endswith("/videos/generations"):
            return url.removesuffix("/videos/generations")
        if url.endswith(("/paas/v4", "/v4")):
            return url
        return f"{url}/paas/v4"

    def _generation_url(self) -> str:
        return f"{self._api_root()}/videos/generations"

    def _result_url(self, task_id: str) -> str:
        return f"{self._api_root()}/async-result/{task_id}"

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _proxy(self) -> str | None:
        if self.provider_config.enable_proxy:
            return self.plugin.common_config.proxy
        return None

    @staticmethod
    def _extract_error(result: dict, status_code: int) -> str:
        error = result.get("error", {})
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if isinstance(message, str) and message:
                return f"{code}: {message}" if code else message
        message = result.get("message")
        if isinstance(message, str) and message:
            return message
        return f"智谱视频接口请求失败，状态码: {status_code}"
