import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.llm_tools.image_generation import BigBananaImageGenerationTool
from core.schemas import GenerationResult, ImageResource


def build_result(count: int) -> GenerationResult:
    return GenerationResult(
        images=[
            ImageResource("image/png", f"image-{index}".encode())
            for index in range(count)
        ],
        urls=[f"https://example.com/{index}.png" for index in range(count)],
    )


def test_llm_image_truncation_is_disabled_by_default() -> None:
    plugin = SimpleNamespace(
        llm_tools_config=SimpleNamespace(llm_tool_truncate_images=False)
    )
    result = build_result(3)

    BigBananaImageGenerationTool._truncate_excess_images(plugin, {"n": 1}, result)

    assert len(result.images) == 3
    assert len(result.urls) == 3


def test_llm_image_result_is_truncated_to_requested_count() -> None:
    plugin = SimpleNamespace(
        llm_tools_config=SimpleNamespace(llm_tool_truncate_images=True)
    )
    result = build_result(4)

    BigBananaImageGenerationTool._truncate_excess_images(plugin, {"n": 2}, result)

    assert len(result.images) == 2
    assert len(result.urls) == 2


def test_llm_image_truncation_defaults_to_one_image() -> None:
    plugin = SimpleNamespace(
        llm_tools_config=SimpleNamespace(llm_tool_truncate_images=True)
    )
    result = build_result(3)

    BigBananaImageGenerationTool._truncate_excess_images(plugin, {}, result)

    assert len(result.images) == 1
    assert len(result.urls) == 1


def test_llm_image_truncation_preserves_none_url_placeholders() -> None:
    plugin = SimpleNamespace(
        llm_tools_config=SimpleNamespace(llm_tool_truncate_images=True)
    )
    result = GenerationResult(
        images=[
            ImageResource("image/png", f"image-{index}".encode())
            for index in range(3)
        ],
        urls=[None, "https://example.com/2.png", None],
    )

    BigBananaImageGenerationTool._truncate_excess_images(plugin, {"n": 1}, result)

    assert len(result.images) == 1
    assert result.urls == [None]


def test_model_result_keeps_url_image_index_with_none_placeholder() -> None:
    result = GenerationResult(
        images=[
            ImageResource("image/png", b"first"),
            ImageResource("image/png", b"second"),
        ],
        urls=[None, "https://example.com/second.png"],
    )

    tool_result = BigBananaImageGenerationTool._build_model_tool_result(result)
    text_parts = [
        item.text for item in tool_result.content if hasattr(item, "text")
    ]

    assert any(
        "image 2: https://example.com/second.png" in text for text in text_parts
    )


def test_llm_image_tool_does_not_append_command_avatar_note() -> None:
    pipeline_run = AsyncMock(return_value=GenerationResult())
    plugin = SimpleNamespace(
        sub_brain_config=SimpleNamespace(tool_enabled=False),
        drawing_pipeline=SimpleNamespace(run=pipeline_run),
        llm_tools_config=SimpleNamespace(llm_tool_truncate_images=False),
    )
    params = {"prompt": "portrait"}

    with patch.object(
        BigBananaImageGenerationTool,
        "_collect_images",
        new=AsyncMock(return_value=([], ["- @123: avatar is image 1"], None)),
    ):
        asyncio.run(
            BigBananaImageGenerationTool()._generate_result(
                plugin, SimpleNamespace(), params, ["@123"]
            )
        )

    pipeline_run.assert_awaited_once()
    assert params["prompt"] == "portrait"


def test_callback_receives_image_chain_even_when_direct_send_is_true() -> None:
    import astrbot.api.message_components as Comp

    dispatch_mock = AsyncMock(return_value=True)
    plugin = SimpleNamespace(
        cooldown_guard=SimpleNamespace(mark_cooldown=lambda gid: None),
        background_callback=SimpleNamespace(dispatch=dispatch_mock),
        task_manager=SimpleNamespace(
            finish=lambda tid: None, build_task_id=lambda evt: "task-1"
        ),
    )
    tool = BigBananaImageGenerationTool()

    gen_result = GenerationResult(
        images=[ImageResource("image/png", b"data", "b64data")]
    )

    with patch.object(tool, "_generate_result", new=AsyncMock(return_value=gen_result)):
        res = asyncio.run(
            tool._generate_and_send_result(
                plugin=plugin,
                event=SimpleNamespace(
                    unified_msg_origin="origin",
                    get_group_id=lambda: "g1",
                    platform_meta=SimpleNamespace(name="qq"),
                ),
                params={},
                image_references=None,
                unified_msg_origin="origin",
                is_background_task=True,
                use_background_callback=True,
                direct_send_result=True,
            )
        )

    assert res is None
    dispatch_mock.assert_awaited_once()
    passed_result_chain = dispatch_mock.await_args.kwargs["result"]
    assert any(isinstance(comp, Comp.Image) for comp in passed_result_chain.chain)


def test_background_callback_receives_error_chain_without_image_components() -> None:
    import astrbot.api.message_components as Comp

    dispatch_mock = AsyncMock(return_value=True)
    plugin = SimpleNamespace(
        cooldown_guard=SimpleNamespace(mark_cooldown=lambda gid: None),
        background_callback=SimpleNamespace(dispatch=dispatch_mock),
        task_manager=SimpleNamespace(
            finish=lambda tid: None, build_task_id=lambda evt: "task-error-1"
        ),
    )
    tool = BigBananaImageGenerationTool()

    error_result = GenerationResult(
        images=[],
        urls=[],
        error_message="image generation failed",
    )

    with patch.object(tool, "_generate_result", new=AsyncMock(return_value=error_result)):
        res = asyncio.run(
            tool._generate_and_send_result(
                plugin=plugin,
                event=SimpleNamespace(
                    unified_msg_origin="origin",
                    get_group_id=lambda: "g1",
                    platform_meta=SimpleNamespace(name="qq"),
                ),
                params={},
                image_references=None,
                unified_msg_origin="origin",
                is_background_task=True,
                use_background_callback=True,
                direct_send_result=True,
            )
        )

    assert res is None
    dispatch_mock.assert_awaited_once()
    passed_result_chain = dispatch_mock.await_args.kwargs["result"]
    assert any(
        isinstance(comp, Comp.Plain) and "image generation failed" in comp.text
        for comp in passed_result_chain.chain
    )
    assert not any(isinstance(comp, Comp.Image) for comp in passed_result_chain.chain)


