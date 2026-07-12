from __future__ import annotations

from typing import TYPE_CHECKING, Any

import mcp
from pydantic import AnyUrl, Field
from pydantic.dataclasses import dataclass

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.message_event_result import MessageChain

from ..schemas import GenerationResult
from .image_generation import (
    PROMPT_DESCRIPTION,
    REFERENCES_DESCRIPTION,
)
from .media_generation_base import BaseMediaGenerationTool

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.tool import ToolExecResult
    from astrbot.core.message.components import BaseMessageComponent
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

    from ...main import BigBanana


def build_video_parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": PROMPT_DESCRIPTION},
            "preset_name": {
                "type": "string",
                "description": "The name of an existing video-generation preset.",
            },
            "image_references": {
                "type": "array",
                "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                "maxItems": 1,
                "description": REFERENCES_DESCRIPTION,
            },
            "quality": {"type": "string", "enum": ["speed", "quality"]},
            "size": {"type": "string"},
            "fps": {"type": "string", "enum": ["30", "60"]},
            "with_audio": {"type": "boolean"},
            "watermark_enabled": {"type": "boolean"},
        },
        "required": [],
    }


@dataclass
class BigBananaVideoGenerationTool(BaseMediaGenerationTool):
    plugin: Any = None
    name: str = "banana_video_generation"
    description: str = (
        "Generate a video from text and optionally one reference image. "
        "Use this tool for both text-to-video and image-to-video requests."
    )
    parameters: dict = Field(default_factory=build_video_parameters)
    media_name = "视频"
    generation_name = "视频生成"

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        if self.plugin is None:
            return "BigBanana 插件未初始化完成，请稍后再试。"
        plugin: BigBanana = self.plugin
        event: AstrMessageEvent = context.context.event

        access_check = plugin.whitelist_guard.check(event, is_command=False)
        if not access_check.allowed:
            logger.info(access_check.log_message)
            return access_check.message

        cooldown_check = plugin.cooldown_guard.check(event)
        if not cooldown_check.allowed:
            logger.info(cooldown_check.log_message)
            return cooldown_check.message

        prompt = kwargs.get("prompt", "")
        preset_name = kwargs.get("preset_name")
        image_references = kwargs.get("image_references", [])
        if not isinstance(prompt, str) or not isinstance(preset_name, str | None):
            return "prompt 和 preset_name 必须是字符串。"
        if not isinstance(image_references, list) or any(
            isinstance(reference, bool)
            or not isinstance(reference, str | int)
            or (isinstance(reference, str) and not reference.strip())
            for reference in image_references
        ):
            return "image_references 必须是由非空字符串或整数用户 ID 组成的列表。"
        if len(image_references) > 1:
            return "视频生成最多支持一张参考图片。"
        if any(
            isinstance(reference, str)
            and reference.strip().lower().startswith(("base64://", "data:image/"))
            for reference in image_references
        ):
            return "image_references 不接受 base64:// 或 Data URL。"

        prompt = prompt.strip()
        preset_name = preset_name.strip() if preset_name else None
        image_references = [str(reference).strip() for reference in image_references]
        params, error = self._resolve_video_params(
            plugin, prompt, preset_name, kwargs, bool(image_references)
        )
        if error:
            return error

        result = await self._submit_drawing_task(
            plugin,
            event,
            params,
            image_references=image_references,
        )
        return result or "视频生成任务启动成功。"

    @staticmethod
    def _resolve_video_params(
        plugin: BigBanana,
        prompt: str,
        preset_name: str | None,
        kwargs: dict[str, Any],
        has_reference: bool,
    ) -> tuple[dict[str, Any], str | None]:
        params: dict[str, Any] = {
            "capability": "video_generation",
            "min_images": 0,
            "max_images": 1,
        }
        prompt_config = plugin.prompt_config_manager.prompt_config
        configured_preset = plugin.llm_tools_config.llm_video_tool_preset_name.strip()
        if configured_preset:
            preset = prompt_config.get(configured_preset)
            if preset is None:
                logger.warning(
                    "[BIG BANANA] 配置的 LLM 视频工具预设不存在："
                    f"「{configured_preset}」，将使用本次调用参数"
                )
            elif preset.get("capability", "image_generation") != "video_generation":
                logger.warning(
                    "[BIG BANANA] 配置的 LLM 视频工具预设不是视频生成预设："
                    f"「{configured_preset}」，将使用本次调用参数"
                )
            else:
                params.update(preset)

        if preset_name:
            preset = prompt_config.get(preset_name)
            if preset is None:
                return {}, f"未找到视频预设提示词：「{preset_name}」。"
            if preset.get("capability", "image_generation") != "video_generation":
                return {}, f"预设提示词「{preset_name}」不是视频生成预设。"
            params.update(preset)

        preset_prompt = params.get("prompt", "")
        if "{{user_text}}" in preset_prompt:
            params["prompt"] = preset_prompt.replace("{{user_text}}", prompt)
        elif prompt:
            params["prompt"] = prompt

        for key in (
            "quality",
            "size",
            "fps",
            "with_audio",
            "watermark_enabled",
        ):
            if key in kwargs:
                params[key] = kwargs[key]

        if not params.get("prompt", "").strip() and not has_reference:
            return {}, "视频生成至少需要 prompt 或一张参考图片。"
        return params, None

    async def _generate_result(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None,
    ) -> GenerationResult:
        try:
            images, supplement_infos, error = await self._collect_images(
                plugin, event, params, image_references
            )
            if error:
                return GenerationResult(error_message=error)

            prompt = params.get("prompt", "")
            if prompt and params.get("sub_brain", plugin.sub_brain_config.tool_enabled):
                optimized_prompt = await plugin.sub_brain_optimizer.optimize_prompt(
                    event, prompt
                )
                if optimized_prompt is not None:
                    params["prompt"] = optimized_prompt

            if plugin.preference_config.enable_at_avatar_note:
                params["prompt"] = (
                    plugin.drawing_command_handler._append_image_supplement_note(
                        params.get("prompt", ""), supplement_infos
                    )
                )
            return await plugin.video_pipeline.run(params, image_list=images)
        except Exception as exc:
            logger.error(f"[BIG BANANA] LLM 工具视频生成失败: {exc}", exc_info=True)
            return GenerationResult(error_message="视频生成发生内部错误，请稍后重试。")

    @staticmethod
    def _build_callback_result_chain(
        result: GenerationResult | str,
    ) -> MessageChain:
        if isinstance(result, str):
            return MessageChain(chain=[Comp.Plain(result)])
        if result.error_message:
            return MessageChain(
                chain=[Comp.Plain(f"后台视频生成失败：{result.error_message}")]
            )
        chain: list[BaseMessageComponent] = [
            Comp.Plain("后台视频生成已完成，以下视频尚未发送给用户。")
        ]
        chain.extend(Comp.Video.fromURL(video.url) for video in result.videos)
        return MessageChain(chain=chain)

    @staticmethod
    def _build_model_tool_result(
        result: GenerationResult,
    ) -> mcp.types.CallToolResult:
        response = mcp.types.CallToolResult(content=[])
        if result.error_message:
            response.content.append(
                mcp.types.TextContent(
                    type="text",
                    text=f"视频生成失败：{result.error_message}",
                )
            )
            return response

        response.content.append(
            mcp.types.TextContent(
                type="text",
                text="视频生成已完成，但尚未发送给用户。",
            )
        )
        response.content.extend(
            mcp.types.ResourceLink(
                type="resource_link",
                name=f"generated_video_{index}",
                uri=AnyUrl(video.url),
                mimeType="video/mp4",
            )
            for index, video in enumerate(result.videos, start=1)
        )
        return response
