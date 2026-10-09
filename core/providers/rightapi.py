from __future__ import annotations

import asyncio
import json
import math
import random
import time
from typing import Any
from urllib.parse import quote

from aiohttp import ClientTimeout

from astrbot.api import logger

from ..schemas import GenerationResult, ProviderCallResult
from .standard import StandardProvider
from .utils import dedupe_images


class RightAPIProvider(StandardProvider):
    """Generate images through RightAPI's asynchronous Images endpoint."""

    provider_type = "RightAPI"

    def _build_api_url(self) -> str:
        """Build the submission URL while preserving a configured proxy prefix.

        Returns:
            The Images generations endpoint, including for reference images.
        """
        url = (
            (self.provider_config.base_url or "https://www.rightapi.ai/draw/v1")
            .strip()
            .rstrip("/")
        )
        if url.endswith(("/images/generations", "/images/edits")):
            return f"{url.rsplit('/', 1)[0]}/generations"
        if url.endswith("/images"):
            return f"{url}/generations"
        if url.endswith("/v1"):
            return f"{url}/images/generations"
        if url.endswith("/draw"):
            return f"{url}/v1/images/generations"
        return f"{url}/draw/v1/images/generations"

    def _build_headers(self, api_key: str) -> dict[str, str]:
        """Build authenticated JSON request headers.

        Args:
            api_key: The key used to submit and query the same task.

        Returns:
            Bearer authentication and JSON content type headers.
        """
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _build_body_context(self) -> dict[str, Any]:
        """Build a JSON task submission with optional data URL references.

        Returns:
            RightAPI's image generation parameters and async flag.
        """
        raw_config = self.provider_config.raw_config
        size = self.params.get("size", self.plugin.params_config.size)
        if size in (None, "", "default"):
            size = self.params.get(
                "aspect_ratio", self.plugin.params_config.aspect_ratio
            )
        if size in (None, "", "default"):
            size = raw_config.get("size", "1:1")

        body: dict[str, Any] = {
            "model": self.provider_config.model,
            "prompt": self.params.get("prompt", "draw a picture"),
            "n": self.params.get("n", self.plugin.params_config.n),
            "async": True,
        }
        if size not in (None, "", "default"):
            body["size"] = size
        image_size = self.params.get("image_size", raw_config.get("image_size", "1K"))
        if image_size not in (None, "", "default"):
            body["imageSize"] = image_size
        if self.image_list:
            body["image"] = [image.to_data_url() for image in self.image_list]
        return body

    async def generate_images(self) -> GenerationResult:
        """Retry task submission, then query the accepted task with its original key.

        Returns:
            Downloaded images or the submission, task, or download error.
        """
        raw_config = self.provider_config.raw_config
        try:
            poll_interval = float(raw_config.get("poll_interval", 5))
            job_timeout = float(raw_config.get("job_timeout", 900))
        except (TypeError, ValueError):
            return GenerationResult(
                error_message="RightAPI 轮询间隔和任务超时必须为正数"
            )
        if any(
            not math.isfinite(value) or value <= 0
            for value in (poll_interval, job_timeout)
        ):
            return GenerationResult(
                error_message="RightAPI 轮询间隔和任务超时必须为正数"
            )

        keys = [key for key in self.provider_config.keys if key.strip()]
        if not keys:
            return GenerationResult(error_message="RightAPI 提供商未配置 API Key")
        random.shuffle(keys)
        max_retry = max(1, self.plugin.common_config.max_retry)
        last_result = ProviderCallResult(error_message="RightAPI 图片任务创建失败")
        for api_key in keys:
            for _attempt in range(max_retry):
                task_id, last_result = await self._create_job(api_key)
                if task_id:
                    # Once accepted, never submit another task on polling failure.
                    result = await self._poll_job(
                        api_key,
                        task_id,
                        poll_interval=poll_interval,
                        job_timeout=job_timeout,
                    )
                    return GenerationResult(
                        images=dedupe_images(result.images or []),
                        error_message=result.error_message,
                    )
                if not self.should_retry(last_result.status_code):
                    break
        error = last_result.error_message or "RightAPI 图片任务创建失败"
        if last_result.status_code:
            error = f"HTTP {last_result.status_code}：{error}"
        return GenerationResult(error_message=error)

    async def _create_job(self, api_key: str) -> tuple[str | None, ProviderCallResult]:
        """Submit a single image task without consuming the task result.

        Args:
            api_key: The API key authorizing this submission.

        Returns:
            The accepted task ID and HTTP result, or a submission error.
        """
        status_code = 0
        try:
            async with self.session.post(
                self._build_api_url(),
                headers=self._build_headers(api_key),
                json=self._build_body_context(),
                proxy=self.proxy,
                timeout=self.timeout,
            ) as response:
                status_code = response.status
                result = json.loads(await response.text())
            if not isinstance(result, dict):
                return None, ProviderCallResult(
                    status_code=status_code,
                    error_message="RightAPI 任务创建响应格式错误",
                )
            if (
                not 200 <= status_code < 300
                or result.get("error")
                or (str(result.get("status", "")).casefold() == "failed")
            ):
                return None, ProviderCallResult(
                    status_code=status_code,
                    error_message=self._extract_error_message(result),
                )
            task_id = result.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                return None, ProviderCallResult(
                    status_code=status_code,
                    error_message="RightAPI 接口未返回 task_id",
                )
            logger.info("[BIG BANANA] RightAPI image task created: %s", task_id)
            return task_id, ProviderCallResult(status_code=status_code)
        except asyncio.TimeoutError:
            return None, ProviderCallResult(
                status_code=408, error_message="RightAPI 图片任务创建超时"
            )
        except json.JSONDecodeError:
            return None, ProviderCallResult(
                status_code=status_code,
                error_message="RightAPI 任务创建响应格式错误",
            )
        except Exception as exc:
            logger.error("[BIG BANANA] RightAPI task submission failed: %s", exc)
            return None, ProviderCallResult(
                error_message="RightAPI 任务创建发生网络错误"
            )

    def _extract_result(self, result: dict) -> tuple[list[str], str | None]:
        """Read RightAPI's top-level Images result, including statusless completion.

        Args:
            result: The JSON object returned by the task query endpoint.

        Returns:
            Image URLs or base64 content and an optional task error.
        """
        if result.get("error") or str(result.get("status", "")).casefold() == "failed":
            return [], self._extract_error_message(result)
        data = result.get("data", [])
        if not isinstance(data, list):
            return [], "RightAPI 图片结果格式错误"
        sources: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            source = item.get("b64_json") or item.get("url")
            if isinstance(source, str) and source.strip():
                sources.append(source)
        return sources, None

    async def _poll_job(
        self,
        api_key: str,
        task_id: str,
        *,
        poll_interval: float,
        job_timeout: float,
    ) -> ProviderCallResult:
        """Query one accepted task until completion, failure, or the total deadline.

        Args:
            api_key: The same key used to submit this task.
            task_id: The task ID returned by RightAPI.
            poll_interval: Seconds between task queries.
            job_timeout: Maximum seconds spent querying and downloading the result.

        Returns:
            Downloaded images or a terminal task/query error.
        """
        # RightAPI queries are site-level and exclude the submission /draw prefix.
        api_root = self._build_api_url().removesuffix("/images/generations")
        api_root = api_root.removesuffix("/v1").removesuffix("/draw")
        task_url = f"{api_root}/v1/tasks/{quote(task_id, safe='')}"
        deadline = time.monotonic() + job_timeout
        consecutive_errors = 0
        max_errors = max(1, self.plugin.common_config.max_retry)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            query_error: str | None = None
            try:
                async with self.session.get(
                    task_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    proxy=self.proxy,
                    timeout=ClientTimeout(
                        total=min(self.timeout.total or 60, 60, remaining)
                    ),
                ) as response:
                    status_code = response.status
                    response_text = await response.text()
                try:
                    result = json.loads(response_text)
                except json.JSONDecodeError:
                    if 200 <= status_code < 300:
                        return ProviderCallResult(
                            error_message="RightAPI 任务查询响应格式错误"
                        )
                    # Gateway failures may return HTML instead of provider JSON.
                    result = {}
                if not 200 <= status_code < 300:
                    reason = self._extract_error_message(
                        result if isinstance(result, dict) else {}
                    )
                    query_error = f"HTTP {status_code}：{reason}"
                    if not self.should_retry(status_code):
                        return ProviderCallResult(error_message=query_error)
                elif not isinstance(result, dict):
                    return ProviderCallResult(
                        error_message="RightAPI 任务查询响应格式错误"
                    )
            except Exception as exc:
                query_error = str(exc)

            if query_error is not None:
                consecutive_errors += 1
                logger.warning(
                    "[BIG BANANA] RightAPI task query failed: %s", query_error
                )
                if consecutive_errors >= max_errors:
                    return ProviderCallResult(
                        error_message="RightAPI 任务状态连续查询失败"
                    )
                continue

            consecutive_errors = 0
            sources, error = self._extract_result(result)
            if error:
                return ProviderCallResult(error_message=error)
            status = str(result.get("status", "")).casefold()
            if status in {"queued", "processing", "in_progress"}:
                continue
            if status not in {"", "completed"}:
                return ProviderCallResult(
                    error_message=f"RightAPI 图片任务返回未知状态：{status}"
                )
            if not sources:
                return ProviderCallResult(
                    error_message="RightAPI 图片任务未返回图片数据"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                images = await asyncio.wait_for(self._build_images(sources), remaining)
            except asyncio.TimeoutError:
                break
            except Exception as exc:
                logger.error("[BIG BANANA] RightAPI result download failed: %s", exc)
                return ProviderCallResult(error_message="RightAPI 生成结果图片下载失败")
            if not images:
                return ProviderCallResult(error_message="RightAPI 生成结果图片下载失败")
            return ProviderCallResult(images=images, status_code=status_code)
        return ProviderCallResult(
            error_message=f"RightAPI 图片生成超过 {job_timeout:g} 秒仍未完成"
        )
