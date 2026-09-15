import json
from pathlib import Path
from types import SimpleNamespace

from core.config.prompt_config import PromptConfigManager
from core.config.provider_config import ProviderConfigManager
from core.providers.openai_images import OpenAIImagesProvider
from core.providers.openai_responses import OpenAIResponsesProvider
from core.schemas import ImageResource, ProviderConfig

ROOT = Path(__file__).resolve().parents[1]


def build_body(
    image_model: str = "",
    *,
    params: dict | None = None,
    image_list: list[ImageResource] | None = None,
) -> dict:
    plugin = SimpleNamespace(
        params_config=SimpleNamespace(
            moderation="auto",
            partial_images=0,
            size="default",
            size_keyword_map={},
            quality="default",
            background="default",
            output_format="default",
            output_compression=None,
            input_fidelity="default",
            action="default",
        )
    )
    config = ProviderConfig(
        provider_type="OpenAI_Responses",
        name="responses",
        model="gpt-5.5",
        image_model=image_model,
    )
    request_params = {"prompt": "test"}
    if params:
        request_params.update(params)
    provider = OpenAIResponsesProvider(plugin, config, request_params, image_list)
    provider._body_context_cache = None
    return provider._build_body_context()


def test_responses_uses_separate_image_generation_model() -> None:
    body = build_body("gpt-image-1")

    assert body["model"] == "gpt-5.5"
    assert body["tools"][0]["model"] == "gpt-image-1"


def test_responses_omits_image_model_when_not_configured() -> None:
    body = build_body()

    assert body["model"] == "gpt-5.5"
    assert "model" not in body["tools"][0]


def test_responses_passes_new_image_generation_options_to_tool() -> None:
    body = build_body(
        "gpt-image-2",
        params={
            "quality": "high",
            "background": "transparent",
            "output_format": "png",
            "output_compression": 50,
            "action": "generate",
        },
    )

    tool = body["tools"][0]
    assert tool["quality"] == "high"
    assert tool["background"] == "transparent"
    assert tool["output_format"] == "png"
    assert tool["output_compression"] == 50
    assert tool["action"] == "generate"


def test_responses_passes_input_fidelity_only_with_reference_images() -> None:
    body = build_body(
        params={"input_fidelity": "high"},
        image_list=[ImageResource("image/png", b"not-a-real-image")],
    )

    assert body["tools"][0]["input_fidelity"] == "high"


def test_responses_omits_input_fidelity_without_reference_images() -> None:
    body = build_body(params={"input_fidelity": "high"})

    assert "input_fidelity" not in body["tools"][0]


def test_images_api_passes_new_image_generation_options() -> None:
    plugin = SimpleNamespace(
        params_config=SimpleNamespace(
            moderation="auto",
            partial_images=0,
            size="default",
            size_keyword_map={},
            quality="default",
            background="default",
            output_format="default",
            output_compression=None,
            input_fidelity="default",
            action="default",
            n=1,
        )
    )
    config = ProviderConfig(
        provider_type="OpenAI_Images",
        name="images",
        model="gpt-image-2",
    )
    provider = OpenAIImagesProvider(
        plugin,
        config,
        {
            "prompt": "test",
            "quality": "medium",
            "background": "opaque",
            "output_format": "webp",
            "output_compression": 60,
        },
    )
    provider._body_context_cache = None

    body = provider._build_body_context()

    assert body["quality"] == "medium"
    assert body["background"] == "opaque"
    assert body["output_format"] == "webp"
    assert body["output_compression"] == 60


def test_provider_config_manager_reads_image_model_without_changing_main_model() -> None:
    manager = ProviderConfigManager(
        {
            "provider_template": [
                {
                    "name": "responses",
                    "provider_type": "OpenAI_Responses",
                    "capability": "image_generation",
                    "enabled": True,
                    "enabled_as_default": False,
                    "fallback_order": 0,
                    "model": "gpt-5.5",
                    "image_model": "gpt-image-1",
                }
            ],
            "default_astr_providers": [],
        }
    )

    configured = manager.get_provider_config("responses")
    overridden = manager.get_provider_config("responses/gpt-5.6")

    assert configured is not None
    assert configured.model == "gpt-5.5"
    assert configured.image_model == "gpt-image-1"
    assert overridden is not None
    assert overridden.model == "gpt-5.6"
    assert overridden.image_model == "gpt-image-1"


def test_openai_responses_schema_exposes_optional_image_model() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    image_model = schema["provider_template"]["templates"]["openai_responses"][
        "items"
    ]["image_model"]

    assert image_model["description"] == "生图模型"
    assert image_model["default"] == ""


def test_openai_image_schema_exposes_new_image_options() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    items = schema["openai_image_config"]["items"]

    assert items["quality"]["default"] == "default"
    assert items["background"]["default"] == "default"
    assert items["output_format"]["default"] == "default"
    assert items["output_compression"]["default"] == -1
    assert items["input_fidelity"]["default"] == "default"
    assert items["action"]["default"] == "default"


def test_prompt_parser_accepts_new_openai_image_options() -> None:
    params = PromptConfigManager({}).parse_prompt_params(
        "scene --quality high --background transparent --output_format webp "
        "--output_compression 50 --input_fidelity high --action edit"
    )

    assert params == {
        "quality": "high",
        "background": "transparent",
        "output_format": "webp",
        "output_compression": 50,
        "input_fidelity": "high",
        "action": "edit",
        "prompt": "scene",
    }
