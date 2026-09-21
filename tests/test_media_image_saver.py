import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.llm_tools.image_generation import BigBananaImageGenerationTool
from core.schemas import GenerationResult, ImageResource


def test_llm_media_send_does_not_require_saver_object_on_plugin() -> None:
    """LLM media sending should work with lightweight plugin objects too."""
    plugin = SimpleNamespace(
        preference_config=SimpleNamespace(quote_reply_mode="both"),
        params_config=SimpleNamespace(url=False),
        temp_dir=None,
    )
    event = SimpleNamespace(
        platform_meta=SimpleNamespace(name="qq"),
        unified_msg_origin="origin",
        chain_result=lambda chain: chain,
        send=AsyncMock(),
    )

    result = asyncio.run(
        BigBananaImageGenerationTool()._send_generation_result(
            plugin,
            event,
            GenerationResult(images=[ImageResource("image/png", b"image-bytes")]),
            {},
            use_proactive_send=False,
            temporary_paths=[],
        )
    )

    assert "成功发送" in result
    event.send.assert_awaited_once()
