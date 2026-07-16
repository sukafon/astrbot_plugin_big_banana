import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.client.downloader import Downloader, _read_image_response
from core.schemas import GenerationResult, ImageResource, VideoResource
from core.video.pipeline import VideoPipeline
from PIL import Image


class ChunkedContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        url: str,
        status: int,
        *,
        location: str | None = None,
        body: bytes = b"",
    ) -> None:
        self.url = url
        self.status = status
        self.headers = {}
        if location is not None:
            self.headers["Location"] = location
        self.content = ChunkedContent([body] if body else [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requested_urls: list[str] = []
        self.request_kwargs: list[dict] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.requested_urls.append(url)
        self.request_kwargs.append(kwargs)
        return self.responses.pop(0)


def build_jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), (120, 80, 40)).save(output, format="JPEG")
    return output.getvalue()


def build_animated_gif() -> bytes:
    output = BytesIO()
    frames = [
        Image.new("RGB", (16, 16), (255, 0, 0)),
        Image.new("RGB", (16, 16), (0, 0, 255)),
    ]
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return output.getvalue()


def test_reads_the_complete_chunked_response() -> None:
    response = SimpleNamespace(
        headers={"Content-Length": "11"},
        content=ChunkedContent([b"hello", b" ", b"world"]),
    )

    content = asyncio.run(_read_image_response(response))

    assert content == b"hello world"


def test_metadata_cleanup_rejects_a_truncated_jpeg() -> None:
    truncated = build_jpeg()[:-12]

    assert ImageResource.strip_metadata(truncated) is None


def test_video_pipeline_drops_a_truncated_reference_without_crashing() -> None:
    truncated = ImageResource("image/jpeg", build_jpeg()[:-12])
    dispatcher = AsyncMock(
        return_value=GenerationResult(
            videos=[VideoResource(url="https://example.com/video.mp4")]
        )
    )
    plugin = SimpleNamespace(
        common_config=SimpleNamespace(strip_metadata=True),
        video_dispatcher=SimpleNamespace(dispatch=dispatcher),
    )

    result = asyncio.run(VideoPipeline(plugin).run({}, [truncated]))

    assert result.videos[0].url == "https://example.com/video.mp4"
    assert dispatcher.await_args.args[1] == []


def test_downloader_defaults_flatten_animated_gif() -> None:
    animated = build_animated_gif()
    encoded = base64.b64encode(animated).decode("ascii")
    data_url = f"data:image/gif;base64,{encoded}"
    downloader = Downloader(FakeSession([]))

    flattened = asyncio.run(downloader.fetch_image(data_url))
    preserved = asyncio.run(
        downloader.fetch_image(data_url, convert=True, allow_gif=True)
    )

    assert flattened is not None
    assert flattened.mime == "image/jpeg"
    with Image.open(BytesIO(flattened.bytes)) as image:
        assert image.format == "JPEG"
        assert getattr(image, "n_frames", 1) == 1
    assert preserved is not None
    assert preserved.mime == "image/gif"
    assert preserved.bytes == animated


def test_output_base64_preserves_animated_gif() -> None:
    animated = build_animated_gif()
    encoded = base64.b64encode(animated).decode("ascii")

    image = asyncio.run(
        Downloader(FakeSession([])).fetch_base64_image(
            encoded,
            convert=True,
            allow_gif=True,
        )
    )

    assert image is not None
    assert image.mime == "image/gif"
    assert image.bytes == animated
    with Image.open(BytesIO(image.bytes)) as gif:
        assert getattr(gif, "n_frames", 1) == 2


def test_restricted_download_follows_relative_redirect_and_checks_each_hop() -> None:
    image_bytes = build_jpeg()
    session = FakeSession(
        [
            FakeResponse(
                "https://public.example/start/image",
                302,
                location="../final.jpg#preview",
            ),
            FakeResponse(
                "https://public.example/final.jpg",
                200,
                body=image_bytes,
            ),
        ]
    )
    validator = AsyncMock(return_value=True)

    with patch("core.client.downloader.is_public_http_url", validator):
        content, success = asyncio.run(
            Downloader(session)._download_image(
                "https://public.example/start/image",
                restrict_private_network=True,
            )
        )

    assert success is True
    assert content == ("image/jpeg", image_bytes)
    assert session.requested_urls == [
        "https://public.example/start/image",
        "https://public.example/final.jpg",
    ]
    assert [
        call.args[0] for call in validator.await_args_list
    ] == session.requested_urls
    assert all(kwargs["allow_redirects"] is False for kwargs in session.request_kwargs)


def test_restricted_download_rejects_private_redirect_before_requesting_it() -> None:
    private_url = "http://127.0.0.1/secret.jpg"
    session = FakeSession(
        [
            FakeResponse(
                "https://public.example/image",
                302,
                location=private_url,
            )
        ]
    )
    validator = AsyncMock(side_effect=[True, False])

    with patch("core.client.downloader.is_public_http_url", validator):
        content, success = asyncio.run(
            Downloader(session)._download_image(
                "https://public.example/image",
                restrict_private_network=True,
            )
        )

    assert content is None
    assert success is True
    assert session.requested_urls == ["https://public.example/image"]
    assert [call.args[0] for call in validator.await_args_list] == [
        "https://public.example/image",
        private_url,
    ]


def test_restricted_download_allows_exactly_five_redirects() -> None:
    image_bytes = build_jpeg()
    responses = [
        FakeResponse(
            f"https://public.example/{index}",
            302,
            location=f"/{index + 1}",
        )
        for index in range(5)
    ]
    responses.append(FakeResponse("https://public.example/5", 200, body=image_bytes))
    session = FakeSession(responses)
    validator = AsyncMock(return_value=True)

    with patch("core.client.downloader.is_public_http_url", validator):
        content, success = asyncio.run(
            Downloader(session)._download_image(
                "https://public.example/0",
                restrict_private_network=True,
            )
        )

    assert success is True
    assert content == ("image/jpeg", image_bytes)
    assert session.requested_urls == [
        f"https://public.example/{index}" for index in range(6)
    ]
    assert validator.await_count == 6


def test_restricted_download_stops_before_a_sixth_redirect_target() -> None:
    responses = [
        FakeResponse(
            f"https://public.example/{index}",
            302,
            location=f"/{index + 1}",
        )
        for index in range(6)
    ]
    session = FakeSession(responses)
    validator = AsyncMock(return_value=True)

    with patch("core.client.downloader.is_public_http_url", validator):
        content, success = asyncio.run(
            Downloader(session)._download_image(
                "https://public.example/0",
                restrict_private_network=True,
            )
        )

    assert content is None
    assert success is True
    assert session.requested_urls == [
        f"https://public.example/{index}" for index in range(6)
    ]
    assert "https://public.example/6" not in session.requested_urls
    assert validator.await_count == 6
