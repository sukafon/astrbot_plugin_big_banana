from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .data import PARAMS_LIST

if TYPE_CHECKING:
    from ..main import BigBanana


async def add_whitelist_command(
    plugin: BigBanana, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
) -> AsyncGenerator[AstrMessageEvent, None]:
    """Add a user or group to the whitelist.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        cmd_type: Whitelist type, either '用户'/'user' or '群组'/'group'.
        target_id: The ID to be added.

    Yields:
        AstrMessageEvent plain result.
    """
    if not plugin.is_global_admin(event):
        logger.info(
            f"用户 {event.get_sender_id()} 试图执行管理员命令 lm白名单添加，权限不足"
        )
        return

    if not cmd_type or not target_id:
        yield event.plain_result("❌ 格式错误。\n用法：lm白名单添加 (用户/群组) (ID)")
        return

    msg_type = ""
    if cmd_type in ["用户", "user"] and target_id not in plugin.user_whitelist:
        msg_type = "用户"
        plugin.user_whitelist.append(target_id)
    elif cmd_type in ["群组", "group"] and target_id not in plugin.group_whitelist:
        msg_type = "群组"
        plugin.group_whitelist.append(target_id)
    elif cmd_type not in ["用户", "user", "群组", "group"]:
        yield event.plain_result("❌ 类型错误，请使用「用户」或「群组」。")
        return
    else:
        yield event.plain_result(f"⚠️ {target_id} 已在名单列表中。")
        return

    plugin.conf.save_config()
    yield event.plain_result(f"✅ 已添加{msg_type}白名单：{target_id}")


async def del_whitelist_command(
    plugin: BigBanana, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
) -> AsyncGenerator[AstrMessageEvent, None]:
    """Remove a user or group from the whitelist.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        cmd_type: Whitelist type, either '用户'/'user' or '群组'/'group'.
        target_id: The ID to be deleted.

    Yields:
        AstrMessageEvent plain result.
    """
    if not plugin.is_global_admin(event):
        logger.info(
            f"用户 {event.get_sender_id()} 试图执行管理员命令 lm白名单删除，权限不足"
        )
        return

    if not cmd_type or not target_id:
        yield event.plain_result("❌ 格式错误。\n用法：lm白名单删除 (用户/群组) (ID)")
        return

    if cmd_type in ["用户", "user"] and target_id in plugin.user_whitelist:
        msg_type = "用户"
        plugin.user_whitelist.remove(target_id)
    elif cmd_type in ["群组", "group"] and target_id in plugin.group_whitelist:
        msg_type = "群组"
        plugin.group_whitelist.remove(target_id)
    elif cmd_type not in ["用户", "user", "群组", "group"]:
        yield event.plain_result("❌ 类型错误，请使用「用户」或「群组」。")
        return
    else:
        yield event.plain_result(f"⚠️ {target_id} 不在名单列表中。")
        return

    plugin.conf.save_config()
    yield event.plain_result(f"🗑️ 已删除{msg_type}白名单：{target_id}")


async def list_whitelist_command(
    plugin: BigBanana, event: AstrMessageEvent
) -> AsyncGenerator[AstrMessageEvent, None]:
    """List all whitelisted users and groups.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.

    Yields:
        AstrMessageEvent plain result listing whitelisted targets.
    """
    if not plugin.is_global_admin(event):
        logger.info(
            f"用户 {event.get_sender_id()} 试图执行管理员命令 lm白名单列表，权限不足"
        )
        return

    msg = f"""📋 白名单配置状态：
=========
🏢 群组限制：{"✅ 开启" if plugin.group_whitelist_enabled else "⬜ 关闭"}
列表：{plugin.group_whitelist}
=========
👤 用户限制：{"✅ 开启" if plugin.user_whitelist_enabled else "⬜ 关闭"}
列表：{plugin.user_whitelist}"""

    yield event.plain_result(msg)


async def add_prompt_command(
    plugin: BigBanana, event: AstrMessageEvent, trigger_word: str = ""
) -> AsyncGenerator[AstrMessageEvent, None]:
    """Start an interactive session to add or update a preset prompt.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        trigger_word: The command word triggering the prompt.

    Yields:
        AstrMessageEvent prompt initialization message.
    """
    if not plugin.is_global_admin(event):
        logger.info(f"用户 {event.get_sender_id()} 试图执行管理员命令 lm添加，权限不足")
        return

    if not trigger_word:
        yield event.plain_result("❌ 格式错误：lm添加 (触发词)")
        return

    yield event.plain_result(
        f"🍌 正在为触发词 「{trigger_word}」 添加/更新提示词\n"
        f"✦ 请在60秒内输入完整的提示词内容（不含触发词，包含参数）\n"
        f"✦ 输入「取消」可取消操作。"
    )

    operator_id = event.get_sender_id()

    @session_waiter(timeout=60, record_history_chains=False)  # type: ignore
    async def waiter(controller: SessionController, waiter_event: AstrMessageEvent):
        if waiter_event.get_sender_id() != operator_id:
            return

        if waiter_event.message_str.strip() == "取消":
            await waiter_event.send(waiter_event.plain_result("🍌 操作已取消。"))
            controller.stop()
            return

        build_prompt = f"{trigger_word} {waiter_event.message_str.strip()}"

        action = "添加"
        if trigger_word in plugin.prompt_dict:
            action = "更新"
            for i, v in enumerate(plugin.prompt_list):
                cmd, _, prompt_str = v.strip().partition(" ")
                if cmd == trigger_word:
                    plugin.prompt_list[i] = build_prompt
                    break
                if cmd.startswith("[") and cmd.endswith("]"):
                    cmd_list = cmd[1:-1].split(",")
                    if trigger_word in cmd_list:
                        cmd_list.remove(trigger_word)
                        if len(cmd_list) == 1:
                            new_config_item = f"{cmd_list[0]} {prompt_str}"
                        else:
                            new_cmd = "[" + ",".join(cmd_list) + "]"
                            new_config_item = f"{new_cmd} {prompt_str}"
                        plugin.prompt_list[i] = new_config_item
                        plugin.prompt_list.append(build_prompt)
                        break
        else:
            plugin.prompt_list.append(build_prompt)

        plugin.conf.save_config()
        plugin.init_prompts()
        await waiter_event.send(
            waiter_event.plain_result(f"✅ 已成功{action}提示词：「{trigger_word}」")
        )
        controller.stop()

    try:
        await waiter(event)
    except TimeoutError:
        yield event.plain_result("❌ 超时了，操作已取消！")
    except Exception as e:
        logger.error(f"大香蕉添加提示词出现错误: {e}", exc_info=True)
        yield event.plain_result("❌ 处理时发生了一个内部错误。")
    finally:
        event.stop_event()


async def list_prompts_command(
    plugin: BigBanana, event: AstrMessageEvent
) -> AsyncGenerator[AstrMessageEvent, None]:
    """List all registered trigger commands.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.

    Yields:
        AstrMessageEvent plain result with trigger list.
    """
    if not plugin.is_global_admin(event):
        logger.info(f"用户 {event.get_sender_id()} 试图执行管理员命令 lm列表，权限不足")
        return

    prompts = list(plugin.prompt_dict.keys())
    if not prompts:
        yield event.plain_result("当前没有预设提示词。")
        return

    msg = "📜 当前预设提示词列表：\n" + "、".join(prompts)
    yield event.plain_result(msg)


async def prompt_details(
    plugin: BigBanana, event: AstrMessageEvent, trigger_word: str
) -> AsyncGenerator[AstrMessageEvent, None]:
    """Retrieve detailed info for a specific trigger prompt.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        trigger_word: The command trigger word.

    Yields:
        AstrMessageEvent with details block orcq/Nodes chain.
    """
    if trigger_word not in plugin.prompt_dict:
        yield event.plain_result(f"❌ 未找到提示词：「{trigger_word}」")
        return

    params = plugin.prompt_dict[trigger_word]
    details = [f"📋 提示词详情：「{trigger_word}」"]
    details.append(params.get("prompt", ""))
    for key in PARAMS_LIST:
        if key in params:
            details.append(f"{key}: {params[key]}")
    if event.platform_meta.name == "aiocqhttp":
        from astrbot.api.message_components import Node, Nodes, Plain

        nodes = []
        for detail in details:
            nodes.append(
                Node(
                    uin=event.get_sender_id(),
                    name=event.get_sender_name(),
                    content=[Plain(detail)],
                )
            )
        yield event.chain_result([Nodes(nodes)])
    else:
        yield event.plain_result("\n".join(details))


async def del_prompt_command(
    plugin: BigBanana, event: AstrMessageEvent, trigger_word: str = ""
) -> AsyncGenerator[AstrMessageEvent, None]:
    """Delete a prompt trigger. Handles multi-trigger configuration interactively.

    Args:
        plugin: The BigBanana plugin instance.
        event: The incoming message event.
        trigger_word: The command word to delete.

    Yields:
        AstrMessageEvent prompt results or interactive confirmation choices.
    """
    if not plugin.is_global_admin(event):
        logger.info(f"用户 {event.get_sender_id()} 试图执行管理员命令 lm删除，权限不足")
        return

    if not trigger_word:
        yield event.plain_result("❌ 格式错误：lm删除 (触发词)")
        return

    if trigger_word not in plugin.prompt_dict:
        yield event.plain_result(f"❌ 未找到提示词：「{trigger_word}」")
        return

    for i, v in enumerate(plugin.prompt_list):
        cmd, _, prompt_str = v.strip().partition(" ")
        if cmd == trigger_word:
            del plugin.prompt_list[i]
            plugin.init_prompts()
            plugin.conf.save_config()
            yield event.plain_result(f"🗑️ 已删除提示词：「{trigger_word}」")
            return
        if cmd.startswith("[") and cmd.endswith("]"):
            cmd_list = cmd[1:-1].split(",")
            if trigger_word not in cmd_list:
                continue

            yield event.plain_result(
                "⚠️ 检测到该提示词为多触发词配置，请选择删除方案\n"
                "A. 单独删除该触发词\n"
                "B. 删除该多触发词\n"
                "C. 取消操作"
            )

            @session_waiter(timeout=30, record_history_chains=False)  # type: ignore
            async def waiter(
                controller: SessionController, waiter_event: AstrMessageEvent
            ):
                if not plugin.is_global_admin(waiter_event):
                    logger.info(
                        f"用户 {waiter_event.get_sender_id()} 试图执行管理员命令 lm删除，权限不足"
                    )
                    return

                reply_content = waiter_event.message_str.strip().upper()
                if reply_content not in ["A", "B", "C"]:
                    await waiter_event.send(
                        waiter_event.plain_result("❌ 请输入有效的选项：A、B 或 C。")
                    )
                    return

                if reply_content == "C":
                    await waiter_event.send(
                        waiter_event.plain_result("🍌 操作已取消。")
                    )
                    controller.stop()
                    return
                if reply_content == "B":
                    del plugin.prompt_list[i]
                    await waiter_event.send(
                        waiter_event.plain_result(f"🗑️ 已删除多触发提示词：{cmd}")
                    )
                    plugin.conf.save_config()
                    controller.stop()
                    return
                if reply_content == "A":
                    cmd_list.remove(trigger_word)
                    if len(cmd_list) == 1:
                        new_config_item = f"{cmd_list[0]} {prompt_str}"
                    else:
                        new_cmd = "[" + ",".join(cmd_list) + "]"
                        new_config_item = f"{new_cmd} {prompt_str}"
                    plugin.prompt_list[i] = new_config_item
                    del plugin.prompt_dict[trigger_word]
                    plugin.init_prompts()
                    await waiter_event.send(
                        waiter_event.plain_result(
                            f"🗑️ 已从多触发提示词中移除：「{trigger_word}」"
                        )
                    )
                    plugin.conf.save_config()
                    controller.stop()
                    return

            try:
                await waiter(event)
            except TimeoutError:
                yield event.plain_result("❌ 超时了，操作已取消！")
            except Exception as e:
                logger.error(f"大香蕉删除提示词出现错误: {e}", exc_info=True)
                yield event.plain_result("❌ 处理时发生了一个内部错误。")
            finally:
                event.stop_event()
            return
    else:
        logger.error(f"提示词列表和提示词字典不一致，未找到提示词：「{trigger_word}」")
        yield event.plain_result(f"❌ 未找到提示词：「{trigger_word}」")
