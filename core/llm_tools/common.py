from __future__ import annotations

from astrbot.api import logger
from astrbot.api.star import Context, StarTools

TOOLS_NAMESPACE = [
    "banana_preset_prompt",
    "banana_image_generation",
    "banana_video_generation",
]


def remove_tools(context: Context):
    """从 AstrBot 上下文中移除本插件注册的 LLM 工具。"""
    func_tool = context.get_llm_tool_manager()
    for name in TOOLS_NAMESPACE:
        tool = func_tool.get_func(name)
        if tool:
            StarTools.unregister_llm_tool(name)
            logger.info(f"[BIG BANANA] 已移除 {name} 工具注册")
