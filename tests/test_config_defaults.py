import json
from pathlib import Path

from core.schemas import CommonConfig, LlmToolsConfig, PreferenceConfig

ROOT = Path(__file__).resolve().parents[1]


def test_background_tasks_are_disabled_by_default() -> None:
    assert PreferenceConfig().command_use_background_task is False
    assert LlmToolsConfig().llm_tool_use_background_task is False
    assert LlmToolsConfig().llm_tool_truncate_images is False


def test_llm_tool_presets_are_disabled_when_not_configured() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    llm_tool_items = schema["llm_tools"]["items"]

    assert LlmToolsConfig().llm_tool_preset_name == ""
    assert LlmToolsConfig().llm_video_tool_preset_name == ""
    assert llm_tool_items["llm_tool_preset_name"]["default"] == ""
    assert llm_tool_items["llm_video_tool_preset_name"]["default"] == ""


def test_empty_results_do_not_fall_back_by_default() -> None:
    assert CommonConfig().fallback_on_empty_result is False

    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert (
        schema["common_config"]["items"]["fallback_on_empty_result"]["default"]
        is False
    )


def test_background_tasks_are_disabled_in_config_schema() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    assert (
        schema["preference_config"]["items"]["command_use_background_task"]["default"]
        is False
    )
    assert (
        schema["llm_tools"]["items"]["llm_tool_use_background_task"]["default"]
        is False
    )
    assert (
        schema["llm_tools"]["items"]["llm_tool_truncate_images"]["default"] is False
    )


def test_deprecated_llm_avatar_skip_preference_is_removed() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    preference_items = schema["preference_config"]["items"]

    assert not hasattr(PreferenceConfig(), "skip_llm_at_first")
    assert "skip_llm_at_first" not in preference_items


def test_avatar_numbering_note_is_documented_as_command_only() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    avatar_note = schema["preference_config"]["items"]["enable_at_avatar_note"]

    assert "命令调用" in avatar_note["description"]
    assert "仅对命令调用生效" in avatar_note["hint"]


def test_vertex_anonymous_retry_controls_are_in_provider_template() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    default_provider = schema["provider_template"]["default"][0]
    provider_items = schema["provider_template"]["templates"][
        "vertex_ai_anonymous"
    ]["items"]

    assert default_provider["max_refresh"] == 5
    assert default_provider["retry_before_switch"] == 5
    assert default_provider["retry_delay"] == 1
    assert "max_retry" not in default_provider
    assert provider_items["max_refresh"]["default"] == 5
    assert provider_items["retry_before_switch"]["default"] == 5
    assert provider_items["retry_delay"]["default"] == 1
    assert "max_retry" not in provider_items
