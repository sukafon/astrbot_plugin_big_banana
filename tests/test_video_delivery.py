import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.schemas import GenerationResult, VideoResource
from core.utils.event_utils import build_result_message_chain
from core.video.delivery import prepare_video_delivery
from core.video.downloader import VideoDownloader, VideoDownloadError
from core.video.pipeline import VideoPipeline

import astrbot.api.message_components as Comp

_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isom"


class FakeResponse:
    def __init__(self, status_code: int, *, location: str | None = None) -> None:
        self.status_code = status_code
        self.headers = {"Content-Length": str(len(_MP4))}
        if location is not None:
            self.headers["Location"] = location

    async def aiter_content(self):
        yield _MP4


class FakeSession:
    instances = []
    responses: list[FakeResponse] = []

    def __init__(self, *, trust_env: bool) -> None:
        self.trust_env = trust_env
        self.requests = []
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None

    def stream(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)

        class Stream:
            async def __aenter__(self):
                return response

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return None

        return Stream()


def test_download_uses_configured_proxy_for_local_provider_url(tmp_path: Path) -> None:
    FakeSession.instances = []
    FakeSession.responses = [FakeResponse(200)]

    with patch("core.video.downloader.AsyncSession", FakeSession):
        path = asyncio.run(
            VideoDownloader(tmp_path).download(
                "http://127.0.0.1:8317/clip.mp4",
                proxy="http://127.0.0.1:10090",
                retries=0,
                timeout=10,
            )
        )

    session = FakeSession.instances[0]
    assert path.read_bytes() == _MP4
    assert session.trust_env is False
    assert session.requests[0][1] == "http://127.0.0.1:8317/clip.mp4"
    assert session.requests[0][2]["proxy"] == "http://127.0.0.1:10090"
    assert session.requests[0][2]["allow_redirects"] is False


def test_download_rejects_non_http_redirect_before_connecting(tmp_path: Path) -> None:
    FakeSession.instances = []
    FakeSession.responses = [FakeResponse(302, location="file:///etc/passwd")]

    with patch("core.video.downloader.AsyncSession", FakeSession):
        try:
            asyncio.run(
                VideoDownloader(tmp_path).download(
                    "https://video.example/clip.mp4",
                    proxy=None,
                    retries=0,
                    timeout=10,
                )
            )
        except VideoDownloadError:
            pass
        else:
            raise AssertionError("non-HTTP redirect was accepted")

    assert len(FakeSession.instances) == 1


def test_download_follows_relative_http_redirect(tmp_path: Path) -> None:
    FakeSession.instances = []
    FakeSession.responses = [
        FakeResponse(302, location="../final.mp4"),
        FakeResponse(200),
    ]

    with patch("core.video.downloader.AsyncSession", FakeSession):
        path = asyncio.run(
            VideoDownloader(tmp_path).download(
                "https://video.example/start/clip.mp4",
                proxy=None,
                retries=0,
                timeout=10,
            )
        )

    assert path.read_bytes() == _MP4
    assert [session.requests[0][1] for session in FakeSession.instances] == [
        "https://video.example/start/clip.mp4",
        "https://video.example/final.mp4",
    ]


def test_next_video_request_cleans_expired_files_only(tmp_path: Path) -> None:
    now = time.time()
    expired_video = tmp_path / "video_expired.mp4"
    current_video = tmp_path / "video_current.mp4"
    old_partial = tmp_path / "video_old.mp4.part"
    active_partial = tmp_path / "video_active.mp4.part"
    for path in (expired_video, current_video, old_partial, active_partial):
        path.write_bytes(_MP4)
    os.utime(expired_video, (now - 901, now - 901))
    os.utime(old_partial, (now - 86401, now - 86401))
    os.utime(active_partial, (now - 901, now - 901))

    downloader = VideoDownloader(tmp_path)
    result = GenerationResult(error_message="provider unavailable")
    plugin = SimpleNamespace(
        video_downloader=downloader,
        common_config=SimpleNamespace(strip_metadata=False),
        video_dispatcher=SimpleNamespace(dispatch=AsyncMock(return_value=result)),
    )
    asyncio.run(VideoPipeline(plugin).run({}, []))

    assert not expired_video.exists()
    assert not old_partial.exists()
    assert current_video.exists()
    assert active_partial.exists()


def test_local_video_is_retained_after_building_the_message(tmp_path: Path) -> None:
    path = tmp_path / "video_ready.mp4"
    path.write_bytes(_MP4)
    downloader = SimpleNamespace(download=AsyncMock(return_value=path))
    plugin = SimpleNamespace(
        video_downloader=downloader,
        common_config=SimpleNamespace(proxy=""),
        params_config=SimpleNamespace(
            video_download_retries=3, video_download_timeout=30
        ),
    )
    video = VideoResource(url="https://video.example/clip.mp4", download_enabled=True)
    result = GenerationResult(videos=[video])

    error = asyncio.run(prepare_video_delivery(plugin, result, url_only=False))
    temporary_paths = []
    message = build_result_message_chain(
        SimpleNamespace(message_obj=None), result, temporary_paths=temporary_paths
    )

    assert error is None
    assert video.local_path == path
    assert path.exists()
    assert temporary_paths == []
    assert any(isinstance(component, Comp.Video) for component in message)
