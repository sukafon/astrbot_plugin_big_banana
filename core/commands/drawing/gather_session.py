from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.core.utils.session_waiter import session_waiter

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.utils.session_waiter import SessionController

    from ....main import BigBanana
    from ...drawing.collector import ImageCollector


class DrawingGatherSession:
    """用于追加文本和图片的交互式收集模式会话。"""

    def __init__(
        self,
        *,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        collector: ImageCollector,
    ) -> None:
        """初始化绘图命令组件所需的依赖和状态。"""
        self.plugin = plugin
        self.event = event
        self.params = params
        self.collector = collector
        self.cancelled = False

    async def run(self) -> AsyncGenerator[MessageEventResult, None]:
        """运行收集模式会话并等待用户追加文本或图片。"""
        yield self.event.plain_result(
            self._build_gather_message(title="绘图收集模式已启用")
        )

        @session_waiter(
            timeout=self.plugin.preference_config.gather_timeout,
            record_history_chains=False,
        )
        async def waiter(
            controller: SessionController, waiter_event: AstrMessageEvent
        ) -> None:
            """处理交互式等待期间收到的后续消息。"""
            if waiter_event.get_sender_id() != self.event.get_sender_id():
                return

            message_text = waiter_event.message_str.strip()
            if message_text == "取消":
                self.cancelled = True
                await waiter_event.send(waiter_event.plain_result("🍌 操作已取消。"))
                controller.stop()
                return

            if message_text == "开始":
                controller.stop()
                return

            # 纯文本消息已包含@的文本化，本插件的处理与之兼容
            user_params = self.plugin.prompt_config_manager.parse_prompt_params(
                message_text
            )
            # 取出并去除用户提示词
            user_prompt = user_params.pop("prompt", "")
            # 将文本拼接到prompt后面
            if user_prompt:
                self.params["prompt"] += " " + user_prompt

            # 更新参数
            self.params.update(user_params)

            # 收集消息中的图片
            await self.collector.add_msg_images(waiter_event)

            await waiter_event.send(
                waiter_event.plain_result(
                    self._build_gather_message(title="绘图追加模式已收集内容")
                )
            )
            controller.keep(
                timeout=self.plugin.preference_config.gather_timeout, reset_timeout=True
            )

        try:
            await waiter(self.event)
        except TimeoutError:
            self.cancelled = True
            yield self.event.plain_result("❌ 超时了，操作已取消！")
        except Exception as e:
            self.cancelled = True
            logger.error(f"绘图提示词追加模式出现错误: {e}", exc_info=True)
            yield self.event.plain_result("❌ 处理时发生了一个内部错误。")
        finally:
            if self.cancelled:
                self.event.stop_event()

    def _build_gather_message(self, title: str) -> str:
        """生成收集模式的当前状态提示文本。"""
        return (
            f"📝 {title}：\n"
            f"文本：{self.params['prompt']}\n"
            f"图片：{len(self.collector.images)} 张\n\n"
            f"💡 继续发送图片或文本，或者：\n"
            f"• 发送「开始」开始生成\n"
            f"• 发送「取消」取消操作\n"
            f"• {self.plugin.preference_config.gather_timeout} 秒内有效\n"
        )
