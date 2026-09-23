from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..schemas import GenerationResult
from .downloader import VideoDownloadError

if TYPE_CHECKING:
    from ...main import BigBanana


async def prepare_video_delivery(
    plugin: BigBanana,
    result: GenerationResult,
    *,
    url_only: bool,
) -> str | None:
    """Download generated videos before sending when local delivery is enabled.

    Args:
        plugin: Active plugin instance.
        result: Successful video generation result to prepare.
        url_only: Whether the caller is returning URLs instead of media components.

    Returns:
        An error message when downloading fails, otherwise None.
    """
    if url_only or not result.videos:
        return None

    created_paths: list[Path] = []
    for video in result.videos:
        if not video.download_enabled:
            continue

        if video.local_path is not None and video.local_path.is_file():
            continue
        try:
            video.local_path = await plugin.video_downloader.download(
                video.url,
                proxy=(plugin.common_config.proxy or "").strip() or None,
                retries=plugin.params_config.video_download_retries,
                timeout=plugin.params_config.video_download_timeout,
            )
            created_paths.append(video.local_path)
        except (VideoDownloadError, OSError, TypeError, ValueError) as exc:
            logger.error(f"[BIG BANANA] Generated video download failed: {exc}")
            for path in created_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning(
                        "[BIG BANANA] Could not remove partial video delivery file: "
                        f"{cleanup_error}"
                    )
            for resource in result.videos:
                if resource.local_path in created_paths:
                    resource.local_path = None
            return (
                "视频已生成，但插件下载视频以便发送失败："
                f"{exc}。可使用 --url true 获取原始视频链接：{video.url}"
            )

    return None
