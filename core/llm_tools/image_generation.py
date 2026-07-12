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
from .media_generation_base import BaseMediaGenerationTool

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.tool import ToolExecResult
    from astrbot.core.message.components import BaseMessageComponent
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

    from ...main import BigBanana

TOOL_DESCRIPTION = "Draw or edit images based on text or reference images."

PROMPT_DESCRIPTION = (
    "The detailed description of the image to generate. If a preset is used, "
    "provide only the subject text for the placeholder, without repeating the preset's "
    "template. If reference images are used, refer to them in the prompt by their "
    "1-based index (e.g., 'image 1', 'image 2')."
)

PRESET_DESCRIPTION = "The name of an existing preset to apply to the generation."

REFERENCES_DESCRIPTION = (
    "Optional list of reference image URLs, cached local image paths, or platform "
    "user IDs prefixed with '@' (e.g., '@123456', '@username') for avatar references. "
    "Do not use base64 or data URLs. Reuse existing paths/URLs from the conversation "
    "history if available."
)


def build_parameters() -> dict:
    """构造统一图片生成工具的参数 schema。

    Returns:
        支持提示词、预设名称和参考图片的 JSON Schema。
    """
    return {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": PROMPT_DESCRIPTION,
            },
            "preset_name": {
                "type": "string",
                "description": PRESET_DESCRIPTION,
            },
            "image_references": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "integer"},
                    ]
                },
                "description": REFERENCES_DESCRIPTION,
            },
        },
        "required": [],
    }


@dataclass
class BigBananaImageGenerationTool(BaseMediaGenerationTool):
    plugin: Any = None
    name: str = "banana_image_generation"
    description: str = TOOL_DESCRIPTION
    parameters: dict = Field(default_factory=build_parameters)
    media_name = "图片"
    generation_name = "绘图"

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        """执行图片生成工具调用并返回模型可读的工具结果。

        Args:
            context: 当前 AstrBot Agent 运行上下文。
            **kwargs: 工具调用传入的提示词、预设名称和参考图片。

        Returns:
            绘图任务状态或可供模型处理的富媒体工具结果。
        """
        if self.plugin is None:
            logger.warning("[BIG BANANA] 插件未初始化完成，无法处理请求")
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
        if prompt is None:
            prompt = ""
        if not isinstance(prompt, str) or not isinstance(preset_name, str | None):
            logger.warning("[BIG BANANA] 绘图工具的 prompt 或 preset_name 参数类型无效")
            return "prompt 和 preset_name 必须是字符串。"

        image_references = kwargs.get("image_references", [])
        if image_references is None:
            image_references = []
        if not isinstance(image_references, list) or any(
            isinstance(reference, bool)
            or not isinstance(reference, str | int)
            or (isinstance(reference, str) and not reference.strip())
            for reference in image_references
        ):
            logger.warning("[BIG BANANA] 绘图工具的 image_references 参数类型无效")
            return "image_references 必须是由非空字符串或整数用户 ID 组成的列表。"
        if any(
            isinstance(reference, str)
            and reference.strip().lower().startswith(("base64://", "data:image/"))
            for reference in image_references
        ):
            logger.warning("[BIG BANANA] 绘图工具拒绝接收内联 base64 图片")
            return (
                "image_references 不接受 base64:// 或 Data URL。"
                "请使用聊天记录中已有的 AstrBot 缓存路径或图片 URL。"
            )

        prompt = prompt.strip()
        preset_name = preset_name.strip() if preset_name else None
        image_references = [str(reference).strip() for reference in image_references]
        if not prompt:
            if preset_name:
                logger.info(
                    f"[BIG BANANA] 未提供 prompt，直接使用预设提示词：{preset_name}"
                )
            elif plugin.llm_tools_config.llm_tool_preset_name.strip():
                logger.info("[BIG BANANA] 未提供 prompt，使用配置的 LLM 工具调用预设")
            else:
                logger.warning("[BIG BANANA] 未提供 prompt 或 preset_name")
                return (
                    "必须传递 prompt 参数以生成图片；若只提供 preset_name，"
                    "将直接使用该预设提示词。"
                )

        params, error = self._resolve_params(plugin, prompt, preset_name)
        if error:
            return error
        if not params:
            logger.warning("[BIG BANANA] 解析后的绘图参数为空")
            return "解析后的绘图参数为空，请检查提示词和预设名称是否有效。"
        if params.get("capability", "image_generation") != "image_generation":
            logger.warning("[BIG BANANA] 图片生成工具拒绝执行非图片预设")
            return (
                "banana_image_generation 只支持图片预设，请通过视频生成命令执行该预设。"
            )

        logger.info(f"[BIG BANANA] 生成图片提示词: {params.get('prompt', '')[:128]}")
        result = await self._submit_drawing_task(
            plugin,
            event,
            params,
            image_references=image_references,
        )
        return result or "绘图任务启动成功。"

    def _resolve_params(
        self, plugin: BigBanana, prompt: str, preset_name: str | None
    ) -> tuple[dict | None, str | None]:
        """按优先级差分合并配置预设、调用预设和本次提示词。

        Args:
            plugin: 当前 Big Banana 插件实例。
            prompt: LLM 本次调用传入的提示词。
            preset_name: LLM 本次调用指定的预设触发词。

        Returns:
            合并后的绘图参数和可选错误消息。
        """
        params: dict = {}
        prompt_config = plugin.prompt_config_manager.prompt_config
        configured_preset_name = plugin.llm_tools_config.llm_tool_preset_name.strip()
        # 读取预设配置
        if configured_preset_name:
            configured_preset = prompt_config.get(configured_preset_name)
            if configured_preset is None:
                logger.warning(
                    "[BIG BANANA] 配置的 LLM 工具调用预设不存在："
                    f"「{configured_preset_name}」，将降级使用「llm_default」"
                )
                configured_preset = prompt_config.get("llm_default")
            if configured_preset is not None:
                params.update(configured_preset)

        # 读取本次调用指定的预设，差分覆盖预设配置
        if preset_name:
            if preset_name not in prompt_config:
                logger.warning(f"[BIG BANANA] 未找到预设提示词：「{preset_name}」")
                return (
                    None,
                    f"未找到预设提示词：「{preset_name}」，请使用有效的预设名称。",
                )
            params.update(prompt_config[preset_name])

        # 将本次调用的提示词应用到完成差分合并后的预设模板。
        preset_prompt = params.get("prompt", "")
        if "{{user_text}}" in preset_prompt:
            if not prompt:
                error = "提示词中包含未替换的占位符 {{user_text}}，请填写有效的prompt"
                logger.warning(f"[BIG BANANA] {error}")
                return None, error
            params["prompt"] = preset_prompt.replace("{{user_text}}", prompt)
        elif prompt:
            params["prompt"] = prompt

        return params, None

    async def _generate_result(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None,
    ) -> GenerationResult:
        """执行 LLM 工具的图片生成阶段。

        Args:
            plugin: 当前 Big Banana 插件实例。
            event: 发起绘图工具调用的消息事件。
            params: 本次绘图使用的参数。
            image_references: AI 明确传入的参考图片或用户 ID。

        Returns:
            图片生成结果，内部异常会转换为带错误消息的结果。
        """
        try:
            (
                collected_images,
                image_supplement_infos,
                collect_err,
            ) = await self._collect_images(plugin, event, params, image_references)
            if collect_err:
                return GenerationResult(error_message=collect_err)

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
                        params.get("prompt", ""), image_supplement_infos
                    )
                )

            return await plugin.drawing_pipeline.run(
                params,
                image_list=collected_images,
            )
        except Exception as e:
            logger.error(f"[BIG BANANA] LLM 工具绘图执行失败: {e}", exc_info=True)
            return GenerationResult(error_message="图片生成发生内部错误，请稍后重试。")

    @staticmethod
    def _build_callback_result_chain(
        result: GenerationResult | str,
    ) -> MessageChain:
        """将生成结果或发送状态包装为供上游交给 AI 的消息链。

        Args:
            result: 尚未发送的图片生成结果，或图片发送后的文字状态。

        Returns:
            包含文本和可选图片组件的消息链。
        """
        if isinstance(result, str):
            status_chain: list[BaseMessageComponent] = [Comp.Plain(result)]
            return MessageChain(chain=status_chain)

        if result.error_message:
            error_chain: list[BaseMessageComponent] = [
                Comp.Plain(
                    f"后台绘图任务执行失败：{result.error_message}。"
                    "请根据失败原因决定是否调整参数重试或告知用户，"
                    "不要声称图片已经生成成功。"
                )
            ]
            return MessageChain(chain=error_chain)

        chain: list[BaseMessageComponent] = [
            Comp.Plain(
                "后台绘图任务已完成，以下图片尚未发送给用户。"
                "请检查结果并决定是否发送、重新生成或执行其他操作。"
            )
        ]
        images_with_bytes = [image for image in result.images if image.bytes]
        chain.extend(Comp.Image.fromBase64(image.base64) for image in images_with_bytes)
        if result.urls:
            if images_with_bytes:
                chain.append(Comp.Plain("可用图片 URL：\n" + "\n".join(result.urls)))
            else:
                chain.extend(Comp.Image.fromURL(url) for url in result.urls)
        if len(chain) == 1:
            chain[0] = Comp.Plain(
                "后台绘图任务已完成，但结果中没有可供查看或发送的图片。"
                "请如实告知用户，或决定是否重新生成。"
            )
        return MessageChain(chain=chain)

    @staticmethod
    def _build_model_tool_result(
        result: GenerationResult,
    ) -> ToolExecResult:
        """构造可交给当前模型的富媒体结果。

        Args:
            result: 本次图片生成结果。

        Returns:
            包含文字、图片或 URL 的 MCP 工具结果。
        """
        if result.error_message:
            return f"绘图任务执行失败：{result.error_message}"

        response = mcp.types.CallToolResult(content=[])
        response.content.append(
            mcp.types.TextContent(
                type="text",
                text=(
                    "图片生成已完成，但尚未发送给用户。"
                    "请检查以下图片并决定是否发送、重新生成或执行其他操作。"
                ),
            )
        )
        images_with_bytes = [image for image in result.images if image.bytes]
        response.content.extend(
            mcp.types.ImageContent(
                type="image",
                data=image.base64,
                mimeType=image.mime,
            )
            for image in images_with_bytes
        )
        if result.urls:
            urls_text = "\n".join(result.urls)
            if not images_with_bytes:
                # 仅在没有图片数据时保留结构化链接，避免模型收到重复图片。
                response.content.extend(
                    mcp.types.ResourceLink(
                        type="resource_link",
                        name=f"generated_image_{index}",
                        uri=AnyUrl(url),
                        mimeType="image/*",
                    )
                    for index, url in enumerate(result.urls, start=1)
                )
            response.content.append(
                mcp.types.TextContent(
                    type="text",
                    text=(
                        "可用图片 URL：\n"
                        f"{urls_text}\n"
                        "如需发送，请使用 send_message_to_user。"
                    ),
                )
            )
        if len(response.content) == 1:
            response.content[0] = mcp.types.TextContent(
                type="text",
                text=(
                    "绘图任务已完成，但没有可供查看或发送的图片数据或 URL。"
                    "请如实告知用户，或决定是否重新生成。"
                ),
            )
        return response
