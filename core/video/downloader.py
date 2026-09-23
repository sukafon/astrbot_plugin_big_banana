from __future__ import annotations

import asyncio
import time
import urllib.parse
from pathlib import Path
from uuid import uuid4

from curl_cffi.requests import AsyncSession

from astrbot.api import logger

from ..client.downloader import is_public_http_url

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_MAX_VIDEO_BYTES = 512 * 1024 * 1024
_RETRYABLE_STATUSES = {408, 429}
_VIDEO_RETENTION_SECONDS = 900
_PARTIAL_RETENTION_SECONDS = 86400


class VideoDownloadError(RuntimeError):
    """Raised when a generated video cannot be downloaded safely."""


class _PermanentVideoDownloadError(VideoDownloadError):
    """Raised for download errors that should not be retried."""


class VideoDownloader:
    """Download generated videos to temporary files with retries and validation."""

    def __init__(self, temp_dir: Path) -> None:
        """Store the temporary output directory.

        Args:
            temp_dir: Directory used to store verified MP4 files.
        """
        self.temp_dir = temp_dir

    async def download(
        self,
        url: str,
        *,
        proxy: str | None,
        retries: int,
        timeout: float,
        allow_private_network: bool = False,
    ) -> Path:
        """Download and validate an MP4, retrying transient errors.

        Args:
            url: HTTP(S) URL returned by a configured video provider.
            proxy: Optional HTTP or SOCKS proxy URL.
            retries: Number of attempts after the first request, capped at five.
            timeout: Per-attempt request timeout in seconds.
            allow_private_network: Whether to trust private provider addresses.

        Returns:
            Path to the complete MP4 file.

        Raises:
            VideoDownloadError: If the response is invalid or all attempts fail.
        """
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.temp_dir / f"video_{uuid4().hex}.mp4"
        partial_path = final_path.with_suffix(".mp4.part")
        attempt_limit = min(max(retries, 0), 5) + 1
        last_error: Exception | None = None

        for attempt in range(1, attempt_limit + 1):
            partial_path.unlink(missing_ok=True)
            try:
                await self._download_once(
                    url,
                    partial_path,
                    proxy=proxy,
                    timeout=max(float(timeout), 1.0),
                    allow_private_network=allow_private_network,
                )
                partial_path.replace(final_path)
                return final_path
            except asyncio.CancelledError:
                partial_path.unlink(missing_ok=True)
                raise
            except _PermanentVideoDownloadError:
                partial_path.unlink(missing_ok=True)
                raise
            except Exception as exc:
                partial_path.unlink(missing_ok=True)
                last_error = exc
                if attempt == attempt_limit:
                    break
                logger.warning(
                    "[BIG BANANA] Video download attempt "
                    f"{attempt}/{attempt_limit} failed: {exc}"
                )
                await asyncio.sleep(min(float(attempt), 3.0))

        final_path.unlink(missing_ok=True)
        raise VideoDownloadError(
            f"download failed after {attempt_limit} attempt(s): {last_error}"
        ) from last_error

    async def _download_once(
        self,
        url: str,
        partial_path: Path,
        *,
        proxy: str | None,
        timeout: float,
        allow_private_network: bool,
    ) -> None:
        """Stream one MP4 response and verify its declared size and file header.

        Args:
            url: Initial provider URL.
            partial_path: Temporary path for this attempt.
            proxy: Optional HTTP or SOCKS proxy URL.
            timeout: Request timeout in seconds.
            allow_private_network: Whether private provider addresses are allowed.

        Raises:
            VideoDownloadError: If a network, response, or file validation error occurs.
        """
        current_url = url
        for redirect_count in range(_MAX_REDIRECTS + 1):
            try:
                parsed_url = urllib.parse.urlparse(current_url)
                if (
                    current_url != current_url.strip()
                    or any(ord(char) < 32 for char in current_url)
                    or parsed_url.scheme not in {"http", "https"}
                    or not parsed_url.hostname
                    or parsed_url.username is not None
                    or parsed_url.password is not None
                ):
                    raise ValueError("unsupported video URL")
                parsed_url.port
            except ValueError as exc:
                raise _PermanentVideoDownloadError(
                    "video URL or redirect is not a valid HTTP(S) address"
                ) from exc

            if not allow_private_network and not await is_public_http_url(current_url):
                raise _PermanentVideoDownloadError(
                    "video URL or redirect is not a public HTTP(S) address"
                )

            async with (
                AsyncSession(trust_env=False) as session,
                session.stream(
                    "GET",
                    current_url,
                    proxy=proxy or None,
                    timeout=timeout,
                    allow_redirects=False,
                    headers={
                        "Accept": "video/mp4, application/octet-stream;q=0.9, */*;q=0.1",
                        "Accept-Encoding": "identity",
                    },
                ) as response,
            ):
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("Location", "").strip()
                    if not location:
                        raise VideoDownloadError(
                            "redirect response is missing Location"
                        )
                    if redirect_count >= _MAX_REDIRECTS:
                        raise VideoDownloadError(
                            f"video URL redirected more than {_MAX_REDIRECTS} times"
                        )
                    try:
                        current_url = urllib.parse.urljoin(current_url, location)
                    except ValueError as exc:
                        raise _PermanentVideoDownloadError(
                            "video URL returned an invalid redirect"
                        ) from exc
                    continue

                if response.status_code != 200:
                    message = f"video server returned HTTP {response.status_code}"
                    if (
                        response.status_code in _RETRYABLE_STATUSES
                        or response.status_code >= 500
                    ):
                        raise VideoDownloadError(message)
                    raise _PermanentVideoDownloadError(message)

                content_length = response.headers.get("Content-Length")
                expected_length: int | None = None
                if content_length:
                    try:
                        expected_length = int(content_length)
                    except ValueError:
                        expected_length = None
                    if expected_length is not None:
                        if expected_length < 0:
                            raise _PermanentVideoDownloadError(
                                "video server returned an invalid Content-Length"
                            )
                        if expected_length > _MAX_VIDEO_BYTES:
                            raise _PermanentVideoDownloadError(
                                "video exceeds the 512 MB download limit"
                            )

                received = 0
                prefix = bytearray()
                with partial_path.open("wb") as output:
                    async for chunk in response.aiter_content():
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > _MAX_VIDEO_BYTES:
                            raise _PermanentVideoDownloadError(
                                "video exceeds the 512 MB download limit"
                            )
                        if len(prefix) < 8:
                            prefix.extend(chunk[: 8 - len(prefix)])
                        output.write(chunk)

                if expected_length is not None and received != expected_length:
                    raise VideoDownloadError(
                        f"incomplete video download ({received}/{expected_length} bytes)"
                    )
                if received < 12 or prefix[4:8] != b"ftyp":
                    raise VideoDownloadError("downloaded file is not a complete MP4")
                return

        raise VideoDownloadError("video redirect limit exceeded")

    def cleanup_stale_files(self) -> None:
        """Remove expired video files when another delivery request arrives.

        Completed videos are retained for 15 minutes. Partial downloads are
        retained for a day so concurrent requests do not remove active writes.
        """
        if not self.temp_dir.exists():
            return
        now = time.time()
        for path in self.temp_dir.glob("video_*"):
            try:
                retention = (
                    _PARTIAL_RETENTION_SECONDS
                    if path.suffix == ".part"
                    else _VIDEO_RETENTION_SECONDS
                )
                if path.stat().st_mtime < now - retention:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    f"[BIG BANANA] Could not remove stale video {path}: {exc}"
                )
