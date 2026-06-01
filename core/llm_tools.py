from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

# from mcp.types import CallToolResult, ContentBlock, ImageContent
from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.star import Context, StarTools
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .utils import clear_cache

TOOLS_NAMESPACE = ["banana_preset_prompt", "banana_image_generation"]

if TYPE_CHECKING:
    from ..main import BigBanana


@dataclass
class BigBananaPromptTool(FunctionTool[AstrAgentContext]):
    plugin: Any = None
    name: str = "banana_preset_prompt"  # 工具名称
    # fmt: off
    description: str = (
"This is a helper tool for the banana_image_generation tool."
"It is used to retrieve preset prompts so that you can reference and refine them before"
"passing the final prompt to the banana_image_generation tool for image generation."
)  # 工具描述
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "get_preset_prompt": {
                    "type": "string",
                    "description": ("If the user requests an image generated with a specific"
"preset, you must ask for the exact preset name. Once provided, set this parameter to that name."
"The tool will then return the full preset prompt, allowing you to review and refine it as"
"needed before passing the final version to banana_image_generation."),
                },
                "get_preset_name_list": {
                    "type": "boolean",
                    "description": ("Set this parameter to true only when you need to retrieve"
"the full list of available preset names. After obtaining the list, you can set the name you want"
"to inspect in the get_preset_prompt parameter to retrieve its corresponding preset prompt."),
                },
            },
            "required": [],
        }
    )
    # fmt: on
    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],  # type: ignore
        **kwargs,
    ) -> ToolExecResult:
        if self.plugin is None:
            logger.warning("[BIG BANANA] 插件未初始化完成，无法处理请求")
            return "BigBanana 插件未初始化完成，请稍后再试。"
        plugin: BigBanana = self.plugin
        event: AstrMessageEvent = context.context.event  # type: ignore

        # 获取参数
        get_preset_prompt = kwargs.get("get_preset_prompt", "")
        get_preset_name_list = kwargs.get("get_preset_name_list", False)

        # 群白名单判断
        if (
            plugin.group_whitelist_enabled
            and event.unified_msg_origin not in plugin.group_whitelist
        ):
            logger.info(
                f"[BIG BANANA] 群 {event.unified_msg_origin} 不在白名单内，跳过处理"
            )
            return "当前群不在白名单内，无法使用图片生成功能。"

        # 用户白名单判断
        if (
            plugin.user_whitelist_enabled
            and event.get_sender_id() not in plugin.user_whitelist
        ):
            logger.info(
                f"[BIG BANANA] 用户 {event.get_sender_id()} 不在白名单内，跳过处理"
            )
            return "该用户不在白名单内，无法使用图片生成功能。"

        # 返回预设名称列表
        if get_preset_name_list:
            preset_name_list = list(plugin.prompt_dict.keys())
            if not preset_name_list:
                logger.info("[BIG BANANA] 当前没有可用的预设提示词")
                return "当前没有可用的预设提示词。"
            preset_names = "、".join(preset_name_list)
            logger.info(f"[BIG BANANA] 返回预设提示词名称列表：{preset_names}")
            return f"当前可用的预设提示词有：{preset_names}"

        # 返回预设提示词内容
        if get_preset_prompt:
            if get_preset_prompt not in plugin.prompt_dict:
                logger.warning(
                    f"[BIG BANANA] 未找到预设提示词：「{get_preset_prompt}」"
                )
                return f"未找到预设提示词：「{get_preset_prompt}」。可用的预设提示词有：{', '.join(plugin.prompt_dict.keys())}"
            params = plugin.prompt_dict.get(get_preset_prompt, {})
            preset_prompt = params.get("prompt", "{{user_text}}")
            if preset_prompt == "{{user_text}}":
                logger.info("[BIG BANANA] 预设提示词为自定义提示词")
                return "该提示词属于自定义提示词，由用户提供文本生成图片。"
            logger.info(f"[BIG BANANA] 返回预设提示词内容: {preset_prompt[:128]}")
            return f"预设提示词「{get_preset_prompt}」内容如下：\n{preset_prompt}"
        logger.warning("[BIG BANANA] get_preset_prompt 参数不能为空")
        return "get_preset_prompt 参数不能为空，请提供有效的预设名称。"


@dataclass
class BigBananaTool(FunctionTool[AstrAgentContext]):
    plugin: Any = None
    name: str = "banana_image_generation"  # 工具名称
    # fmt: off
    description: str = (
"This tool uses the Nano Banana Pro model for image generation."
"It supports both text-based generation and image-reference generation. When a user requests"
"generation based on an image, you must first verify whether a valid image is present"
"in the user's current message or in the message they are replying to. Textual pointers"
'such as "that one" "the one above" or similar expressions are not acceptable as valid'
"image inputs. The user must provide an actual image file for the request to proceed."
"In special cases, if the user says to use their avatar or mentions another user's avatar,"
"there is no need to explicitly provide an image. The tool will automatically fetch"
"the corresponding user avatar as a reference. But you must first ensure that the message"
"has @-mentioned the target user, or that it is using the sender's own avatar."
"Prioritize the tool response as the highest priority event,"
"taking precedence over chat history.")  # 工具描述
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": ("Refine the image generation prompt to ensure it is clear,"
"detailed, and accurately aligned with the user's intent by elaborating on the visual elements"
"in a logical sequence that explicitly describes specific physical actions, nuanced facial"
"expressions, and the overall color scheme with lighting atmosphere. This parameter must be"
"populated with the full, descriptive prompt content rather than just a preset name,"
"even if derived from one, to guarantee the generation of a vivid and strictly defined image."),
                },
                "preset_name": {
                    "type": "string",
                    "description": ("When filling in this parameter for the first time,"
"you also need to use banana_preset_prompt tool to retrieve the full content of"
"that preset prompt. If your prompt is a modification based on a preset prompt,"
"this field must retain the original preset name so the tool can retrieve"
"the correct generation parameters."),
                },
                "referer_id": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": ("If the user requests to use another person's avatar,"
"please enter the target user's ID here. Pass this parameter together with the prompt parameter."),
                },
            },
            "required": ["prompt"],
        }
    )
    # fmt: on
    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],  # type: ignore
        **kwargs,
    ) -> ToolExecResult:
        if self.plugin is None:
            logger.warning("[BIG BANANA] 插件未初始化完成，无法处理请求")
            return "BigBanana 插件未初始化完成，请稍后再试。"
        plugin: BigBanana = self.plugin
        event: AstrMessageEvent = context.context.event  # type: ignore

        # 获取参数
        prompt = kwargs.get("prompt", "anything")
        preset_name = kwargs.get("preset_name", None)
        referer_id = kwargs.get("referer_id", [])

        # 群白名单判断
        if (
            plugin.group_whitelist_enabled
            and event.unified_msg_origin not in plugin.group_whitelist
        ):
            logger.info(
                f"[BIG BANANA] 群 {event.unified_msg_origin} 不在白名单内，跳过处理"
            )
            return "当前群不在白名单内，无法使用图片生成功能。"

        # 用户白名单判断
        if (
            plugin.user_whitelist_enabled
            and event.get_sender_id() not in plugin.user_whitelist
        ):
            logger.info(
                f"[BIG BANANA] 用户 {event.get_sender_id()} 不在白名单内，跳过处理"
            )
            return "该用户不在白名单内，无法使用图片生成功能。"

        # 必须提供 prompt 或 preset_name 参数
        if not prompt and not preset_name:
            logger.warning("[BIG BANANA] prompt 参数不能为空")
            return "prompt 参数不能为空，请提供有效的提示词。"

        params = {}
        if preset_name:
            if preset_name not in plugin.prompt_dict:
                logger.warning(f"[BIG BANANA] 未找到预设提示词：「{preset_name}」")
                return f"未找到预设提示词：「{preset_name}」，请使用有效的预设名称。"
            else:
                params = plugin.prompt_dict.get(preset_name, {})
        if prompt:
            params["prompt"] = prompt
        if "{{user_text}}" in prompt:
            logger.warning("[BIG BANANA] 提示词中包含未替换的占位符 {{user_text}}")
            return (
                "提示词中包含未替换的占位符 {{user_text}}，请将其替换为用户提供的文本。"
            )

        if referer_id and event.platform_meta.name != "aiocqhttp":
            logger.warning(
                "[BIG BANANA] referer_id 参数仅兼容 aiocqhttp 平台，当前消息平台不支持该参数。"
            )
            return "referer_id 参数仅兼容 aiocqhttp 平台，当前消息平台不支持该参数。"

        logger.info(f"[BIG BANANA] 生成图片提示词: {prompt[:128]}")

        # 创建后台任务
        task = asyncio.create_task(
            plugin.job(event, params, referer_id=referer_id, is_llm_tool=True)
        )
        task_id = event.message_obj.message_id
        plugin.running_tasks[task_id] = task
        try:
            results, err_msg, result_urls = await task
            if err_msg:
                return err_msg or "图片生成失败，未返回任何结果。"

            # 组装消息链
            msg_chain: list[BaseMessageComponent] = plugin.build_message_chain(
                event,
                results or [],
                result_urls=result_urls,
                url_only=bool(params.get("url", False)),
            )
            await event.send(MessageChain(chain=msg_chain))
            # 告知模型图片已发送
            logger.info("[BIG BANANA] 图片生成成功，已直接发送给用户")
            return (
                "图片生成完成，已发送给用户。请直接回复用户消息，禁止重复调用函数工具。"
            )
        except asyncio.CancelledError:
            logger.info(f"[BIG BANANA] {task_id} 任务被取消")
            return "图片生成任务被取消"
        finally:
            plugin.running_tasks.pop(task_id, None)
            # 目前只有 telegram 平台需要清理缓存
            if event.platform_meta.name == "telegram":
                clear_cache(plugin.temp_dir)

        # 暂时不采用Astr的返回方法，改用手动发送，实现原理是一样的。
        # # 构建返回结果，Agent代码似乎只会取content的第一个元素
        # contents: list[ContentBlock] = []
        # for mime, b64_data in results:
        #     contents.append(
        #         ImageContent(
        #             type="image",
        #             data=b64_data,
        #             mimeType=mime,
        #         )
        #     )
        # logger.info("[BIG BANANA] 图片生成成功，返回图片内容")
        # return CallToolResult(content=contents)


def remove_tools(context: Context):
    func_tool = context.get_llm_tool_manager()
    for name in TOOLS_NAMESPACE:
        tool = func_tool.get_func(name)
        if tool:
            StarTools.unregister_llm_tool(name)
            logger.info(f"[BIG BANANA] 已移除 {name} 工具注册")
