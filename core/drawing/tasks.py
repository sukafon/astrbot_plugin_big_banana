from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


class DrawingTaskManager:
    """跟踪绘图任务。"""

    def __init__(self) -> None:
        """初始化任务表。"""
        self.running_tasks: dict[str, asyncio.Task] = {}
        self._tracked_tasks: set[asyncio.Task] = set()

    @staticmethod
    def build_task_id(event: AstrMessageEvent) -> str:
        """构造包含会话来源和消息 ID 的全局任务键。

        Args:
            event: 发起绘图任务的消息事件。

        Returns:
            可在不同平台和会话之间安全区分的任务键。
        """
        return f"{event.unified_msg_origin}:{event.message_obj.message_id}"

    def is_running(self, task_id: str) -> bool:
        """判断指定会话是否已有未完成的绘图任务。"""
        task = self.running_tasks.get(task_id)
        return task is not None and not task.done()

    def start(self, task_id: str, task: asyncio.Task) -> None:
        """登记一个会话的绘图任务。"""
        self.running_tasks[task_id] = task
        self._tracked_tasks.add(task)
        task.add_done_callback(self._tracked_tasks.discard)

    def finish(self, task_id: str) -> None:
        """移除会话已结束的绘图任务。"""
        self.running_tasks.pop(task_id, None)

    async def cancel_all(self) -> None:
        """取消并等待所有登记过且尚未结束的绘图任务。"""
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in self._tracked_tasks
            if task is not current_task and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.running_tasks.clear()
        self._tracked_tasks.clear()
