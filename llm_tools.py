from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.star import Context, StarTools
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent


@dataclass
class BigBananaTool(FunctionTool[AstrAgentContext]):
    instance: Any | None = None
    name: str = "banana_image_generation"  # 工具名称
    description: str = """This tool uses the Nano Banana Pro model for image generation.
It supports both text-based generation and image-reference generation. When a user requests
generation based on an image, you must first verify whether a valid image is present
in the user's current message or in the message they are replying to. Textual pointers
such as "that one" "the one above" or similar expressions are not acceptable as valid
image inputs. The user must provide an actual image file for the request to proceed.
In special cases, if the user says to use their avatar or mentions another user's avatar,
there is no need to explicitly provide an image. The tool will automatically fetch
the corresponding user avatar as a reference. But you must first ensure that the message
has @-mentioned the target user, or that it is using the sender's own avatar.
After getting the preset prompt, you need to perform multiple rounds of tool function
calls until the image is generated. Make decisions on your own, and do not ask
the user unless it is necessary."""  # 工具描述
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": """The image generation prompt. Refine the image generation
prompt to ensure it is clear, detailed, and accurately aligned with the user's intent.""",
                },
                "preset_name": {
                    "type": "string",
                    "description": """When filling in this parameter for the first time,
you also need to use get_preset to retrieve the full content of that preset prompt.
If your prompt is a modification based on a preset prompt, this field must retain
the original preset name so the tool can retrieve the correct generation parameters.""",
                },
                "get_preset": {
                    "type": "boolean",
                    "description": """If you do not know the specific preset name and
the user has not provided it, you may first use get_preset_name_list to retrieve the list
of preset names. Once you have obtained, set the option to True and assign the "preset_name"
parameter to that preset name. The tool will return the preset prompt's content,
allowing you to review and modify it as needed. Once you get the preset prompt and
finish modifying it, you must put the revised prompt into the prompt parameter,
and set this option to false. Then continue call the tool.""",
                },
                "get_preset_name_list": {
                    "type": "boolean",
                    "description": """If you need to get the list of preset names,
set this option to true, then the tool will return a list of all preset names,
allowing you to accurately fill the correct preset into the preset_name parameter.
After obtaining the list, you must set this option back to false. Then continue call the tool.""",
                },
                "referer_id": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": """If the user requests to use another person's avatar,
please enter the target user's ID here.""",
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],  # type: ignore
        **kwargs,
    ) -> ToolExecResult:
        if self.instance is None:
            logger.warning("BigBanana 插件未初始化完成，无法处理请求")
            return "BigBanana 插件未初始化完成，请稍后再试。"
        astr_agent_ctx = context.context  # type: ignore
        event: AstrMessageEvent = astr_agent_ctx.event

        # 获取参数
        prompt = kwargs.get("prompt", "")
        preset_name = kwargs.get("preset_name", "")
        get_preset = kwargs.get("get_preset", False)
        get_preset_name_list = kwargs.get("get_preset_name_list", False)
        referer_id = kwargs.get("referer_id", [])
        logger.debug(
            {
                "prompt": prompt[:60],
                "preset_name": preset_name,
                "get_preset": get_preset,
                "get_preset_name_list": get_preset_name_list,
                "referer_id": referer_id,
            }
        )

        # 群白名单判断
        if (
            self.instance.group_whitelist_enabled
            and event.unified_msg_origin not in self.instance.group_whitelist
        ):
            logger.info(f"群 {event.unified_msg_origin} 不在白名单内，跳过处理")
            return "当前群不在白名单内，无法使用图片生成功能。"

        # 用户白名单判断
        if (
            self.instance.user_whitelist_enabled
            and event.get_sender_id() not in self.instance.user_whitelist
        ):
            logger.info(f"用户 {event.get_sender_id()} 不在白名单内，跳过处理")
            return "该用户不在白名单内，无法使用图片生成功能。"

        # 返回预设名称列表
        if get_preset_name_list:
            preset_name_list = list(self.instance.prompt_dict.keys())
            if not preset_name_list:
                return "当前没有可用的预设提示词。"
            preset_names = "\n".join(f"- {name}" for name in preset_name_list)
            return f"当前可用的预设提示词有：\n{preset_names}"

        # 返回预设提示词内容
        if get_preset:
            if preset_name not in self.instance.prompt_dict:
                logger.warning(f"未找到预设提示词：「{preset_name}」")
                return f"未找到预设提示词：「{preset_name}」。可用的预设提示词有：{', '.join(self.instance.prompt_dict.keys())}"
            params = self.instance.prompt_dict.get(preset_name, {})
            preset_prompt = params.get("prompt", "{{user_text}}")
            if preset_prompt == "{{user_text}}":
                return "该提示词属于自定义提示词，由用户提供文本生成图片。"
            return preset_prompt

        if not prompt:
            return "prompt 参数不能为空，请提供有效的提示词。"

        params = {}
        if preset_name:
            if preset_name not in self.instance.prompt_dict:
                logger.warning(f"未找到预设提示词：「{preset_name}」")
                return f"未找到预设提示词：「{preset_name}」，请使用有效的预设名称。"
            else:
                params = self.instance.prompt_dict.get(preset_name, {})
                preset_prompt = params.get("prompt", "{{user_text}}")

        if referer_id and event.platform_meta.name != "aiocqhttp":
            return "referer_id 参数仅兼容 aiocqhttp 平台。"

        logger.info(f"生成图片提示词: {prompt[:128]}...")
        msg_chain: list[
            BaseMessageComponent
        ] = await self.instance._dispatch_generate_image(
            event, params, prompt, is_llm_tool=True, referer_id=referer_id
        )
        if any(isinstance(msg, Comp.Image) for msg in msg_chain):
            await event.send(MessageChain(msg_chain))
            return "图片已发送，停止调用此工具。"
        else:
            for msg in msg_chain:
                if isinstance(msg, Comp.Plain):
                    return msg.text
            return "图片生成失败，请稍后再试。"


def remove_tools(context: Context):
    func_tool = context.get_llm_tool_manager()
    tool = func_tool.get_func("banana_image_generation")
    if tool:
        StarTools.unregister_llm_tool("banana_image_generation")
        logger.info("已移除 banana_image_generation 工具注册")
