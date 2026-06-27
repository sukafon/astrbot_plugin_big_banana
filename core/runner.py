from __future__ import annotations

import asyncio
import importlib
import os
import re
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .data import MAX_SIZE_B64_LEN, SUPPORTED_FILE_FORMATS_WITH_DOT
from .utils import clear_cache, copy_local_file, read_file, save_images

if TYPE_CHECKING:
    from ..main import BigBanana


async def handle_drawing_result(
    plugin: BigBanana,
    event: AstrMessageEvent,
    task: asyncio.Task,
    params: dict,
    session_id: str,
    group_id: str | None,
    cooldown_seconds: float,
) -> None:
    """Awaits drawing task and generates/sends LLM status response asynchronously.

    Acquires the session lock once the drawing task completes. Sends the generated
    image first if the task succeeds, then calls the LLM (optionally using multimodal
    image URLs) to construct a persona-aligned success or failure reply.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        task: The background drawing task.
        params: Parameters for the drawing task, containing prompts and style options.
        session_id: The unique session identifier for tracking concurrent tasks.
        group_id: The group identifier, or None if private.
        cooldown_seconds: Group cooldown in seconds.

    Returns:
        None
    """
    try:
        results, err_msg = await task
        result_urls = getattr(task, "result_urls", None)

        # Check if giftia plugin is loaded and active
        giftia_inst = None
        bot_name = None
        try:
            giftia_star = plugin.context.get_registered_star("astrbot_plugin_giftia")
            if giftia_star and giftia_star.star_cls and giftia_star.activated:
                giftia_inst = giftia_star.star_cls
                bot_name = giftia_inst.adapter_id_map.get(event.platform_meta.id)
        except Exception as detection_err:
            logger.debug(
                f"[BIG BANANA] Failed to detect giftia plugin: {detection_err}"
            )

        # 如果画图成功，先发送图片
        if not err_msg:
            # 组装消息链
            msg_chain = build_message_chain(
                plugin,
                event,
                results or [],
                result_urls=result_urls,
                url_only=bool(params.get("url", False)),
            )
            await event.send(event.chain_result(msg_chain))

            # Cache the sent image message in giftia database
            if giftia_inst and bot_name:
                try:
                    from datetime import datetime

                    from data.plugins.astrbot_plugin_giftia.core.utils.schemas import (
                        MessageData,
                    )

                    iso_string = datetime.now().isoformat()
                    nickname = event.get_sender_name() or bot_name
                    group_or_user_id = event.get_group_id() or event.get_sender_id()

                    content_str = "[图片]"
                    if result_urls:
                        content_str = f" [图片:{result_urls[0]}]"

                    msg_data = MessageData(
                        nickname=nickname,
                        user_id=event.get_self_id(),
                        group_or_user_id=group_or_user_id,
                        time=iso_string,
                        message_id="",
                        content=content_str,
                        is_recalled=0,
                    )
                    await giftia_inst.data_cache.add_message(
                        bot_name, group_or_user_id, msg_data
                    )
                except Exception as cache_err:
                    logger.warning(
                        f"[BIG BANANA] Failed to cache image message in giftia: {cache_err}"
                    )

        # 开始生成 LLM 回复并排队
        reply_text = ""
        from astrbot.core.utils.session_lock import session_lock_manager

        async with session_lock_manager.acquire_lock(session_id):
            if giftia_inst and bot_name:
                try:
                    nickname = event.get_sender_name() or bot_name
                    group_or_user_id = event.get_group_id() or event.get_sender_id()

                    image_urls_for_llm = []
                    if not err_msg:
                        if result_urls:
                            image_urls_for_llm = result_urls
                        elif results:
                            for mime, b64 in results:
                                if b64:
                                    image_urls_for_llm.append(f"base64://{b64}")

                        prompt_for_reply = (
                            f"【系统通知】画图任务已成功完成。\n"
                            f"用户画图描述是：「{params.get('prompt', '')}」。\n"
                            f"生成的图片已经发送到群聊。请根据你的角色人设 and 上下文，"
                            f"写一句简短、自然、生动的回复告知用户图片已生成完毕并展示在上方。"
                        )
                    else:
                        prompt_for_reply = (
                            f"【系统通知】画图任务失败。\n"
                            f"用户原本的画图描述是：「{params.get('prompt', '')}」。\n"
                            f"报错信息是：「{err_msg}」。\n"
                            f"请根据你的角色人设 and 上下文，写一句自然、温和的话向用户致歉并委婉告知生成失败的原因。"
                        )

                    async for chunk in (
                        giftia_inst.chat_manager.reply_pipeline.dispatch_llm_reply_loop(
                            event=event,
                            bot_name=bot_name,
                            nickname=nickname,
                            group_or_user_id=group_or_user_id,
                            remind_message=prompt_for_reply,
                            image_urls=image_urls_for_llm,
                        )
                    ):
                        if chunk:
                            await giftia_inst.chat_manager.action_dispatcher.dispatch_actions(
                                event=event,
                                bot_name=bot_name,
                                nickname=nickname,
                                group_or_user_id=group_or_user_id,
                                llm_result=chunk,
                            )
                    logger.info(
                        f"[BIG BANANA] Successfully delegated drawing reply to giftia. bot_name: {bot_name}"
                    )
                except Exception as giftia_err:
                    logger.error(
                        f"[BIG BANANA] Failed to delegate drawing reply to giftia: {giftia_err}, falling back to default.",
                        exc_info=True,
                    )
                    giftia_inst = None

            # Fallback to default big_banana response logic if giftia is not active or failed
            if not giftia_inst or not bot_name:
                provider_id = None
                try:
                    using_provider = plugin.context.get_using_provider(session_id)
                    provider_id = using_provider.meta().id if using_provider else None
                except Exception as e:
                    logger.warning(
                        f"[BIG BANANA] 获取当前会话正在使用的提供商失败: {e}"
                    )

                if provider_id:
                    session_curr_cid = await plugin.context.conversation_manager.get_curr_conversation_id(
                        session_id,
                    )
                    system_prompt = ""
                    contexts = []
                    hist_list = []
                    if session_curr_cid:
                        conv = (
                            await plugin.context.conversation_manager.get_conversation(
                                session_id,
                                session_curr_cid,
                            )
                        )
                        if conv:
                            if conv.persona_id:
                                try:
                                    persona = await plugin.context.persona_manager.get_persona(
                                        conv.persona_id
                                    )
                                    if persona:
                                        system_prompt = persona.system_prompt
                                except Exception as e:
                                    logger.warning(
                                        f"[BIG BANANA] 获取人格设定失败: {e}"
                                    )
                            if conv.history:
                                try:
                                    import json

                                    hist_list = json.loads(conv.history)
                                    contexts = hist_list
                                except Exception as e:
                                    logger.warning(
                                        f"[BIG BANANA] 解析对话历史失败: {e}"
                                    )

                    # 区分成功/失败定制 prompt
                    image_urls_for_llm = []
                    if not err_msg:
                        # 成功时收集图片 URL 传给多模态 LLM
                        if result_urls:
                            image_urls_for_llm = result_urls
                        elif results:
                            for mime, b64 in results:
                                if b64:
                                    image_urls_for_llm.append(f"base64://{b64}")

                        prompt_for_reply = (
                            f"【系统通知】画图任务已成功完成。\n"
                            f"用户画图描述是：「{params.get('prompt', '')}」。\n"
                            f"生成的图片已经发送到群聊。请根据你的角色人设 and 上下文，写一句简短、自然、生动的回复告知用户图片已生成完毕并展示在上方（直接说话，回复需符合角色口吻，不要任何多余旁白，也不要再次描述图片）。"
                        )
                    else:
                        prompt_for_reply = (
                            f"【系统通知】画图任务失败。\n"
                            f"用户原本的画图描述是：「{params.get('prompt', '')}」。\n"
                            f"报错信息是：「{err_msg}」。\n"
                            f"请根据你的角色人设 and 上下文，写一句自然、温和的话向用户致歉并委婉告知生成失败的原因（直接说话，回复需符合角色口吻，不要任何多余旁白）。"
                        )

                    try:
                        resp = await plugin.context.llm_generate(
                            chat_provider_id=provider_id,
                            prompt=prompt_for_reply,
                            image_urls=image_urls_for_llm,
                            system_prompt=system_prompt,
                            contexts=contexts,
                        )
                        reply_text = resp.completion_text
                    except Exception as vision_err:
                        logger.warning(
                            f"[BIG BANANA] 使用多模态生成绘图回复失败: {vision_err}，尝试仅文本生成"
                        )
                        try:
                            resp = await plugin.context.llm_generate(
                                chat_provider_id=provider_id,
                                prompt=prompt_for_reply,
                                system_prompt=system_prompt,
                                contexts=contexts,
                            )
                            reply_text = resp.completion_text
                        except Exception as all_err:
                            logger.error(
                                f"[BIG BANANA] 后台生成绘图回复完全失败: {all_err}"
                            )

                    if reply_text and session_curr_cid:
                        try:
                            hist_list.append(
                                {"role": "assistant", "content": reply_text}
                            )
                            await (
                                plugin.context.conversation_manager.update_conversation(
                                    unified_msg_origin=session_id,
                                    conversation_id=session_curr_cid,
                                    history=hist_list,
                                )
                            )
                        except Exception as history_err:
                            logger.warning(
                                f"[BIG BANANA] 更新对话历史失败: {history_err}"
                            )

        if reply_text:
            await asyncio.sleep(0.2)
            await event.send(
                event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(reply_text.strip()),
                    ]
                )
            )
        elif err_msg and (not giftia_inst or not bot_name):
            # 如果 LLM 生成失败，则回退发送标准错误信息
            await event.send(
                event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(f"❌ 图片生成失败：{err_msg}"),
                    ]
                )
            )

        # 记录成功后的冷却时间
        if not err_msg and group_id and cooldown_seconds > 0:
            plugin.group_cooldowns[group_id] = time.time()
    except asyncio.CancelledError:
        logger.info(f"会话 {session_id} 的绘图任务被取消")
    except Exception as e:
        logger.error(f"绘图任务后台处理出错: {e}", exc_info=True)
        try:
            await event.send(
                event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("❌ 绘图执行过程中发生内部错误。"),
                    ]
                )
            )
        except Exception:
            pass
    finally:
        plugin.running_tasks.pop(session_id, None)
        task_temp_dir = params.get("task_temp_dir", plugin.temp_dir)
        clear_cache(task_temp_dir)
        if task_temp_dir != plugin.temp_dir:
            try:
                task_temp_dir.rmdir()
            except Exception as e:
                logger.warning(
                    f"[BIG BANANA] Failed to remove task temp dir {task_temp_dir}: {e}"
                )


async def handle_on_message(
    plugin: BigBanana, event: AstrMessageEvent
) -> AsyncGenerator[AstrMessageEvent, None]:
    """Handles the incoming message event to trigger image generation.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.

    Yields:
        AstrMessageEvent with results or errors.
    """
    # 取出所有 Plain 类型的组件拼接成纯文本内容
    plain_components = [
        comp for comp in event.get_messages() if isinstance(comp, Comp.Plain)
    ]

    # 拼接成一个字符串
    if plain_components:
        message_str = " ".join(comp.text for comp in plain_components).strip()
    else:
        message_str = event.message_str
    # 跳过空消息
    if not message_str:
        return

    # 先处理前缀
    matched_prefix = False
    for prefix in plugin.prefix_list:
        if message_str.startswith(prefix):
            message_str = message_str.removeprefix(prefix).lstrip()
            matched_prefix = True
            break

    # 若未@机器人且未开启混合模式，且配置了前缀列表但消息未匹配到任何前缀，则跳过处理
    if (
        not event.is_at_or_wake_command
        and not plugin.coexist_enabled
        and plugin.prefix_list
        and not matched_prefix
    ):
        return

    cmd = message_str.split(" ", 1)[0]
    # 检查命令是否在提示词配置中
    if cmd not in plugin.prompt_dict:
        return

    # 群白名单判断
    if (
        plugin.group_whitelist_enabled
        and event.unified_msg_origin not in plugin.group_whitelist
    ):
        logger.info(f"群 {event.unified_msg_origin} 不在白名单内，跳过处理")
        return

    # 用户白名单判断
    if (
        plugin.user_whitelist_enabled
        and event.get_sender_id() not in plugin.user_whitelist
    ):
        logger.info(f"用户 {event.get_sender_id()} 不在白名单内，跳过处理")
        return

    # 冷却时间判断 (Group Cooldown)
    group_id = event.get_group_id()
    cooldown_seconds = getattr(plugin.preference_config, "group_cooldown", 0)
    if group_id and cooldown_seconds > 0:
        last_sent_time = plugin.group_cooldowns.get(group_id, 0)
        now = time.time()
        elapsed = now - last_sent_time
        if elapsed < cooldown_seconds:
            remaining = int(cooldown_seconds - elapsed)
            logger.info(f"群 {group_id} 处于画图冷却中，剩余时间: {remaining} 秒")
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(
                        f"❌ 冷却中！该群画图冷却时间为 {cooldown_seconds} 秒，剩余 {remaining} 秒，请稍后再试。"
                    ),
                ]
            )
            return

    # 获取提示词配置 (使用 .copy() 防止修改污染全局预设)
    params = plugin.prompt_dict.get(cmd, {}).copy()

    session_id = event.unified_msg_origin
    task_temp_dir = plugin.temp_dir / f"task_{session_id}_{int(time.time())}"
    os.makedirs(task_temp_dir, exist_ok=True)
    params["task_temp_dir"] = task_temp_dir

    # Copy all local temp images of this event to task_temp_dir so they don't get deleted early
    for comp in event.get_messages():
        if isinstance(comp, Comp.Image) and comp.url:
            comp.url = copy_local_file(comp.url, task_temp_dir)
            if comp.file:
                comp.file = comp.url
            if comp.path:
                comp.path = comp.url
        elif isinstance(comp, Comp.Reply) and comp.chain:
            for quote in comp.chain:
                if isinstance(quote, Comp.Image) and quote.url:
                    quote.url = copy_local_file(quote.url, task_temp_dir)
                    if quote.file:
                        quote.file = quote.url
                    if quote.path:
                        quote.path = quote.url

    # 先从预设提示词参数字典字典中取出提示词
    preset_prompt = params.get("prompt", "{{user_text}}")

    # 处理预设提示词补充参数preset_append
    if (
        params.get("preset_append", plugin.common_config.preset_append)
        and "{{user_text}}" not in preset_prompt
    ):
        preset_prompt += " {{user_text}}"

    # 检查预设提示词中是否包含动态参数占位符
    if "{{user_text}}" in preset_prompt:
        # 存在动态参数，解析用户消息
        _, user_params = plugin.parsing_prompt_params(message_str)
        # 将用户参数差分覆盖预设参数
        params.update(user_params)
        # 解析到用户的提示词和配置参数
        user_prompt = user_params.get("prompt", "anything").strip()
        # 替换占位符，更新提示词
        new_prompt = preset_prompt.replace("{{user_text}}", user_prompt)
        params["prompt"] = new_prompt

    # 处理收集模式
    image_urls = []
    if params.get("gather_mode", plugin.prompt_config.gather_mode):
        # 记录操作员账号
        operator_id = event.get_sender_id()
        # 取消标记
        is_cancel = False
        yield event.plain_result(
            f"📝 绘图收集模式已启用：\n"
            f"文本：{params['prompt']}\n"
            f"图片：{len(image_urls)} 张\n\n"
            f"💡 继续发送图片或文本，或者：\n"
            f"• 发送「开始」开始生成\n"
            f"• 发送「取消」取消操作\n"
            f"• 60 秒内有效\n"
        )

        @session_waiter(timeout=60, record_history_chains=False)  # type: ignore
        async def waiter(controller: SessionController, waiter_event: AstrMessageEvent):
            nonlocal is_cancel
            # 判断消息来源是否是同一用户
            if waiter_event.get_sender_id() != operator_id:
                return

            if waiter_event.message_str.strip() == "取消":
                is_cancel = True
                await waiter_event.send(waiter_event.plain_result("🍌 操作已取消。"))
                controller.stop()
                return
            if waiter_event.message_str.strip() == "开始":
                controller.stop()
                return
            # 开始收集文本 and 图片
            for comp in waiter_event.get_messages():
                if isinstance(comp, Comp.Plain) and comp.text:
                    # 追加文本到提示词
                    params["prompt"] += " " + comp.text.strip()
                elif isinstance(comp, Comp.Image) and comp.url:
                    image_urls.append(copy_local_file(comp.url, task_temp_dir))
                elif (
                    isinstance(comp, Comp.File)
                    and comp.url
                    and comp.url.startswith("http")
                    and comp.url.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
                ):
                    image_urls.append(comp.url)
            await waiter_event.send(
                waiter_event.plain_result(
                    f"📝 绘图追加模式已收集内容：\n"
                    f"文本：{params['prompt']}\n"
                    f"图片：{len(image_urls)} 张\n\n"
                    f"💡 继续发送图片或文本，或者：\n"
                    f"• 发送「开始」开始生成\n"
                    f"• 发送「取消」取消操作\n"
                    f"• 60 秒内有效\n"
                )
            )
            controller.keep(timeout=60, reset_timeout=True)

        try:
            await waiter(event)
        except TimeoutError:
            yield event.plain_result("❌ 超时了，操作已取消！")
            return
        except Exception as e:
            logger.error(f"绘图提示词追加模式出现错误: {e}", exc_info=True)
            yield event.plain_result("❌ 处理时发生了一个内部错误。")
            return
        finally:
            if is_cancel:
                event.stop_event()
                return

    session_id = event.unified_msg_origin
    if session_id in plugin.running_tasks:
        yield event.chain_result(
            [
                Comp.Reply(id=event.message_obj.message_id),
                Comp.Plain(
                    "❌ 当前会话已有一个绘图任务正在进行，请等待其完成后再发起新任务。"
                ),
            ]
        )
        return

    logger.info(f"正在生成图片，提示词: {params['prompt'][:60]}")
    logger.debug(
        f"生成图片应用参数: { {k: v for k, v in params.items() if k != 'prompt'} }"
    )
    # 调用作图任务
    task = asyncio.create_task(job(plugin, event, params, image_urls=image_urls))
    plugin.running_tasks[session_id] = task

    # 立即发送正在画图提示
    if getattr(plugin.preference_config, "enable_drawing_message", True):
        yield event.plain_result(plugin.preference_config.drawing_message)

    asyncio.create_task(
        handle_drawing_result(
            plugin,
            event,
            task,
            params,
            session_id,
            group_id,
            cooldown_seconds,
        )
    )


async def job(
    plugin: BigBanana,
    event: AstrMessageEvent,
    params: dict,
    image_urls: list[str] | None = None,
    referer_id: list[str] | None = None,
    is_llm_tool: bool = False,
) -> tuple[list[tuple[str, str]] | None, str | None]:
    """Generates images by calling model providers and handling prompt optimization.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        params: Prompt and image configuration parameters.
        image_urls: Input image URLs.
        referer_id: Referer user IDs.
        is_llm_tool: True if invoked via LLM tool.

    Returns:
        A tuple of (valid_results, error_message).
    """
    # 副脑提示词优化
    should_optimize = False
    if "sub_brain" in params:
        should_optimize = bool(params["sub_brain"])
    else:
        should_optimize = plugin.sub_brain_config.enabled and is_llm_tool

    if should_optimize:
        orig_prompt = params.get("prompt", "")
        if orig_prompt:
            provider_id = plugin.sub_brain_config.provider_id
            if not provider_id:
                umo = event.unified_msg_origin if event else None
                try:
                    using_provider = plugin.context.get_using_provider(umo)
                    provider_id = using_provider.meta().id if using_provider else None
                except Exception as e:
                    logger.warning(
                        f"[BIG BANANA] 获取当前会话正在使用的提供商失败: {e}"
                    )

            if provider_id:
                try:
                    logger.info(
                        f"[BIG BANANA] 正在使用副脑进行提示词优化，模型提供商: {provider_id}"
                    )
                    resp = await plugin.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=orig_prompt,
                        system_prompt=plugin.sub_brain_config.system_prompt,
                    )
                    optimized_prompt = resp.completion_text
                    if optimized_prompt:
                        optimized_prompt = optimized_prompt.strip()
                        logger.info(
                            f"[BIG BANANA] 副脑优化完成，优化后提示词: {optimized_prompt}"
                        )
                        params["prompt"] = optimized_prompt
                    else:
                        logger.warning(
                            "[BIG BANANA] 副脑优化返回了空文本，将使用原始提示词"
                        )
                except Exception as e:
                    logger.error(
                        f"[BIG BANANA] 副脑提示词优化失败: {e}，将使用原始提示词生成图片"
                    )
            else:
                logger.warning(
                    "[BIG BANANA] 已启用副脑优化但未能解析到有效的副脑模型供应商，跳过优化"
                )

    # 收集图片URL，后面统一处理
    if image_urls is None:
        image_urls = []

    if referer_id is None:
        referer_id = []
    # Local substitution reference images list
    bot_local_refs = []

    # Helper function to substitute avatar if configured
    def get_substituted_image(target_id: str) -> tuple[str | None, str | None]:
        target_id = str(target_id).strip()
        self_id = str(event.get_self_id())

        ref_imgs = None
        if target_id in plugin.avatar_substitutions_map:
            ref_imgs = plugin.avatar_substitutions_map[target_id]
        elif target_id == self_id:
            for key in (self_id, "bot", "self"):
                if key in plugin.avatar_substitutions_map:
                    ref_imgs = plugin.avatar_substitutions_map[key]
                    break
        elif target_id in ("bot", "self"):
            for key in (self_id, "bot", "self"):
                if key in plugin.avatar_substitutions_map:
                    ref_imgs = plugin.avatar_substitutions_map[key]
                    break

        if ref_imgs:
            import random

            chosen = random.choice(ref_imgs)
            if chosen.startswith("http"):
                return chosen, None
            else:
                return None, chosen
        return None, None

    # Flag to optimize At avatar by skipping it if At target is reply sender
    skipped_at_qq = False
    reply_sender_id = ""
    for comp in event.get_messages():
        if isinstance(comp, Comp.Reply) and comp.chain:
            reply_sender_id = str(comp.sender_id)
            for quote in comp.chain:
                if isinstance(quote, Comp.Image) and quote.url:
                    image_urls.append(quote.url)
                elif (
                    isinstance(quote, Comp.File)
                    and quote.url
                    and quote.url.startswith("http")
                    and quote.url.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
                ):
                    image_urls.append(quote.url)
        # Process At targets
        elif (
            isinstance(comp, Comp.At)
            and comp.qq
            and event.platform_meta.name == "aiocqhttp"
        ):
            qq = str(comp.qq)
            self_id = str(event.get_self_id())
            if not skipped_at_qq and (
                # If At target is the reply sender, skip once
                (qq == reply_sender_id and plugin.preference_config.skip_quote_first)
                or (
                    qq == self_id
                    and event.is_at_or_wake_command
                    and plugin.preference_config.skip_at_first
                )  # Skipped first At wake
                or (
                    qq == self_id
                    and plugin.preference_config.skip_llm_at_first
                    and is_llm_tool
                )  # Skipped first At tool invocation
            ):
                skipped_at_qq = True
                continue

            # Substitute target avatar if substitution mapping exists
            sub_url, sub_file = get_substituted_image(qq)
            if sub_url:
                image_urls.append(sub_url)
            elif sub_file:
                bot_local_refs.append(sub_file)
            else:
                image_urls.append(f"https://q.qlogo.cn/g?b=qq&s=0&nk={comp.qq}")
        elif isinstance(comp, Comp.Image) and comp.url:
            image_urls.append(comp.url)
        elif (
            isinstance(comp, Comp.File)
            and comp.url
            and comp.url.startswith("http")
            and comp.url.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
        ):
            image_urls.append(comp.url)

    # Process referer_id argument and fetch user avatars
    if is_llm_tool and referer_id and event.platform_meta.name == "aiocqhttp":
        for target_id in referer_id:
            target_id = target_id.strip()
            if target_id:
                # Substitute target avatar if substitution mapping exists
                sub_url, sub_file = get_substituted_image(target_id)
                if sub_url:
                    if sub_url not in image_urls:
                        image_urls.append(sub_url)
                elif sub_file:
                    bot_local_refs.append(sub_file)
                else:
                    build_url = f"https://q.qlogo.cn/g?b=qq&s=0&nk={target_id}"
                    if build_url not in image_urls:
                        image_urls.append(build_url)

    min_required_images = params.get("min_images", plugin.prompt_config.min_images)
    max_allowed_images = params.get("max_images", plugin.prompt_config.max_images)
    # If total images are less than minimum required, fall back to sender avatar
    if (
        len(image_urls) + len(bot_local_refs) < min_required_images
        and event.platform_meta.name == "aiocqhttp"
    ):
        image_urls.append(f"https://q.qlogo.cn/g?b=qq&s=0&nk={event.get_sender_id()}")

    # Base64 images list
    image_b64_list: list[tuple[str, str]] = []

    # Load local bot reference images first
    for filename in bot_local_refs:
        if len(image_b64_list) >= max_allowed_images:
            break
        filename = filename.strip()
        if filename:
            path = plugin.refer_images_dir / filename
            mime_type, b64_data = await asyncio.to_thread(read_file, path)
            if mime_type and b64_data:
                image_b64_list.append((mime_type, b64_data))

    # Load refer_images configurations
    refer_images = params.get("refer_images", plugin.prompt_config.refer_images)
    if refer_images:
        for filename in refer_images.split(","):
            if len(image_b64_list) >= max_allowed_images:
                break
            filename = filename.strip()
            if filename:
                path = plugin.refer_images_dir / filename
                mime_type, b64_data = await asyncio.to_thread(read_file, path)
                if mime_type and b64_data:
                    image_b64_list.append((mime_type, b64_data))
    # 图片去重
    image_urls = list(dict.fromkeys(image_urls))
    # 判断图片数量是否满足最小要求
    if len(image_urls) + len(image_b64_list) < min_required_images:
        warn_msg = f"图片数量不足，最少需要 {min_required_images} 张图片，当前仅 {len(image_urls) + len(image_b64_list)} 张"
        logger.warning(warn_msg)
        return None, warn_msg

    # 检查图片数量是否超过最大允许数量，不超过则可从url中下载图片
    append_count = max_allowed_images - len(image_b64_list)
    if append_count > 0 and image_urls:
        # 取前n张图片，下载并转换为Base64，追加到b64图片列表
        if len(image_b64_list) + len(image_urls) > max_allowed_images:
            logger.warning(
                f"参考图片数量超过或等于最大图片数量，将只使用前 {max_allowed_images} 张参考图片"
            )
        fetched = await plugin.downloader.fetch_images(image_urls[:append_count])
        if fetched:
            image_b64_list.extend(fetched)

        # 如果 min_required_images 为 0，列表为空是允许的
        if not image_b64_list and min_required_images > 0:
            logger.error("全部图片下载失败或者图片格式不支持")
            return None, "全部图片下载失败或者图片格式不支持"
    elif append_count < 0:
        logger.warning(
            f"参考图片数量超过最大允许数量 {max_allowed_images}，跳过下载图片步骤"
        )

    # 发送绘图中提示
    if getattr(plugin.preference_config, "enable_drawing_message", True):
        text = plugin.preference_config.drawing_message
        clean_text = re.sub(
            r"<emotions>.*?</emotions>", "", text, flags=re.DOTALL | re.IGNORECASE
        ).strip()
        sent_meme = False
        if "<emotions>" in text.lower():
            try:
                from astrbot.core.star.star import star_map

                meme_manager = None
                meme_manager_module_name = None
                for star in star_map.values():
                    if (
                        star.root_dir_name == "astrbot_plugin_meme_manager"
                        and star.star_cls
                    ):
                        meme_manager = star.star_cls
                        meme_manager_module_name = star.module.__name__
                        break

                if meme_manager and meme_manager_module_name:
                    raw_tags = []
                    for match in re.finditer(
                        r"<emotions>(.*?)</emotions>",
                        text,
                        re.DOTALL | re.IGNORECASE,
                    ):
                        inner_content = match.group(1)
                        for tag in re.split(r"[,，\s]+", inner_content):
                            tag = tag.strip()
                            if tag:
                                raw_tags.append(tag)

                    if raw_tags:
                        # Get package name
                        if "." in meme_manager_module_name:
                            package_name = meme_manager_module_name.rsplit(".", 1)[0]
                        else:
                            package_name = meme_manager_module_name

                        config_mod = importlib.import_module(f"{package_name}.config")
                        memes_dir = config_mod.MEMES_DIR

                        handler_mod = importlib.import_module(
                            f"{package_name}.backend.core.emotion_handler"
                        )
                        get_direct_trigger_memes = handler_mod.get_direct_trigger_memes

                        helper_mod = importlib.import_module(
                            f"{package_name}.backend.core.helpers"
                        )
                        convert_to_gif = helper_mod.convert_to_gif

                        selected_memes = await get_direct_trigger_memes(
                            meme_manager, event, raw_tags
                        )
                        if selected_memes:
                            meme_file = os.path.join(memes_dir, selected_memes[0])
                            final_meme_file = convert_to_gif(meme_file, meme_manager)
                            img = Comp.Image.fromFileSystem(final_meme_file)
                            object.__setattr__(img, "sub_type", 1)
                            if clean_text:
                                await event.send(MessageChain([Comp.Plain(clean_text)]))
                            await event.send(MessageChain([img]))
                            sent_meme = True
            except Exception as e:
                logger.warning(f"[BIG BANANA] 尝试从 meme_manager 获取表情包失败: {e}")

        if not sent_meme:
            await event.send(MessageChain().message(clean_text))

    # 调度提供商生成图片
    images_result, err, result_urls = await plugin.dispatcher.dispatch(
        event=event, params=params, image_b64_list=image_b64_list
    )

    # 再次检查图片结果是否为空
    valid_results = [(mime, b64) for mime, b64 in (images_result or []) if b64]

    # 确定最终的 result_urls
    final_urls = result_urls
    if params.get("url", False) and not final_urls and valid_results:
        final_urls = await _upload_results_for_url_mode(plugin, valid_results)

    # 将 result_urls 挂载到当前 task 对象上，实现向前/向后兼容的多返回值传递
    if final_urls:
        try:
            current_task = asyncio.current_task()
            if current_task:
                current_task.result_urls = final_urls
        except Exception:
            pass

    if params.get("url", False):
        if final_urls:
            return [], None
        return None, "当前提供商未返回可用的图片URL"

    if not valid_results:
        if not err:
            err = "图片生成失败：响应中未包含图片数据"
            logger.error(err)
        return None, err

    # 保存图片到本地
    if plugin.save_images:
        save_images(valid_results, plugin.save_dir)

    return valid_results, None


async def _upload_results_for_url_mode(
    plugin: BigBanana, results: list[tuple[str, str]]
) -> list[str] | None:
    """Uploads the image generation results to the image hoster when url=True.

    Args:
        plugin: The BigBanana plugin instance.
        results: List of (mime, base64) results.

    Returns:
        List of public URLs if successful, otherwise None.
    """
    if not plugin.image_hoster.is_enabled():
        logger.warning("[BIG BANANA] 未配置图床上传，无法将图片转换为URL返回")
        return None
    try:
        return await plugin.image_hoster.upload_images(results)
    except Exception as e:
        logger.error(f"[BIG BANANA] 图床上传失败: {e}")
        return None


def build_message_chain(
    plugin: BigBanana,
    event: AstrMessageEvent,
    results: list[tuple[str, str]],
    result_urls: list[str] | None = None,
    url_only: bool = False,
    params: dict | None = None,
) -> list[BaseMessageComponent]:
    """Builds the message chain containing the generated images or file components.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        results: List of (mime, base64) results.
        result_urls: Optional list of uploaded image URLs.
        url_only: True if only returning URLs.

    Returns:
        List of message components.
    """
    msg_chain: list[BaseMessageComponent] = [
        Comp.Reply(id=event.message_obj.message_id)
    ]
    if url_only:
        if result_urls:
            msg_chain.append(Comp.Plain("\n".join(result_urls)))
        return msg_chain
    # 对Telegram平台特殊处理，超过10MB的图片需要作为文件发送
    if event.platform_meta.name == "telegram" and any(
        (b64 and len(b64) > MAX_SIZE_B64_LEN) for _, b64 in results
    ):
        task_temp_dir = (
            params.get("task_temp_dir", plugin.temp_dir) if params else plugin.temp_dir
        )
        save_results = save_images(results, task_temp_dir)
        for name_, path_ in save_results:
            msg_chain.append(Comp.File(name=name_, file=str(path_)))
        return msg_chain

    # 其他平台直接发送图片
    msg_chain.extend(Comp.Image.fromBase64(b64) for _, b64 in results)
    return msg_chain
