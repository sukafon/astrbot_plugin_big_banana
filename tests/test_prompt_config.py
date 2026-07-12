import json
from pathlib import Path

from core.config.prompt_config import PromptConfigManager


def test_llm_presets_are_in_default_configuration() -> None:
    schema_path = Path(__file__).parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    preset_names = {item.split(maxsplit=1)[0] for item in schema["prompt"]["default"]}

    assert {"llm_default", "llm_video_default"} <= preset_names


def test_builtin_llm_presets_exist_without_user_configuration() -> None:
    manager = PromptConfigManager({})

    assert manager.prompt_config["llm_default"] == {
        "prompt": "{{user_text}}",
        "min_images": 0,
        "max_images": 6,
        "aspect_ratio": "default",
        "image_size": "1K",
        "google_search": True,
        "gather_mode": False,
        "n": 1,
        "partial_images": 0,
        "size": "default",
        "url": False,
        "moderation": "auto",
    }
    assert manager.prompt_config["llm_video_default"] == {
        "prompt": "{{user_text}}",
        "capability": "video_generation",
        "min_images": 0,
        "max_images": 1,
        "quality": "speed",
        "fps": 30,
        "size": "default",
        "with_audio": False,
        "watermark_enabled": True,
    }


def test_user_configuration_overrides_internal_presets() -> None:
    manager = PromptConfigManager(
        {
            "prompt": [
                (
                    "bnv Animate {{user_text}} --capability video_generation "
                    "--quality quality --fps 60"
                ),
                "llm_default Paint {{user_text}} --max_images 2",
            ]
        }
    )

    assert manager.prompt_config["bnv"] == {
        "prompt": "Animate {{user_text}}",
        "capability": "video_generation",
        "quality": "quality",
        "fps": 60,
    }
    assert manager.prompt_config["llm_default"] == {
        "prompt": "Paint {{user_text}}",
        "max_images": 2,
    }


def test_rebuild_restores_internal_default_after_config_override_is_removed() -> None:
    config = {"prompt": ["llm_video_default {{user_text}} --quality quality"]}
    manager = PromptConfigManager(config)

    assert manager.prompt_config["llm_video_default"] == {
        "prompt": "{{user_text}}",
        "quality": "quality",
    }

    config["prompt"] = []
    manager.prompt_config = manager._build_prompt_config()

    assert manager.prompt_config["llm_video_default"] == {
        "prompt": "{{user_text}}",
        "capability": "video_generation",
        "min_images": 0,
        "max_images": 1,
        "quality": "speed",
        "fps": 30,
        "size": "default",
        "with_audio": False,
        "watermark_enabled": True,
    }
