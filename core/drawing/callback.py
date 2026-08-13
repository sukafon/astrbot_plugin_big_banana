from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.message.message_event_result import MessageChain

    from ...main import BigBanana


class CallbackDispatcher:
    """Dispatch LLM background media results to a configured plugin."""

    def __init__(self, plugin: BigBanana) -> None:
        """Store the plugin instance used to access configuration and context.

        Args:
            plugin: The active BigBanana plugin instance.
        """
        self.plugin = plugin

    def enabled(self) -> bool:
        """Return whether a complete upstream callback target is configured.

        Returns:
            True when both plugin and method names are non-empty.
        """
        config = self.plugin.llm_tools_config
        return bool(
            config.background_callback_plugin.strip()
            and config.background_callback_method.strip()
        )

    async def dispatch(
        self,
        *,
        event: AstrMessageEvent,
        result: MessageChain,
        params: dict,
        unified_msg_origin: str,
        is_success: bool,
    ) -> bool:
        """Invoke the configured upstream completion callback.

        Args:
            event: Event that initiated the media generation tool call.
            result: Message chain for the upstream plugin to deliver to the AI.
            params: Final media generation parameters.
            unified_msg_origin: Session origin captured when the task was submitted.
            is_success: Whether the media generation was successful.

        Returns:
            True when the upstream callback completed successfully (or did not return a bool).
        """
        config = self.plugin.llm_tools_config
        plugin_name = config.background_callback_plugin.strip()
        method_name = config.background_callback_method.strip()
        metadata = self.plugin.context.get_registered_star(plugin_name)
        if metadata is None or metadata.star_cls is None:
            logger.warning(f"[BIG BANANA] 未找到回调插件：{plugin_name}")
            return False

        callback = getattr(metadata.star_cls, method_name, None)
        if not callable(callback):
            logger.warning(
                f"[BIG BANANA] 回调插件 {plugin_name} 中不存在方法：{method_name}"
            )
            return False

        try:
            quote_reply_mode = self.plugin.preference_config.quote_reply_mode
            should_quote = quote_reply_mode in ("both", "tool_only")

            callback_result = callback(
                event=event,
                result=result,
                params=params,
                unified_msg_origin=unified_msg_origin,
                is_success=is_success,
                should_quote=should_quote,
            )
            res = None
            if inspect.isasyncgen(callback_result):
                async for item in callback_result:
                    res = item
            elif inspect.isawaitable(callback_result):
                res = await callback_result
            else:
                res = callback_result
        except Exception as e:
            logger.warning(
                f"[BIG BANANA] 后台绘图回调 {plugin_name}.{method_name} 执行失败: {e}",
                exc_info=True,
            )
            return False

        logger.info(
            f"[BIG BANANA] 后台绘图结果已交给回调插件 {plugin_name}.{method_name}"
        )
        if isinstance(res, bool):
            return res
        return True
