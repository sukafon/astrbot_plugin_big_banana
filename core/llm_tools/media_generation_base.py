from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.message_event_result import MessageChain

from ..drawing.collector import ImageCollector
from ..schemas import GenerationResult

if TYPE_CHECKING:
    from astrbot.core.agent.tool import ToolExecResult
    from astrbot.core.message.components import BaseMessageComponent
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

    from ...main import BigBanana
    from ..schemas import ImageResource


class BaseMediaGenerationTool(FunctionTool[AstrAgentContext], ABC):
    """Shared task lifecycle for image and video generation tools."""

    plugin: Any = None
    media_name = "媒体"
    generation_name = "生成"

    async def _submit_drawing_task(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None = None,
    ) -> ToolExecResult | None:
        task_id = plugin.task_manager.build_task_id(event)
        unified_msg_origin = event.unified_msg_origin
        if plugin.task_manager.is_running(task_id):
            return "该任务已在执行中，请勿重复操作。"

        current_task = asyncio.current_task()
        if current_task:
            plugin.task_manager.start(task_id, current_task)

        try:
            direct_send_result = plugin.llm_tools_config.llm_tool_direct_send_result
            is_background_task = plugin.llm_tools_config.llm_tool_use_background_task
            use_background_callback = (
                is_background_task and plugin.background_callback.enabled()
            )
            if plugin.preference_config.enable_llm_tool_drawing_message:
                drawing_message = (
                    plugin.preference_config.video_generation_message
                    if self.media_name == "视频"
                    else plugin.preference_config.drawing_message
                ).strip()
                if drawing_message:
                    drawing_chain: list[BaseMessageComponent] = [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(drawing_message),
                    ]
                    await event.send(event.chain_result(drawing_chain))

            if is_background_task and (use_background_callback or direct_send_result):
                task = asyncio.create_task(
                    self._generate_and_send_result(
                        plugin,
                        event,
                        params,
                        image_references,
                        unified_msg_origin=unified_msg_origin,
                        is_background_task=True,
                        use_background_callback=use_background_callback,
                        direct_send_result=direct_send_result,
                    )
                )
                plugin.task_manager.start(task_id, task)
                if not use_background_callback:
                    return (
                        f"后台{self.generation_name}任务已启动，完成后会直接把"
                        f"{self.media_name}发送给用户。请告知用户{self.media_name}正在生成，"
                        f"不要重复调用{self.generation_name}工具。"
                    )
                return (
                    f"后台{self.generation_name}任务已启动。请告知用户"
                    f"{self.media_name}正在生成，不要重复调用{self.generation_name}工具；"
                    "任务完成后会再次通知你处理结果。"
                )

            if is_background_task:
                logger.warning(
                    "[BIG BANANA] 后台任务未配置回调且未开启直接发送，"
                    f"已改为前台执行以便把{self.media_name}结果返回给模型"
                )

            return await self._generate_and_send_result(
                plugin,
                event,
                params,
                image_references,
                unified_msg_origin=unified_msg_origin,
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
        unified_msg_origin: str,
        is_background_task: bool = False,
        use_background_callback: bool = False,
        direct_send_result: bool = True,
    ) -> ToolExecResult | None:
        temporary_paths: list[Path] = []
        try:
            result = await self._generate_result(
                plugin,
                event,
                params,
                image_references,
            )
            if not result.error_message:
                plugin.cooldown_guard.mark_cooldown(event.get_group_id())

            if not direct_send_result:
                if not use_background_callback:
                    return self._build_model_tool_result(result)

                handled = await plugin.background_callback.dispatch(
                    event=event,
                    result=self._build_callback_result_chain(result),
                    params=params,
                    unified_msg_origin=unified_msg_origin,
                    is_success=not result.error_message,
                )
                if handled:
                    return None

            completion_text = await self._send_generation_result(
                plugin,
                event,
                result,
                params,
                use_proactive_send=is_background_task
                and (plugin.preference_config.background_task_send_type == "active"),
                temporary_paths=temporary_paths,
            )

            if use_background_callback and direct_send_result:
                handled = await plugin.background_callback.dispatch(
                    event=event,
                    result=self._build_callback_result_chain(completion_text),
                    params=params,
                    unified_msg_origin=unified_msg_origin,
                    is_success=not result.error_message,
                )
                if handled:
                    return None
            return completion_text
        finally:
            if is_background_task:
                plugin.task_manager.finish(plugin.task_manager.build_task_id(event))
            if event.platform_meta.name == "telegram":
                for path in temporary_paths:
                    try:
                        path.unlink(missing_ok=True)
                        logger.debug(f"[BIG BANANA] 已删除当前任务缓存文件: {path}")
                    except Exception as exc:
                        logger.error(f"[BIG BANANA] 删除缓存文件 {path} 失败: {exc}")

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
        try:
            if result.error_message:
                msg_chain: list[BaseMessageComponent] = [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(f"❌ {self.media_name}生成失败：{result.error_message}"),
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
        except Exception as exc:
            logger.error(
                f"[BIG BANANA] {self.generation_name}结果发送失败: {exc}",
                exc_info=True,
            )
            return (
                f"{self.generation_name}结果发送失败。请告知用户发送失败，"
                f"并根据当前结果决定是否重试；不要声称{self.media_name}已经发送成功。"
            )

        if result.error_message:
            return (
                f"{self.generation_name}任务执行失败：{result.error_message}。"
                "失败原因已发送给用户。"
                "无需重复发送相同错误；如需重试，请先根据失败原因调整参数。"
            )
        return (
            f"{self.media_name}已成功发送给用户。请勿重复发送{self.media_name}，"
            "只需用简短文字确认生成完成。"
        )

    async def _collect_images(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None,
    ) -> tuple[list[ImageResource], list[str], str | None]:
        collector = ImageCollector(
            plugin=plugin,
            event=event,
            params=params,
            is_llm_tool=True,
        )
        # 提示词或全局默认参数中的固定参考图对 LLM 工具同样生效。
        await collector.add_refer_images()
        if image_references:
            await collector.add_explicit_references(image_references)
        if collector.reference_failures:
            return [], [], "\n".join(collector.reference_failures)

        if not collector.check_images_limit():
            return (
                [],
                [],
                f"可用参考图片数量不足，成功加载 {len(collector.images)} 张，"
                f"最少需要 {collector.min_images} 张。请更换或补充 image_references 后重试。",
            )
        return collector.images, collector.image_supplement_infos, None

    @abstractmethod
    async def _generate_result(
        self,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        image_references: list[str] | None,
    ) -> GenerationResult: ...

    @staticmethod
    @abstractmethod
    def _build_callback_result_chain(
        result: GenerationResult | str,
    ) -> MessageChain: ...

    @staticmethod
    @abstractmethod
    def _build_model_tool_result(result: GenerationResult) -> ToolExecResult: ...
