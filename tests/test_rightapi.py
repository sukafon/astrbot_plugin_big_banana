import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import (
    ClientConnectorError,
    ConnectionTimeoutError,
    ServerDisconnectedError,
)
from core.config.provider_config import ProviderConfigManager
from core.providers import BaseProvider, rightapi
from core.providers.rightapi import RightAPIProvider
from core.schemas import CommonConfig, ImageResource, ParamsConfig, ProviderConfig

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    """Return a synthetic HTTP response without contacting a provider."""

    def __init__(self, result: object, status: int = 200) -> None:
        self.status = status
        self.text = AsyncMock(return_value=json.dumps(result))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def provider() -> RightAPIProvider:
    """Build the provider with synthetic HTTP responses and image downloads.

    Returns:
        An uninitialized provider using the real schemas and mocked I/O.
    """
    session = SimpleNamespace(
        post=Mock(
            return_value=FakeResponse({"task_id": "task-test", "status": "processing"})
        ),
        get=Mock(
            return_value=FakeResponse(
                {"data": [{"url": "https://example.com/result.png"}]}
            )
        ),
    )
    plugin = SimpleNamespace(
        common_config=CommonConfig(),
        params_config=ParamsConfig(),
        http_manager=SimpleNamespace(get_aiohttp_session=lambda: session),
        downloader=SimpleNamespace(
            fetch_images=AsyncMock(
                return_value=[ImageResource("image/png", b"result")]
            ),
            fetch_base64_image=AsyncMock(
                return_value=ImageResource("image/png", b"base64-result")
            ),
        ),
    )
    return RightAPIProvider(
        plugin,
        ProviderConfig(
            provider_type="RightAPI",
            name="RightAPI",
            keys=["test-key"],
            base_url="https://www.rightapi.ai/draw/v1",
            model="nano-banana-fast",
            raw_config={"poll_interval": 5, "job_timeout": 30},
        ),
        {"prompt": "A cat in the rain"},
    )


@pytest.fixture(autouse=True)
def skip_poll_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace polling delays while retaining the real coroutine execution.

    Args:
        monkeypatch: Fixture restoring asyncio.sleep after each test.

    Returns:
        The mock used to inspect the requested polling delays.
    """
    sleep = AsyncMock()
    monkeypatch.setattr(rightapi.asyncio, "sleep", sleep)
    return sleep


def test_template_registers_the_rightapi_image_provider() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    template = schema["provider_template"]["templates"]["rightapi"]
    configured = {key: item["default"] for key, item in template["items"].items()}
    manager = ProviderConfigManager({"provider_template": [configured]})

    assert template["name"] == "RightAPI"
    assert "stream" not in template["items"]
    assert configured["capability"] == "image_generation"
    assert configured["base_url"] == "https://www.rightapi.ai/draw/v1"
    assert configured["poll_interval"] > 0
    assert configured["job_timeout"] > 0
    assert BaseProvider.get_provider_class("RIGHTAPI") is RightAPIProvider
    assert (
        manager.get_provider_config("RightAPI/nano-banana-pro").model
        == "nano-banana-pro"
    )


@pytest.mark.asyncio
async def test_reference_images_use_json_generations_with_rightapi_parameters(provider):
    provider.image_list = [
        ImageResource("image/png", b"first"),
        ImageResource("image/jpeg", b"second"),
    ]
    provider.params.update(n=2, aspect_ratio="16:9", image_size="2K")
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    submission = provider.session.post.call_args
    assert submission.kwargs["json"] == {
        "model": "nano-banana-fast",
        "prompt": "A cat in the rain",
        "n": 2,
        "size": "16:9",
        "imageSize": "2K",
        "async": True,
        "image": ["data:image/png;base64,Zmlyc3Q=", "data:image/jpeg;base64,c2Vjb25k"],
    }
    assert submission.args[0].endswith("/images/generations")
    assert "data" not in submission.kwargs
    assert submission.kwargs["allow_redirects"] is False


def test_provider_defaults_and_explicit_size_overrides(provider):
    provider.provider_config.raw_config.update(size="4:3", image_size="4K")
    body = provider._build_body_context()
    assert body["size"] == "4:3"
    assert body["imageSize"] == "4K"
    assert "image" not in body

    provider.params.update(size="1024x1024", aspect_ratio="16:9", image_size="default")
    body = provider._build_body_context()
    assert body["size"] == "1024x1024"
    assert "imageSize" not in body


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "https://www.rightapi.ai",
        "https://www.rightapi.ai/draw/",
        "https://www.rightapi.ai/draw/v1",
        "https://www.rightapi.ai/draw/v1/images",
        "https://www.rightapi.ai/draw/v1/images/generations",
        "https://www.rightapi.ai/draw/v1/images/edits",
    ],
)
def test_normalizes_documented_submission_urls(provider, base_url):
    provider.provider_config.base_url = base_url
    assert (
        provider._build_api_url()
        == "https://www.rightapi.ai/draw/v1/images/generations"
    )


@pytest.mark.asyncio
async def test_queries_the_site_endpoint_until_statusless_completion(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.get.side_effect = [
        FakeResponse({"status": "pending"}),
        FakeResponse({"status": "queued"}),
        FakeResponse({"status": "processing"}),
        FakeResponse({"status": "in_progress", "progress": 99}),
        FakeResponse(
            {"created": 1782800000, "data": [{"url": "https://example.com/result.png"}]}
        ),
    ]
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    assert result.images[0].bytes == b"result"
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["json"]["async"] is True
    assert session.get.call_count == 5
    assert all(
        call.args[0] == "https://www.rightapi.ai/v1/tasks/task-test"
        for call in session.get.call_args_list
    )
    assert all(
        call.kwargs["headers"] == {"Authorization": "Bearer test-key"}
        for call in session.get.call_args_list
    )
    assert all(
        0 < call.kwargs["timeout"].total <= 30 for call in session.get.call_args_list
    )


@pytest.mark.asyncio
async def test_completed_base64_result_uses_the_existing_decoder(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.post.return_value.status = 202
    session.get.return_value = FakeResponse(
        {"status": "completed", "data": [{"b64_json": "YmFzZTY0LXJlc3VsdA=="}]}
    )
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    assert result.images[0].bytes == b"base64-result"
    provider.plugin.downloader.fetch_base64_image.assert_awaited_once_with(
        "YmFzZTY0LXJlc3VsdA==", convert=True, allow_gif=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_private", [False, True])
async def test_proxy_and_global_media_url_policy_are_preserved(provider, allow_private):
    provider.provider_config.enable_proxy = True
    provider.plugin.common_config.proxy = "http://127.0.0.1:10090"
    provider.plugin.common_config.allow_private_provider_urls = allow_private
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    session = provider.plugin.http_manager.get_aiohttp_session()
    assert session.post.call_args.kwargs["proxy"] == provider.plugin.common_config.proxy
    assert session.get.call_args.kwargs["proxy"] == provider.plugin.common_config.proxy
    download = provider.plugin.downloader.fetch_images.await_args.kwargs
    assert download["use_proxy"] is True
    assert download["restrict_private_network"] is not allow_private


@pytest.mark.asyncio
async def test_failure_does_not_download_images_or_submit_another_task(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.get.return_value = FakeResponse(
        {
            "status": "failed",
            "error": {
                "code": "upstream_error",
                "message": "Upstream generation failed",
            },
            "data": [{"url": "https://example.com/result.png"}],
        }
    )
    provider.provider_config.keys = ["first-key", "second-key"]
    provider.plugin.common_config.smart_retry = False
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == "upstream_error: Upstream generation failed"
    assert session.post.call_count == 1
    provider.plugin.downloader.fetch_images.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_timeout", [False, True])
async def test_retries_connection_failures_before_submission(
    provider, connection_timeout
):
    session = provider.plugin.http_manager.get_aiohttp_session()
    if connection_timeout:
        error = ConnectionTimeoutError("connection timed out")
    else:
        error = ClientConnectorError(
            SimpleNamespace(host="www.rightapi.ai", port=443, ssl=True),
            OSError("connection refused"),
        )
    session.post.side_effect = [
        error,
        FakeResponse({"task_id": "task-retried"}),
    ]
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    assert session.post.call_count == 2
    assert session.get.call_args.args[0].endswith("/task-retried")


@pytest.mark.asyncio
async def test_pre_submission_failures_exhaust_the_configured_retry_budget(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.post.side_effect = ConnectionTimeoutError("connection timed out")
    provider.plugin.common_config.max_retry = 2
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == "RightAPI 任务创建连接失败"
    assert session.post.call_count == 2
    session.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("smart_retry", [False, True])
@pytest.mark.parametrize("status_code", [301, 302, 307, 308, 408, 500, 502, 503, 504])
async def test_ambiguous_http_submission_is_never_replayed(
    provider, status_code, smart_retry
):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.post.return_value = FakeResponse(
        {"error": {"message": "submission failed"}}, status_code
    )
    provider.provider_config.keys = ["first-key", "second-key"]
    provider.plugin.common_config.smart_retry = smart_retry
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == f"HTTP {status_code}：submission failed"
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["allow_redirects"] is False
    session.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("smart_retry", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        asyncio.TimeoutError(),
        ServerDisconnectedError(),
        ConnectionResetError("connection reset"),
    ],
)
async def test_unknown_transport_outcome_never_replays_submission(
    provider, error, smart_retry
):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.post.side_effect = error
    provider.provider_config.keys = ["first-key", "second-key"]
    provider.plugin.common_config.smart_retry = smart_retry
    await provider.initialize()

    result = await provider.generate_images()

    assert "提交结果不明确" in result.error_message
    assert session.post.call_count == 1
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_accepted_response_body_timeout_never_replays_submission(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.post.return_value.text.side_effect = asyncio.TimeoutError()
    provider.provider_config.keys = ["first-key", "second-key"]
    provider.plugin.common_config.smart_retry = False
    await provider.initialize()

    result = await provider.generate_images()

    assert "提交结果不明确" in result.error_message
    assert session.post.call_count == 1
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_query_uses_the_key_that_successfully_submitted_the_task(
    provider, monkeypatch
):
    session = provider.plugin.http_manager.get_aiohttp_session()
    provider.provider_config.keys = ["invalid-key", "valid-key"]
    monkeypatch.setattr(rightapi.random, "shuffle", lambda _keys: None)
    session.post.side_effect = [
        FakeResponse({"error": {"message": "invalid key"}}, 401),
        FakeResponse({"task_id": "task-valid"}),
    ]
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    assert session.post.call_count == 2
    assert (
        session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer valid-key"
    )


@pytest.mark.asyncio
async def test_transient_query_errors_keep_polling_the_same_task(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.get.side_effect = [
        asyncio.TimeoutError(),
        FakeResponse({"status": "in_progress"}),
        FakeResponse({"error": {"message": "temporarily unavailable"}}, 503),
        FakeResponse({"data": [{"url": "https://example.com/result.png"}]}),
    ]
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    assert session.post.call_count == 1
    assert session.get.call_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [502, 503, 504])
async def test_gateway_html_errors_retry_the_accepted_task(provider, status_code):
    session = provider.plugin.http_manager.get_aiohttp_session()
    gateway_error = FakeResponse({}, status_code)
    gateway_error.text.return_value = "<html>Gateway temporarily unavailable</html>"
    session.get.side_effect = [
        gateway_error,
        FakeResponse({"data": [{"url": "https://example.com/result.png"}]}),
    ]
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message is None
    assert result.images[0].bytes == b"result"
    assert session.post.call_count == 1
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_repeated_query_errors_do_not_resubmit_with_another_key(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.get.side_effect = ConnectionError("connection reset")
    provider.provider_config.keys = ["first-key", "second-key"]
    provider.plugin.common_config.smart_retry = False
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == "RightAPI 任务状态连续查询失败"
    assert session.post.call_count == 1
    assert session.get.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [[], {"status": "processing"}, {"task_id": ""}, {"task_id": 123}]
)
async def test_rejects_invalid_submission_responses_without_polling(provider, body):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.post.return_value = FakeResponse(body)
    provider.provider_config.keys = ["first-key", "second-key"]
    provider.plugin.common_config.smart_retry = False
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message
    assert session.post.call_count == 1
    session.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["post", "get"])
async def test_reports_non_json_responses(provider, operation):
    session = provider.plugin.http_manager.get_aiohttp_session()
    getattr(session, operation).return_value.text.return_value = "<html>not JSON</html>"
    await provider.initialize()

    result = await provider.generate_images()

    assert "响应格式错误" in result.error_message
    assert session.post.call_count == 1
    assert session.get.call_count <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "error"),
    [
        ([], "RightAPI 任务查询响应格式错误"),
        ({"status": "unexpected"}, "RightAPI 图片任务返回未知状态：unexpected"),
        ({"status": "completed", "data": []}, "RightAPI 图片任务未返回图片数据"),
        (
            {"data": {"url": "https://example.com/result.png"}},
            "RightAPI 图片结果格式错误",
        ),
    ],
)
async def test_rejects_invalid_completion_responses(provider, body, error):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.get.return_value = FakeResponse(body)
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == error
    assert session.post.call_count == 1
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_query_authentication_error_is_terminal(provider):
    session = provider.plugin.http_manager.get_aiohttp_session()
    session.get.return_value = FakeResponse(
        {"error": {"message": "Not authorized"}}, 403
    )
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == "HTTP 403：Not authorized"
    assert session.get.call_count == 1
    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_poll_sleep_is_bounded_by_the_total_deadline(
    provider, monkeypatch, skip_poll_sleep
):
    provider.provider_config.raw_config.update(poll_interval=10, job_timeout=1)
    current_time = 0.0

    async def advance_time(delay: float) -> None:
        nonlocal current_time
        current_time += delay

    monkeypatch.setattr(
        rightapi, "time", SimpleNamespace(monotonic=lambda: current_time)
    )
    skip_poll_sleep.side_effect = advance_time
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == "RightAPI 图片生成超过 1 秒仍未完成"
    skip_poll_sleep.assert_awaited_once_with(1)
    provider.session.get.assert_not_called()
    assert provider.session.post.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "download_error",
    [None, asyncio.TimeoutError(), ConnectionError("connection reset")],
)
async def test_download_failure_or_timeout_never_creates_a_second_task(
    provider, download_error
):
    if download_error is not None:
        provider.plugin.downloader.fetch_images.side_effect = download_error
    else:
        provider.plugin.downloader.fetch_images.return_value = []
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message
    assert not result.images
    assert provider.session.post.call_count == 1
    if not isinstance(download_error, asyncio.TimeoutError):
        assert result.error_message == "RightAPI 生成结果图片下载失败"


@pytest.mark.asyncio
async def test_cancellation_propagates_without_resubmission(provider):
    await provider.initialize()
    provider.session.get.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await provider.generate_images()

    assert provider.session.post.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), "invalid"])
@pytest.mark.parametrize("field", ["poll_interval", "job_timeout"])
async def test_invalid_poll_settings_are_rejected_before_submission(
    provider, field, value
):
    provider.provider_config.raw_config[field] = value
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == "RightAPI 轮询间隔和任务超时必须为正数"
    provider.session.post.assert_not_called()


@pytest.mark.asyncio
async def test_missing_api_key_is_reported_before_submission(provider):
    provider.provider_config.keys = []
    await provider.initialize()

    result = await provider.generate_images()

    assert result.error_message == "RightAPI 提供商未配置 API Key"
    provider.session.post.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_prefix_is_preserved_and_task_id_is_url_encoded(provider):
    provider.provider_config.base_url = "https://proxy.example.com/service/draw/v1"
    await provider.initialize()
    provider.session.post.return_value = FakeResponse({"task_id": "task/a?b"})

    result = await provider.generate_images()

    assert result.error_message is None
    assert (
        provider.session.post.call_args.args[0]
        == "https://proxy.example.com/service/draw/v1/images/generations"
    )
    assert (
        provider.session.get.call_args.args[0]
        == "https://proxy.example.com/service/v1/tasks/task%2Fa%3Fb"
    )
