from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.message.message_event_result import MessageEventResult

    from ...guards import WhitelistGuard


class WhitelistHandler:
    """处理白名单访问判断和白名单管理命令。"""

    def __init__(self, guard: WhitelistGuard) -> None:
        """保存配置对象并初始化白名单守卫。"""
        self._guard = guard
        self.conf = guard.conf

    async def add_whitelist(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ) -> AsyncGenerator[MessageEventResult, None]:
        """校验参数并把用户或群组加入白名单。"""
        cmd_type = cmd_type.strip()
        target_id = target_id.strip()
        if not cmd_type or not target_id:
            yield event.plain_result(
                "❌ 格式错误。\n用法：lm白名单添加 (用户/群组) (ID)"
        )
            return

        # 识别用户输入的目标类型，支持中文和英文别名。
        target_type: str | None = None
        if cmd_type in {"用户", "user"}:
            target_type = "用户"
        elif cmd_type in {"群组", "group"}:
            target_type = "群组"
        if target_type is None:
            yield event.plain_result("❌ 类型错误，请使用「用户」或「群组」。")
            return

        # 复制当前配置中的名单，修改后再整体写回，避免直接改到异常类型。
        config = dict(self.conf.get("whitelist_config", {}))
        group_whitelist = [str(item) for item in config.get("whitelist", [])]
        user_whitelist = [str(item) for item in config.get("user_whitelist", [])]
        target_list = user_whitelist if target_type == "用户" else group_whitelist

        if target_id in target_list:
            yield event.plain_result(f"⚠️ {target_id} 已在名单列表中。")
            return

        target_list.append(target_id)
        config["whitelist"] = group_whitelist
        config["user_whitelist"] = user_whitelist
        self.conf["whitelist_config"] = config
        self.conf.save_config()

        yield event.plain_result(f"✅ 已添加{target_type}白名单：{target_id}")

    async def del_whitelist(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ) -> AsyncGenerator[MessageEventResult, None]:
        """校验参数并从白名单移除用户或群组。"""
        cmd_type = cmd_type.strip()
        target_id = target_id.strip()
        if not cmd_type or not target_id:
            yield event.plain_result(
                "❌ 格式错误。\n用法：lm白名单删除 (用户/群组) (ID)"
        )
            return

        # 识别要删除的是用户白名单还是群组白名单。
        target_type: str | None = None
        if cmd_type in {"用户", "user"}:
            target_type = "用户"
        elif cmd_type in {"群组", "group"}:
            target_type = "群组"
        if target_type is None:
            yield event.plain_result("❌ 类型错误，请使用「用户」或「群组」。")
            return

        # 从配置中取出可修改列表，删除命中的 ID 后保存。
        config = dict(self.conf.get("whitelist_config", {}))
        group_whitelist = [str(item) for item in config.get("whitelist", [])]
        user_whitelist = [str(item) for item in config.get("user_whitelist", [])]
        target_list = user_whitelist if target_type == "用户" else group_whitelist

        if target_id not in target_list:
            yield event.plain_result(f"⚠️ {target_id} 不在名单列表中。")
            return

        target_list.remove(target_id)
        config["whitelist"] = group_whitelist
        config["user_whitelist"] = user_whitelist
        self.conf["whitelist_config"] = config
        self.conf.save_config()

        yield event.plain_result(f"🗑️ 已删除{target_type}白名单：{target_id}")

    async def list_whitelist(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """返回当前白名单配置状态。"""
        whitelist_config = self.conf.get("whitelist_config", {})

        # 列表命令只负责读取并格式化当前配置，不额外拆分状态构造函数。
        group_enabled = whitelist_config.get("enabled", False)
        user_enabled = whitelist_config.get("user_enabled", False)
        group_whitelist = [
            str(item) for item in whitelist_config.get("whitelist", [])
        ]
        user_whitelist = [
            str(item) for item in whitelist_config.get("user_whitelist", [])
        ]

        yield event.plain_result(
            f"""📋 白名单配置状态：
=========
🏢 群组限制：{"✅ 开启" if group_enabled else "⬜ 关闭"}
列表：{group_whitelist}
=========
👤 用户限制：{"✅ 开启" if user_enabled else "⬜ 关闭"}
列表：{user_whitelist}"""
        )
