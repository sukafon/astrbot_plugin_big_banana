import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.drawing.collector import ImageCollector
from core.llm_tools.image_generation import BigBananaImageGenerationTool
from core.schemas import ImageResource

import astrbot.api.message_components as Comp


def build_event(platform_name: str = "aiocqhttp") -> SimpleNamespace:
    return SimpleNamespace(
        platform_meta=SimpleNamespace(name=platform_name),
        client=None,
        bot=None,
    )


def build_plugin(
    tmp_path: Path,
    *,
    refer_images: str | None = None,
    fetched_results: list[ImageResource | None] | None = None,
) -> SimpleNamespace:
    refer_images_dir = tmp_path / "refer_images"
    refer_images_dir.mkdir(exist_ok=True)

    fetched_results_list = list(fetched_results) if fetched_results else []

    async def mock_fetch_image(image_url, **kwargs):
        if not fetched_results_list:
            return None
        return fetched_results_list.pop(0)

    return SimpleNamespace(
        params_config=SimpleNamespace(
            min_images=0,
            max_images=6,
            refer_images=refer_images,
        ),
        refer_images_dir=refer_images_dir,
        data_dir=tmp_path,
        avatar_map={},
        preference_config=SimpleNamespace(
            skip_quote_first=False,
            skip_at_first=False,
        ),
        llm_tools_config=SimpleNamespace(
            llm_tool_restrict_private_network=True,
        ),
        downloader=SimpleNamespace(fetch_image=AsyncMock(side_effect=mock_fetch_image)),
    )


def test_explicit_mixed_references_keep_the_original_order(tmp_path: Path) -> None:
    fetched = [
        ImageResource("image/png", b"first-image"),
        ImageResource("image/png", b"second-image"),
        ImageResource("image/png", b"third-image"),
    ]
    plugin = build_plugin(tmp_path, fetched_results=fetched)
    collector = ImageCollector(
        plugin=plugin,
        event=build_event(),
        params={"max_images": 3},
        is_llm_tool=True,
    )

    asyncio.run(
        collector.add_explicit_references(
            ["https://example.com/first.png", "@123", "cached/third.png"]
        )
    )

    assert [image.url for image in collector.images] == [
        "https://example.com/first.png",
        ImageCollector.qq_avatar_url("123"),
        "cached/third.png",
    ]


def test_process_and_add_image_returns_status_and_error(
    tmp_path: Path,
) -> None:
    plugin = build_plugin(
        tmp_path,
        fetched_results=[ImageResource("image/png", b"usable-image"), None],
    )
    collector = ImageCollector(
        plugin=plugin,
        event=build_event(),
        params={"max_images": 2},
        is_llm_tool=True,
    )

    assert asyncio.run(collector._process_and_add_image("usable.png")) == (True, None)
    assert asyncio.run(collector._process_and_add_image("usable.png")) == (
        False,
        None,
    )
    assert asyncio.run(collector._process_and_add_image("broken.png")) == (
        False,
        "图片下载或读取失败",
    )
    assert [image.url for image in collector.images] == ["usable.png"]
    assert collector.avatar_mappings == {}
    assert collector.image_supplement_infos == []
    assert collector.reference_failures == []
    call_kwargs = plugin.downloader.fetch_image.await_args_list[0].kwargs
    assert call_kwargs["restrict_private_network"] is True
    assert call_kwargs["local_base_dir"] == plugin.refer_images_dir


def test_process_and_add_image_returns_max_error_without_fetching(
    tmp_path: Path,
) -> None:
    plugin = build_plugin(
        tmp_path,
        fetched_results=[ImageResource("image/png", b"first-image")],
    )
    collector = ImageCollector(
        plugin=plugin,
        event=build_event(),
        params={"max_images": 1},
    )

    assert asyncio.run(collector._process_and_add_image("first.png")) == (True, None)
    assert asyncio.run(collector._process_and_add_image("extra.png")) == (
        False,
        "超出 max_images=1 的限制",
    )
    assert plugin.downloader.fetch_image.await_count == 1


def test_supplement_avatars_only_collects_images(
    tmp_path: Path,
) -> None:
    event = build_event()
    event.get_sender_id = lambda: "111"
    event.get_self_id = lambda: "222"
    plugin = build_plugin(
        tmp_path,
        fetched_results=[None, ImageResource("image/png", b"self-avatar")],
    )
    collector = ImageCollector(
        plugin=plugin,
        event=event,
        params={"min_images": 1, "max_images": 2},
    )
    collector._get_avatar_url = AsyncMock(
        side_effect=["https://example.com/111.png", "https://example.com/222.png"]
    )

    asyncio.run(collector.supplement_avatars())

    assert [image.url for image in collector.images] == ["https://example.com/222.png"]
    assert collector.avatar_mappings == {}
    assert collector.image_supplement_infos == []


def test_add_msg_images_records_only_the_at_image_position(tmp_path: Path) -> None:
    event = build_event()
    event.message_obj = SimpleNamespace(message_id="message-1")
    event.get_messages = lambda: [
        Comp.Image(None, url="https://example.com/reference.png"),
        Comp.At(qq="123"),
    ]
    event.get_self_id = lambda: "999"
    event.is_at_or_wake_command = False
    plugin = build_plugin(
        tmp_path,
        fetched_results=[
            ImageResource("image/png", b"reference-image"),
            ImageResource("image/png", b"avatar-image"),
        ],
    )
    collector = ImageCollector(
        plugin=plugin,
        event=event,
        params={"max_images": 2},
    )

    asyncio.run(collector.add_msg_images())

    assert [image.url for image in collector.images] == [
        "https://example.com/reference.png",
        ImageCollector.qq_avatar_url("123"),
    ]
    assert collector.avatar_mappings == {"123": 2}
    assert collector.image_supplement_infos == ["- @123: avatar is image 2"]


def test_different_references_with_same_content_are_collected_separately(
    tmp_path: Path,
) -> None:
    plugin = build_plugin(
        tmp_path,
        fetched_results=[
            ImageResource("image/png", b"same-image"),
            ImageResource("image/png", b"same-image"),
        ],
    )
    collector = ImageCollector(
        plugin=plugin,
        event=build_event(),
        params={"max_images": 2},
        is_llm_tool=True,
    )

    asyncio.run(
        collector.add_explicit_references(
            ["https://example.com/a.png", "https://example.com/b.png"]
        )
    )

    assert [image.url for image in collector.images] == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


def test_duplicate_reference_is_not_added_or_reported_as_failure(
    tmp_path: Path,
) -> None:
    avatar_url = ImageCollector.qq_avatar_url("123")
    plugin = build_plugin(
        tmp_path,
        fetched_results=[ImageResource("image/png", b"avatar-image")],
    )
    collector = ImageCollector(
        plugin=plugin,
        event=build_event(),
        params={"max_images": 1},
        is_llm_tool=True,
    )

    asyncio.run(collector.add_explicit_references([avatar_url, "@123"]))

    assert len(collector.images) == 1
    assert collector.avatar_mappings == {}
    assert collector.reference_failures == []
    assert plugin.downloader.fetch_image.await_count == 1


def test_llm_collection_loads_default_refer_images_before_explicit_refs(
    tmp_path: Path,
) -> None:
    fixed_image = tmp_path / "refer_images" / "fixed.png"
    fixed_image.parent.mkdir()
    fixed_image.write_bytes(b"fixed")
    fetched = [
        ImageResource("image/png", b"fixed-image"),
        ImageResource("image/png", b"explicit-image"),
    ]
    plugin = build_plugin(
        tmp_path,
        refer_images="fixed.png",
        fetched_results=fetched,
    )

    images, _, error = asyncio.run(
        BigBananaImageGenerationTool()._collect_images(
            plugin,
            build_event(),
            {},
            ["https://example.com/explicit.png"],
        )
    )

    assert error is None
    assert images == fetched
    calls = plugin.downloader.fetch_image.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == fixed_image.resolve()
    assert calls[1].args[0] == "https://example.com/explicit.png"
    assert calls[0].kwargs["convert"] is True
    assert calls[0].kwargs["allow_gif"] is False


def test_llm_collection_does_not_build_avatar_numbering_notes(tmp_path: Path) -> None:
    plugin = build_plugin(
        tmp_path,
        fetched_results=[ImageResource("image/png", b"avatar-image")],
    )

    images, supplement_infos, error = asyncio.run(
        BigBananaImageGenerationTool()._collect_images(
            plugin,
            build_event(),
            {},
            ["@123"],
        )
    )

    assert error is None
    assert len(images) == 1
    assert supplement_infos == []


def test_llm_collection_identifies_the_failed_mixed_reference(
    tmp_path: Path,
) -> None:
    plugin = build_plugin(
        tmp_path,
        fetched_results=[
            ImageResource("image/png", b"first-image"),
            None,
            ImageResource("image/png", b"third-image"),
        ],
    )

    images, _, error = asyncio.run(
        BigBananaImageGenerationTool()._collect_images(
            plugin,
            build_event(),
            {"min_images": 1},
            [
                "https://example.com/first.png",
                "https://example.com/broken.png",
                "@123",
            ],
        )
    )

    assert images == []
    assert error is not None
    assert "参考图 https://example.com/broken.png 处理失败" in error
    assert "图片下载或读取失败" in error
    assert "https://example.com/first.png 处理失败" not in error
    assert "@123 处理失败" not in error
    assert plugin.downloader.fetch_image.await_count == 3


def test_llm_collection_identifies_an_unresolvable_avatar(tmp_path: Path) -> None:
    plugin = build_plugin(tmp_path)

    images, _, error = asyncio.run(
        BigBananaImageGenerationTool()._collect_images(
            plugin,
            build_event("unsupported"),
            {},
            ["https://example.com/usable.png", "@unknown-user"],
        )
    )

    assert images == []
    assert error is not None
    assert "参考图 @unknown-user 处理失败" in error
    assert "无法获取unsupported用户unknown-user的头像" in error


def test_refer_images_config_hint_covers_commands_and_llm_tools() -> None:
    schema_path = Path(__file__).parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    hint = schema["params_config"]["items"]["refer_images"]["hint"]
    assert "命令调用" in hint
    assert "LLM 图片/视频工具调用" in hint
