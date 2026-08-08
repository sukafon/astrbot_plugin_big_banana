from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


from ..utils import get_message_id


class DrawingTaskManager:
    """跟踪绘图任务。"""

    def __init__(self) -> None:
        """初始化任务表。"""
        self.running_tasks: dict[str, asyncio.Task] = {}
        self._tracked_tasks: set[asyncio.Task] = set()
        self.session_llm_tasks: dict[str, set[asyncio.Task]] = {}

    @staticmethod
    def build_task_id(event: AstrMessageEvent) -> str:
        """构造包含会话来源和消息 ID 的全局任务键。

        Args:
            event: 发起绘图任务的消息事件。

        Returns:
            可在不同平台和会话之间安全区分的任务键。
        """
        message_id = get_message_id(event)
        if message_id is None:
            # LLM 工具调用等场景可能只有虚拟事件，没有对应的原始消息对象。
            # 使用事件对象身份作为回退值，既避免属性错误，也不会让同一会话中的
            # 不同事件共用同一个任务 ID。
            message_id = f"event-{id(event)}"
        return f"{event.unified_msg_origin}:{message_id}"

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

    def get_session_llm_task_count(self, session_id: str) -> int:
        """获取指定会话中正在运行的 LLM 工具后台任务数。"""
        tasks = self.session_llm_tasks.get(session_id)
        if not tasks:
            if tasks is not None:
                self.session_llm_tasks.pop(session_id, None)
            return 0
        running = [task for task in list(tasks) if not task.done()]
        if not running:
            self.session_llm_tasks.pop(session_id, None)
            return 0
        if len(running) != len(tasks):
            self.session_llm_tasks[session_id] = set(running)
        return len(running)
    def start_llm_task(self, session_id: str, task: asyncio.Task) -> None:
        """登记指定会话的 LLM 工具后台任务。"""
        if session_id not in self.session_llm_tasks:
            self.session_llm_tasks[session_id] = set()
        self.session_llm_tasks[session_id].add(task)
        # 仅负责会话级别 LLM 任务的登记与清理，通用任务追踪由上层 start(...) 负责
        task.add_done_callback(lambda _t: self.finish_llm_task(session_id, task))

    def finish_llm_task(self, session_id: str, task: asyncio.Task) -> None:
        """移除指定会话已结束的 LLM 工具后台任务。"""
        tasks = self.session_llm_tasks.get(session_id)
        if tasks is not None:
            tasks.discard(task)
            if not tasks:
                self.session_llm_tasks.pop(session_id, None)

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
        self.session_llm_tasks.clear()
