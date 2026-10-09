import asyncio
import base64
import socket
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.resolver import ThreadedResolver
from core.client.downloader import (
    Downloader,
    _PublicMediaResolver,
    _read_image_response,
)
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
        self.connector = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        if self.connector is not None:
            await self.connector.close()

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.requested_urls.append(url)
        self.request_kwargs.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def mock_restricted_session(monkeypatch):
    """Inject fake responses while closing the actual restricted connector.

    Args:
        monkeypatch: Fixture restoring the scoped ClientSession factory.

    Returns:
        A function installing a fake session for a restricted download.
    """

    def install(session):
        def create_session(*, connector, **kwargs):
            session.connector = connector
            return session

        monkeypatch.setattr("core.client.downloader.ClientSession", create_session)

    return install


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
        video_downloader=SimpleNamespace(cleanup_stale_files=lambda: None),
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


def test_restricted_download_follows_relative_redirect_and_checks_each_hop(
    mock_restricted_session,
) -> None:
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
    mock_restricted_session(session)

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


def test_restricted_download_rejects_private_redirect_before_requesting_it(
    mock_restricted_session,
) -> None:
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
    mock_restricted_session(session)

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


def test_restricted_download_allows_exactly_five_redirects(
    mock_restricted_session,
) -> None:
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
    mock_restricted_session(session)

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


def test_restricted_download_stops_before_a_sixth_redirect_target(
    mock_restricted_session,
) -> None:
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
    mock_restricted_session(session)

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], ["::1"], ["224.0.0.1"]]
)
async def test_connection_resolver_rejects_nonpublic_or_mixed_dns_answers(
    monkeypatch, addresses
):
    results = [
        {
            "hostname": "media.example",
            "host": ip,
            "port": 443,
            "family": socket.AF_INET,
            "proto": 0,
            "flags": 0,
        }
        for ip in addresses
    ]
    monkeypatch.setattr(ThreadedResolver, "resolve", AsyncMock(return_value=results))

    with pytest.raises(OSError, match="nonpublic"):
        await _PublicMediaResolver().resolve("media.example", 443)


@pytest.mark.asyncio
async def test_connection_resolver_preserves_public_answers_and_allows_only_the_configured_proxy(
    monkeypatch,
):
    results = [
        {
            "hostname": "media.example",
            "host": "93.184.216.34",
            "port": 443,
            "family": socket.AF_INET,
            "proto": 0,
            "flags": 0,
        }
    ]
    resolve = AsyncMock(return_value=results)
    monkeypatch.setattr(ThreadedResolver, "resolve", resolve)
    resolver = _PublicMediaResolver(trusted_proxy_host="proxy.internal")

    assert await resolver.resolve("media.example", 443) is results
    resolve.return_value = [dict(results[0], host="127.0.0.1")]
    assert await resolver.resolve("proxy.internal", 8080) == resolve.return_value
    with pytest.raises(OSError, match="nonpublic"):
        await resolver.resolve("other.internal", 8080)


@pytest.mark.asyncio
async def test_rebinding_is_blocked_before_any_internal_http_request(monkeypatch):
    received = []

    async def internal_image(request):
        received.append(request.path)
        return web.Response(body=build_jpeg(), content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/private.jpg", internal_image)
    runner = web.AppRunner(app)
    await runner.setup()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    site = web.SockSite(runner, listener)
    await site.start()
    monkeypatch.setattr(
        "core.client.downloader.is_public_http_url", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        ThreadedResolver,
        "resolve",
        AsyncMock(
            return_value=[
                {
                    "hostname": "rebind.example",
                    "host": "127.0.0.1",
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": 0,
                }
            ]
        ),
    )
    try:
        content, _success = await Downloader(FakeSession([]))._download_image(
            f"http://rebind.example:{port}/private.jpg",
            restrict_private_network=True,
        )
        assert content is None
        assert received == []
    finally:
        await runner.cleanup()


def test_restricted_proxy_pins_verified_ip_and_preserves_host_tls_and_relative_redirects(
    mock_restricted_session,
):
    image_bytes = build_jpeg()
    session = FakeSession(
        [
            FakeResponse(
                "https://93.184.216.34/start/image", 302, location="../final.jpg"
            ),
            FakeResponse("https://93.184.216.34/final.jpg", 200, body=image_bytes),
        ]
    )
    mock_restricted_session(session)
    addresses = AsyncMock(return_value=["93.184.216.34"])
    with patch("core.client.downloader.resolve_public_http_addresses", addresses):
        content, success = asyncio.run(
            Downloader(session, "http://127.0.0.1:8080")._download_image(
                "https://public.example:8443/start/image",
                use_proxy=True,
                restrict_private_network=True,
                headers={"User-Agent": "test-agent"},
            )
        )

    assert success is True
    assert content == ("image/jpeg", image_bytes)
    assert session.requested_urls == [
        "https://93.184.216.34:8443/start/image",
        "https://93.184.216.34:8443/final.jpg",
    ]
    assert [call.args[0] for call in addresses.await_args_list] == [
        "https://public.example:8443/start/image",
        "https://public.example:8443/final.jpg",
    ]
    for kwargs in session.request_kwargs:
        assert kwargs["proxy"] == "http://127.0.0.1:8080"
        assert kwargs["headers"]["Host"] == "public.example:8443"
        assert kwargs["headers"]["User-Agent"] == "test-agent"
        assert kwargs["server_hostname"] == "public.example"
        assert kwargs["allow_redirects"] is False


def test_private_url_opt_in_uses_the_existing_shared_session():
    session = FakeSession(
        [FakeResponse("http://127.0.0.1/image.jpg", 200, body=build_jpeg())]
    )
    with patch("core.client.downloader.ClientSession") as create_session:
        content, success = asyncio.run(
            Downloader(session)._download_image(
                "http://127.0.0.1/image.jpg", restrict_private_network=False
            )
        )
    assert success is True
    assert content is not None
    create_session.assert_not_called()


@pytest.mark.asyncio
async def test_real_proxy_connect_uses_the_verified_ip_without_another_dns_lookup(
    monkeypatch,
):
    requests = []

    async def proxy_handler(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        requests.append(request.decode("ascii").split("\r\n")[0])
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(proxy_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        "core.client.downloader.resolve_public_http_addresses",
        AsyncMock(return_value=["93.184.216.34"]),
    )
    dns = AsyncMock(side_effect=AssertionError("unexpected second DNS lookup"))
    monkeypatch.setattr(ThreadedResolver, "resolve", dns)
    try:
        content, _success = await asyncio.wait_for(
            Downloader(FakeSession([]), f"http://127.0.0.1:{port}")._download_image(
                "https://rebind.example/image.jpg",
                use_proxy=True,
                restrict_private_network=True,
            ),
            timeout=2,
        )
        assert content is None
        assert requests == ["CONNECT 93.184.216.34:443 HTTP/1.1"]
        dns.assert_not_awaited()
    finally:
        server.close()
        await server.wait_closed()
