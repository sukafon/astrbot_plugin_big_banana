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

TOOLS_NAMESPACE = [
    "banana_preset_prompt",
    "banana_image_generation_with_reference",
    "banana_image_generation_with_avatar",
]

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
class BigBananaReferenceTool(FunctionTool[AstrAgentContext]):
    plugin: Any = None
    name: str = "banana_image_generation_with_reference"  # 工具名称
    # fmt: off
    description: str = (
"Generate images from text prompts or reference chat images. "
"If the user wants to draw/edit based on an image, verify that a real image file is present in the current or replied message. "
"Textual references like 'that image' are invalid. "
"Do NOT use this tool for avatar-based interactions (use banana_image_generation_with_avatar instead).")  # 工具描述
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": ("Detailed image description. Refine user input with explicit physical actions, "
"facial expressions, background elements, and lighting atmosphere. "
"If the user hasn't specified an art style, you may optionally choose one matching your persona."),
                },
                "preset_name": {
                    "type": "string",
                    "description": ("Use to retrieve preset prompts via banana_preset_prompt. "
"Keep original preset name if modifying based on a preset."),
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

        # 冷却时间判断
        group_id = event.get_group_id()
        cooldown_seconds = getattr(plugin.preference_config, "group_cooldown", 0)
        if group_id and cooldown_seconds > 0:
            import time

            last_sent_time = plugin.group_cooldowns.get(group_id, 0)
            now = time.time()
            elapsed = now - last_sent_time
            if elapsed < cooldown_seconds:
                remaining = int(cooldown_seconds - elapsed)
                logger.info(
                    f"[BIG BANANA] 群 {group_id} 处于冷却中，剩余 {remaining} 秒"
                )
                return f"当前群处于画图冷却中，冷却时间为 {cooldown_seconds} 秒，剩余 {remaining} 秒，请稍后再试。"

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

        logger.info(f"[BIG BANANA] 生成图片提示词: {prompt[:128]}")

        # 创建后台任务
        task = asyncio.create_task(plugin.job(event, params, is_llm_tool=True))
        task_id = event.message_obj.message_id
        plugin.running_tasks[task_id] = task
        try:
            results, err_msg = await task
            result_urls = getattr(task, "result_urls", None)
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

            # 记录成功后的冷却时间
            if group_id and cooldown_seconds > 0:
                import time

                plugin.group_cooldowns[group_id] = time.time()

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


@dataclass
class BigBananaAvatarTool(FunctionTool[AstrAgentContext]):
    plugin: Any = None
    name: str = "banana_image_generation_with_avatar"  # 工具名称
    # fmt: off
    description: str = (
"Generate images involving characters using their chat avatars (sender, you, or mentioned users) as reference images. "
"Pass their user IDs (QQ numbers) to referer_id.")  # 工具描述
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": ("Detailed scene description. "
"IMPORTANT: Do NOT invent or describe visual appearance details (like hair color, clothing) for referenced characters (especially you yourself), as their appearance is taken from their avatars. "
"Instead, focus on actions, poses, expressions, and background. "
"Link referenced characters to images by explicitly referring to them as 'the character in image 1' and 'the character in image 2' matching the order of IDs in referer_id. "
"Example: If referer_id is [your_id, user_id], write 'The character in image 1 is feeding the character in image 2 dinner'. "
"If the user hasn't specified an art style, you may optionally choose one matching your persona."),
                },
                "referer_id": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": ("Array of user IDs (QQ numbers) whose avatars should be used as reference images. "
"Include multiple IDs (sender ID, your ID, etc.) for interactive scenes."),
                },
                "preset_name": {
                    "type": "string",
                    "description": ("Use to retrieve preset prompts via banana_preset_prompt. "
"Keep original preset name if modifying based on a preset."),
                },
            },
            "required": ["prompt", "referer_id"],
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

        # 冷却时间判断
        group_id = event.get_group_id()
        cooldown_seconds = getattr(plugin.preference_config, "group_cooldown", 0)
        if group_id and cooldown_seconds > 0:
            import time

            last_sent_time = plugin.group_cooldowns.get(group_id, 0)
            now = time.time()
            elapsed = now - last_sent_time
            if elapsed < cooldown_seconds:
                remaining = int(cooldown_seconds - elapsed)
                logger.info(
                    f"[BIG BANANA] 群 {group_id} 处于冷却中，剩余 {remaining} 秒"
                )
                return f"当前群处于画图冷却中，冷却时间为 {cooldown_seconds} 秒，剩余 {remaining} 秒，请稍后再试。"

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
            results, err_msg = await task
            result_urls = getattr(task, "result_urls", None)
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

            # 记录成功后的冷却时间
            if group_id and cooldown_seconds > 0:
                import time

                plugin.group_cooldowns[group_id] = time.time()

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
