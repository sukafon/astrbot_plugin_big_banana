import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.config.prompt_config import PromptConfigManager
from core.llm_tools.image_generation import BigBananaImageGenerationTool
from core.llm_tools.media_generation_base import BaseMediaGenerationTool
from core.llm_tools.video_generation import (
    BigBananaVideoGenerationTool,
    build_video_parameters,
)
from core.schemas import GenerationResult, VideoResource


def build_plugin() -> SimpleNamespace:
    return SimpleNamespace(
        llm_tools_config=SimpleNamespace(
            llm_tool_preset_name="llm_default",
            llm_video_tool_preset_name="llm_video_default",
        ),
        prompt_config_manager=PromptConfigManager({}),
    )


def test_video_tool_schema_limits_reference_images() -> None:
    parameters = build_video_parameters()

    assert parameters["properties"]["image_references"]["maxItems"] == 1
    fps_schema = parameters["properties"]["fps"]
    assert fps_schema["type"] == "string"
    assert fps_schema["enum"] == ["30", "60"]


def test_media_tools_share_only_the_base_class() -> None:
    assert BigBananaImageGenerationTool.__bases__ == (BaseMediaGenerationTool,)
    assert BigBananaVideoGenerationTool.__bases__ == (BaseMediaGenerationTool,)
    assert not issubclass(BigBananaVideoGenerationTool, BigBananaImageGenerationTool)


def test_resolves_video_preset_and_call_overrides() -> None:
    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        build_plugin(),
        "A paper plane takes off.",
        None,
        {"fps": 60, "with_audio": True},
        False,
    )

    assert error is None
    assert params["capability"] == "video_generation"
    assert params["prompt"] == "A paper plane takes off."
    assert params["fps"] == 60
    assert params["with_audio"] is True


def test_builtin_video_preset_is_used_without_user_configuration() -> None:
    plugin = build_plugin()
    plugin.prompt_config_manager = PromptConfigManager({})

    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        plugin,
        "A paper plane takes off.",
        None,
        {},
        False,
    )

    assert error is None
    assert params["prompt"] == "A paper plane takes off."
    assert params["capability"] == "video_generation"
    assert params["max_images"] == 1


def test_builtin_image_preset_is_used_without_user_configuration() -> None:
    plugin = build_plugin()
    plugin.prompt_config_manager = PromptConfigManager({})

    params, error = BigBananaImageGenerationTool()._resolve_params(
        plugin,
        "A paper plane takes off.",
        None,
    )

    assert error is None
    assert params is not None
    assert params["prompt"] == "A paper plane takes off."
    assert params["max_images"] == 6


def test_blank_configured_image_preset_does_not_enable_internal_preset() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_tool_preset_name = "   "

    params, error = BigBananaImageGenerationTool()._resolve_params(
        plugin,
        "A paper plane takes off.",
        None,
    )

    assert error is None
    assert params == {"prompt": "A paper plane takes off."}


def test_blank_configured_video_preset_does_not_enable_internal_preset() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_video_tool_preset_name = "   "

    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        plugin,
        "A paper plane takes off.",
        None,
        {},
        False,
    )

    assert error is None
    assert params == {
        "capability": "video_generation",
        "min_images": 0,
        "max_images": 1,
        "prompt": "A paper plane takes off.",
    }


def test_additional_image_preset_differentially_overrides_tool_preset() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_tool_preset_name = "tool_base"
    plugin.prompt_config_manager.prompt_config.update(
        {
            "tool_base": {
                "prompt": "{{user_text}}",
                "min_images": 0,
                "max_images": 6,
                "google_search": False,
            },
            "手办化": {
                "prompt": "Create a figurine of {{user_text}}",
                "max_images": 1,
            },
        }
    )

    params, error = BigBananaImageGenerationTool()._resolve_params(
        plugin,
        "a black cat",
        "手办化",
    )

    assert error is None
    assert params == {
        "prompt": "Create a figurine of a black cat",
        "min_images": 0,
        "max_images": 1,
        "google_search": False,
    }


def test_additional_video_preset_differentially_overrides_tool_preset() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_video_tool_preset_name = "video_base"
    plugin.prompt_config_manager.prompt_config.update(
        {
            "video_base": {
                "prompt": "{{user_text}}",
                "capability": "video_generation",
                "min_images": 0,
                "max_images": 1,
                "quality": "speed",
                "with_audio": False,
            },
            "cinematic": {
                "prompt": "Cinematic shot of {{user_text}}",
                "capability": "video_generation",
                "quality": "quality",
            },
        }
    )

    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        plugin,
        "a paper plane",
        "cinematic",
        {},
        False,
    )

    assert error is None
    assert params == {
        "prompt": "Cinematic shot of a paper plane",
        "capability": "video_generation",
        "min_images": 0,
        "max_images": 1,
        "quality": "quality",
        "with_audio": False,
    }


def test_missing_configured_video_preset_falls_back_to_builtin_default() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_video_tool_preset_name = "missing"

    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        plugin,
        "A paper plane takes off.",
        None,
        {"fps": 60},
        False,
    )

    assert error is None
    assert params["prompt"] == "A paper plane takes off."
    assert params["capability"] == "video_generation"
    assert params["fps"] == 60


def test_missing_configured_image_preset_falls_back_to_builtin_default() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_tool_preset_name = "missing"

    params, error = BigBananaImageGenerationTool()._resolve_params(
        plugin,
        "A paper plane takes off.",
        None,
    )

    assert error is None
    assert params is not None
    assert params["prompt"] == "A paper plane takes off."
    assert params["max_images"] == 6


def test_bnv_video_tool_default_is_resolved_as_regular_preset() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_video_tool_preset_name = "bnv"
    plugin.prompt_config_manager.prompt_config["bnv"] = {
        "prompt": "User override: {{user_text}}",
        "capability": "video_generation",
        "quality": "quality",
    }

    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        plugin,
        "a paper plane",
        None,
        {},
        False,
    )

    assert error is None
    assert params["prompt"] == "User override: a paper plane"
    assert params["quality"] == "quality"


def test_missing_configured_preset_allows_explicit_video_preset() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_video_tool_preset_name = "missing"
    plugin.prompt_config_manager.prompt_config["custom_video"] = {
        "prompt": "Animate {{user_text}}",
        "capability": "video_generation",
    }

    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        plugin,
        "a paper plane",
        "custom_video",
        {},
        False,
    )

    assert error is None
    assert params["prompt"] == "Animate a paper plane"
    assert params["capability"] == "video_generation"


def test_non_video_configured_preset_allows_explicit_video_preset() -> None:
    plugin = build_plugin()
    plugin.llm_tools_config.llm_video_tool_preset_name = "image_preset"
    plugin.prompt_config_manager.prompt_config.update(
        {
            "image_preset": {
                "prompt": "Draw {{user_text}}",
                "capability": "image_generation",
            },
            "custom_video": {
                "prompt": "Animate {{user_text}}",
                "capability": "video_generation",
            },
        }
    )

    params, error = BigBananaVideoGenerationTool._resolve_video_params(
        plugin,
        "a paper plane",
        "custom_video",
        {},
        False,
    )

    assert error is None
    assert params["prompt"] == "Animate a paper plane"
    assert params["capability"] == "video_generation"


def test_builds_video_resource_links_for_model() -> None:
    result = GenerationResult(
        videos=[VideoResource(url="https://example.com/video.mp4")]
    )

    tool_result = BigBananaVideoGenerationTool._build_model_tool_result(result)

    assert len(tool_result.content) == 2
    assert str(tool_result.content[1].uri) == "https://example.com/video.mp4"
    assert tool_result.content[1].mimeType == "video/mp4"


def test_returns_video_generation_failure_as_plain_text() -> None:
    result = GenerationResult(error_message="模型当前访问量过大")

    tool_result = BigBananaVideoGenerationTool._build_model_tool_result(result)

    assert tool_result == "视频生成失败：模型当前访问量过大"


def test_llm_video_tool_does_not_append_command_avatar_note() -> None:
    pipeline_run = AsyncMock(return_value=GenerationResult())
    plugin = SimpleNamespace(
        sub_brain_config=SimpleNamespace(tool_enabled=False),
        video_pipeline=SimpleNamespace(run=pipeline_run),
    )
    params = {"prompt": "animate portrait"}

    with patch.object(
        BigBananaVideoGenerationTool,
        "_collect_images",
        new=AsyncMock(return_value=([], ["- @123: avatar is image 1"], None)),
    ):
        asyncio.run(
            BigBananaVideoGenerationTool()._generate_result(
                plugin, SimpleNamespace(), params, ["@123"]
            )
        )

    pipeline_run.assert_awaited_once()
    assert params["prompt"] == "animate portrait"
