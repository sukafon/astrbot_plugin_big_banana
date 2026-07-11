from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext

from ..schemas import PARAMS_LIST

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.tool import ToolExecResult
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

    from ...main import BigBanana

PROMPT_TOOL_DESCRIPTION = (
    "Retrieve preset names, full prompts, and parameters for Big Banana. "
    "Call this tool if the user specifies a preset to get its full details before generating images."
)

PROMPT_TOOL_PRESET_DESCRIPTION = (
    "The exact name of the preset to retrieve."
)

PROMPT_TOOL_LIST_DESCRIPTION = (
    "Set to true to list all available preset names."
)


def build_prompt_tool_parameters() -> dict:
    """Build the preset-query tool parameter schema.

    Returns:
        The JSON Schema used for LLM tool calls.
    """
    return {
        "type": "object",
        "properties": {
            "get_preset_prompt": {
                "type": "string",
                "description": PROMPT_TOOL_PRESET_DESCRIPTION,
            },
            "get_preset_name_list": {
                "type": "boolean",
                "description": PROMPT_TOOL_LIST_DESCRIPTION,
            },
        },
        "required": [],
    }


@dataclass
class BigBananaPromptTool(FunctionTool[AstrAgentContext]):
    plugin: Any = None
    name: str = "banana_preset_prompt"
    description: str = PROMPT_TOOL_DESCRIPTION
    parameters: dict = Field(default_factory=build_prompt_tool_parameters)

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],  # type: ignore
        **kwargs,
    ) -> ToolExecResult:
        """Run a preset query and return a model-readable result.

        Args:
            context: Current AstrBot agent execution context.
            **kwargs: Arguments supplied by the model tool call.

        Returns:
            Preset details, a preset-name list, or a validation error.
        """
        if self.plugin is None:
            logger.warning("[BIG BANANA] 插件未初始化完成，无法处理请求")
            return "BigBanana 插件未初始化完成，请稍后再试。"
        plugin: BigBanana = self.plugin
        event: AstrMessageEvent = context.context.event  # type: ignore

        access_check = plugin.whitelist_guard.check(event, is_command=False)
        if not access_check.allowed:
            logger.info(access_check.log_message)
            return access_check.message

        preset_query_result = self._resolve_preset_query(plugin, kwargs)
        if preset_query_result:
            return preset_query_result

        logger.warning("[BIG BANANA] 未提供有效的预设查询参数")
        return (
            "未提供有效的预设查询参数。请填写 get_preset_prompt，"
            "或将 get_preset_name_list 设为 true。"
        )

    def _resolve_preset_query(self, plugin: BigBanana, kwargs: dict) -> str | None:
        """Resolve and validate a preset query.

        Args:
            plugin: Initialized Big Banana plugin instance.
            kwargs: Arguments supplied by the model tool call.

        Returns:
            A model-readable query result, or ``None`` when no query was given.
        """
        get_preset_name_list = kwargs.get("get_preset_name_list", False)
        get_preset_prompt = kwargs.get("get_preset_prompt", "")

        if not isinstance(get_preset_name_list, bool):
            logger.warning(
                "[BIG BANANA] get_preset_name_list 参数类型无效："
                f"{type(get_preset_name_list).__name__}"
            )
            return "get_preset_name_list 必须是 boolean 类型，请使用 true 或 false。"
        if not isinstance(get_preset_prompt, str):
            logger.warning(
                "[BIG BANANA] get_preset_prompt 参数类型无效："
                f"{type(get_preset_prompt).__name__}"
            )
            return "get_preset_prompt 必须是 string 类型，请提供有效的预设名称。"

        get_preset_prompt = get_preset_prompt.strip()
        prompt_config = plugin.prompt_config_manager.prompt_config

        if get_preset_name_list:
            preset_name_list = list(prompt_config.keys())
            if not preset_name_list:
                logger.info("[BIG BANANA] 当前没有可用的预设提示词")
                return "当前没有可用的预设提示词。"
            preset_names = "、".join(preset_name_list)
            logger.info(f"[BIG BANANA] 返回预设提示词名称列表：{preset_names}")
            return f"当前可用的预设提示词有：{preset_names}"

        if get_preset_prompt:
            if get_preset_prompt not in prompt_config:
                logger.warning(
                    f"[BIG BANANA] 未找到预设提示词：「{get_preset_prompt}」"
                )
                preset_names = "、".join(prompt_config.keys()) or "无"
                return (
                    f"未找到预设提示词：「{get_preset_prompt}」。"
                    f"可用的预设提示词有：{preset_names}"
                )
            params = prompt_config.get(get_preset_prompt, {})
            preset_prompt = params.get("prompt", "{{user_text}}")
            if preset_prompt == "{{user_text}}":
                logger.info("[BIG BANANA] 预设提示词为自定义提示词")
                prompt_details = "提示词：由用户提供文本生成图片。"
            else:
                logger.info(f"[BIG BANANA] 返回预设提示词内容: {preset_prompt[:128]}")
                prompt_details = f"提示词：\n{preset_prompt}"

            preset_params = [
                f"{key}: {params[key]}" for key in PARAMS_LIST if key in params
            ]
            if preset_params:
                prompt_details += "\n预设参数：\n" + "\n".join(preset_params)
            return f"预设提示词「{get_preset_prompt}」详情如下：\n{prompt_details}"

        return None
