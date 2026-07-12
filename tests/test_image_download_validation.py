import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from core.client.downloader import _read_image_response
from core.schemas import GenerationResult, ImageResource, VideoResource
from core.video.pipeline import VideoPipeline


class ChunkedContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self.chunks:
            yield chunk


def build_jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), (120, 80, 40)).save(output, format="JPEG")
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
