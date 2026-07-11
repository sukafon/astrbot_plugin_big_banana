from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import mcp
from pydantic import AnyUrl, Field
from pydantic.dataclasses import dataclass

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.message_event_result import MessageChain

from ..drawing.collector import ImageCollector
from ..schemas import GenerationResult

if TYPE_CHECKING:
    from pathlib import Path

    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.tool import ToolExecResult
    from astrbot.core.message.components import BaseMessageComponent
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

    from ...main import BigBanana
    from ..schemas import ImageResource

TOOL_DESCRIPTION = "Draw or edit images based on text or reference images."

PROMPT_DESCRIPTION = (
    "The detailed description of the image to generate. If a preset is used, "
    "provide only the subject text for the placeholder, without repeating the preset's "
    "template. If reference images are used, refer to them in the prompt by their "
    "1-based index (e.g., 'image 1', 'image 2')."
)

PRESET_DESCRIPTION = (
    "The name of an existing preset to apply to the generation."
)

REFERENCES_DESCRIPTION = (
    "Optional list of reference image URLs, cached local image paths, or platform "
    "user IDs (for avatar references). Do not use base64 or data URLs. Reuse "
    "existing paths/URLs from the conversation history if available."
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
class BigBananaImageGenerationTool(FunctionTool[AstrAgentContext]):
    plugin: Any = None
    name: str = "banana_image_generation"
    description: str = TOOL_DESCRIPTION
    parameters: dict = Field(default_factory=build_parameters)

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
            if configured_preset_name not in prompt_config:
                logger.warning(
                    "[BIG BANANA] 配置的 LLM 工具调用预设不存在："
                    f"「{configured_preset_name}」"
                )
            else:
                params.update(prompt_config[configured_preset_name])

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

    async def _submit_drawing_task(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None = None,
    ) -> ToolExecResult | None:
        """按配置选择前台执行或插件内部后台任务。

        Args:
            plugin: 当前 Big Banana 插件实例。
            event: 发起绘图工具调用的消息事件。
            params: 本次绘图使用的参数。
            image_references: AI 明确传入的参考图片或用户 ID。

        Returns:
            任务启动状态、前台工具结果或空值。
        """
        task_id = plugin.task_manager.build_task_id(event)
        if plugin.task_manager.is_running(task_id):
            return "该任务已在执行中，请勿重复操作。"

        current_task = asyncio.current_task()
        if current_task:
            plugin.task_manager.start(task_id, current_task)

        try:
            direct_send_result = plugin.llm_tools_config.llm_tool_direct_send_result
            # 插件自行管理后台任务，避免依赖 AstrBot 核心的后台工具实现细节。
            is_background_task = plugin.llm_tools_config.llm_tool_use_background_task
            use_background_callback = (
                is_background_task and plugin.background_callback.enabled()
            )
            # 发送开始绘图的提示信息
            if plugin.preference_config.enable_llm_tool_drawing_message:
                drawing_message = plugin.preference_config.drawing_message.strip()
                if drawing_message:
                    drawing_chain: list[BaseMessageComponent] = [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(drawing_message),
                    ]
                    await event.send(event.chain_result(drawing_chain))

            # 后台结果必须有明确去向：回调上游，或由插件直接发送给用户。
            if is_background_task and (use_background_callback or direct_send_result):
                task = asyncio.create_task(
                    self._generate_and_send_result(
                        plugin,
                        event,
                        params,
                        image_references,
                        is_background_task=True,
                        use_background_callback=use_background_callback,
                        direct_send_result=direct_send_result,
                    )
                )
                plugin.task_manager.start(task_id, task)
                if not use_background_callback:
                    return (
                        "后台绘图任务已启动，完成后会直接把图片发送给用户。"
                        "请告知用户图片正在生成，不要重复调用绘图工具。"
                    )
                return (
                    "后台绘图任务已启动。请告知用户图片正在生成，不要重复调用绘图工具；"
                    "任务完成后会再次通知你处理结果。"
                )

            if is_background_task:
                logger.warning(
                    "[BIG BANANA] 后台任务未配置回调且未开启直接发送，"
                    "已改为前台执行以便把图片结果返回给模型"
                )

            # 没有异步结果接收方时在当前调用中完成，确保富媒体结果不会丢失。
            return await self._generate_and_send_result(
                plugin,
                event,
                params,
                image_references,
                is_background_task=False,
                direct_send_result=direct_send_result,
            )
        finally:
            if plugin.task_manager.running_tasks.get(task_id) is current_task:
                plugin.task_manager.finish(task_id)

    async def _generate_and_send_result(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None = None,
        *,
        is_background_task: bool = False,
        use_background_callback: bool = False,
        direct_send_result: bool = True,
    ) -> ToolExecResult | None:
        """生成图片并按是否直接发送处理结果。

        执行逻辑:
        1. 生成图片
        2. 不直接发送时，插件后台回调完整结果，其他模式返回富媒体工具结果
        3. 插件后台回调失败时，继续进入统一发送流程并主动发送图片
        4. 统一发送流程中，前台通过事件发送，后台通过主动消息发送
        5. 插件后台原本配置为直接发送时，将发送后的文字状态回调给上游插件

        Args:
            plugin: 当前 Big Banana 插件实例。
            event: 发起绘图工具调用的消息事件。
            params: 本次绘图使用的参数。
            image_references: 显式传入的参考图片或用户 ID。
            is_background_task: 是否为后台任务。
            use_background_callback: 是否由插件内部后台执行并回调上游插件。
            direct_send_result: 是否由插件直接发送生成结果。

        Returns:
            返回给模型的ToolExecResult、后台任务状态文本或空值。
        """
        group_id = event.get_group_id()
        temporary_paths: list[Path] = []
        try:
            result = await self._generate_result(
                plugin,
                event,
                params,
                image_references,
            )

            # 只有生成成功才记录冷却，失败任务仍允许用户调整参数后重试。
            if not result.error_message:
                plugin.cooldown_guard.mark_cooldown(group_id)

            # 不直接发送时，前台把富媒体结果返回给模型；
            # 插件后台则把完整结果消息链交给配置的上游回调。
            if not direct_send_result:
                # 前台任务直接构建富媒体工具结果，图片尚未发送给用户。
                if not use_background_callback:
                    return self._build_model_tool_result(result)

                # 插件后台任务，回调消息链包含文字以及尚未发送给用户的图片。
                handled = await plugin.background_callback.dispatch(
                    event=event,
                    result=self._build_callback_result_chain(result),
                    params=params,
                )
                if handled:
                    return None

                # 回调失败时不提前返回，继续进入下方统一发送流程完成降级发送。

            # 统一发送入口：直接发送任务正常进入，插件回调失败也会降级到这里。
            # 前台使用当前事件发送；插件后台使用统一会话来源主动发送。
            completion_text = await self._send_generation_result(
                plugin,
                event,
                result,
                params,
                use_proactive_send=is_background_task and (plugin.preference_config.background_task_send_type == "active"),
                temporary_paths=temporary_paths,
            )

            # 只有原本配置为直接发送的插件后台任务，才回调发送后的文字状态。
            # 不直接发送但回调失败的任务不会再次调用同一个回调。
            if use_background_callback and direct_send_result:
                handled = await plugin.background_callback.dispatch(
                    event=event,
                    result=self._build_callback_result_chain(completion_text),
                    params=params,
                )
                if handled:
                    return None

            # 前台直接发送后把文字状态返回给当前模型。
            return completion_text
        finally:
            if is_background_task:
                plugin.task_manager.finish(plugin.task_manager.build_task_id(event))
            if event.platform_meta.name == "telegram":
                for path in temporary_paths:
                    try:
                        path.unlink(missing_ok=True)
                        logger.debug(f"[BIG BANANA] 已删除当前任务缓存文件: {path}")
                    except Exception as e:
                        logger.error(f"[BIG BANANA] 删除缓存文件 {path} 失败: {e}")

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

    async def _send_generation_result(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        result: GenerationResult,
        params: dict,
        *,
        use_proactive_send: bool,
        temporary_paths: list[Path],
    ) -> str:
        """发送生成结果并返回文字完成状态。

        Args:
            plugin: 当前 Big Banana 插件实例。
            event: 发起绘图工具调用的消息事件。
            result: 本次图片生成结果。
            params: 本次绘图使用的参数。
            use_proactive_send: 是否使用统一会话来源主动发送消息。
            temporary_paths: 用于记录本次任务创建的临时文件。

        Returns:
            描述发送结果的文字状态。
        """
        try:
            if result.error_message:
                msg_chain: list[BaseMessageComponent] = [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(f"❌ 图片生成失败：{result.error_message}"),
                ]
            else:
                msg_chain = plugin.drawing_command_handler._build_result_message_chain(
                    event,
                    result=result,
                    url_only=params.get("url", plugin.params_config.url),
                    temporary_paths=temporary_paths,
                )

            if use_proactive_send:
                await plugin.context.send_message(
                    event.unified_msg_origin,
                    MessageChain(msg_chain),
                )
            else:
                await event.send(event.chain_result(msg_chain))
        except Exception as e:
            logger.error(f"[BIG BANANA] 绘图结果发送失败: {e}", exc_info=True)
            return (
                "绘图结果发送失败。请告知用户发送失败，并根据当前结果决定是否重试；"
                "不要声称图片已经发送成功。"
            )

        if result.error_message:
            return (
                f"绘图任务执行失败：{result.error_message}。失败原因已发送给用户。"
                "无需重复发送相同错误；如需重试，请先根据失败原因调整参数。"
            )
        return "图片已成功发送给用户。请勿重复发送图片，只需用简短文字确认生成完成。"

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

    async def _collect_images(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None,
    ) -> tuple[list[ImageResource], list[str], str | None]:
        """收集 LLM 工具明确指定的参考图。

        Args:
            plugin: 当前 Big Banana 插件实例。
            event: 发起绘图工具调用的消息事件。
            params: 本次绘图使用的参数。
            image_references: AI 明确传入的参考图片或用户 ID。

        Returns:
            已加载图片、图片索引补充信息和可选错误消息组成的元组。
        """
        collector = ImageCollector(
            plugin=plugin,
            event=event,
            params=params,
            is_llm_tool=True,
        )
        if image_references:
            await collector.add_explicit_references(image_references)
        if not collector.check_urls_limit():
            return (
                [],
                [],
                f"参考图片数量不足，当前仅 {len(collector.get_final_urls())} 张，"
                f"最少需要 {collector.min_images} 张。请补充 image_references 后重试。",
            )
        images = await collector.fetch_collected_images()
        if not collector.check_images_limit():
            return (
                [],
                [],
                f"可用参考图片数量不足，成功加载 {len(images)} 张，"
                f"最少需要 {collector.min_images} 张。请更换或补充 image_references 后重试。",
            )
        return images, collector.image_supplement_infos, None

    @staticmethod
    def _build_model_tool_result(
        result: GenerationResult,
    ) -> mcp.types.CallToolResult:
        """构造可交给当前模型的富媒体结果。

        Args:
            result: 本次图片生成结果。

        Returns:
            包含文字、图片或 URL 的 MCP 工具结果。
        """
        response = mcp.types.CallToolResult(content=[])
        if result.error_message:
            response.content.append(
                mcp.types.TextContent(
                    type="text",
                    text=(
                        f"绘图任务执行失败：{result.error_message}。"
                        "请根据失败原因调整参数后重试，或直接告知用户；"
                        "不要声称图片已经生成成功。"
                    ),
                )
            )
            return response

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
