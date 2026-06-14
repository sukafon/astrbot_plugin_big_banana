import asyncio
import itertools
import os

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .core import BaseProvider, Downloader, HttpManager, R2ImageHoster
from .core.dispatcher import ProviderDispatcher
from .core.data import (
    SUPPORTED_FILE_FORMATS_WITH_DOT,
    CommonConfig,
    ImageHostingConfig,
    PreferenceConfig,
    PromptConfig,
    ProviderConfig,
    SubBrainConfig,
)
from .core.llm_tools import (
    BigBananaAvatarTool,
    BigBananaPromptTool,
    BigBananaReferenceTool,
    remove_tools,
)
from .core.utils import clear_cache, read_file, save_images

# 提示词参数列表
PARAMS_LIST = [
    "min_images",
    "max_images",
    "refer_images",
    "image_size",
    "aspect_ratio",
    "google_search",
    "preset_append",
    "gather_mode",
    "providers",
    "n",
    "size",
    "url",
]

# 部分平台对单张图片大小有限制，超过限制需要作为文件发送
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
# 预计算 Base64 长度阈值 (向下取整)，base64编码约为原始数据的4/3倍
MAX_SIZE_B64_LEN = int(MAX_SIZE_BYTES * 4 / 3)

class BigBanana(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.refresh_config()

        # Data directory setup
        data_dir = StarTools.get_data_dir("astrbot_plugin_big_banana")
        self.refer_images_dir = data_dir / "refer_images"
        self.save_dir = data_dir / "save_images"
        # Temporary file directory
        self.temp_dir = data_dir / "temp_images"

        # Active task mapping
        self.running_tasks: dict[str, asyncio.Task] = {}

        # Cooldown mapping per group: {group_id: timestamp}
        self.group_cooldowns: dict[str, float] = {}

        # Instantiate Web API and register routes
        from .web.web_api import BigBananaWebApi

        self.web_api = BigBananaWebApi(self)
        self.web_api.register_routes()

    def refresh_config(self):
        """Refresh configuration attributes from updated self.conf."""
        # Initialize regular configuration and image generation configuration
        self.common_config = CommonConfig(**self.conf.get("common_config", {}))
        self.prompt_config = PromptConfig(**self.conf.get("prompt_config", {}))
        self.sub_brain_config = SubBrainConfig(**self.conf.get("sub_brain", {}))
        # Parameter alias list
        self.params_alias = self.conf.get("params_alias_map", {})
        # Initialize prompt configuration
        self.init_prompts()
        # Whitelist configuration
        self.whitelist_config = self.conf.get("whitelist_config", {})
        # Group whitelist
        self.group_whitelist_enabled = self.whitelist_config.get("enabled", False)
        self.group_whitelist = self.whitelist_config.get("whitelist", [])
        # User whitelist
        self.user_whitelist_enabled = self.whitelist_config.get("user_enabled", False)
        self.user_whitelist = self.whitelist_config.get("user_whitelist", [])

        # Prefix configuration
        prefix_config = self.conf.get("prefix_config", {})
        self.coexist_enabled = prefix_config.get("coexist_enabled", False)
        self.prefix_list = prefix_config.get("prefix_list", [])

        # Image persistence
        self.save_images = self.conf.get("save_images", {}).get("local_save", False)

        # Load custom avatar substitutions mapping from separate JSON file
        self.avatar_substitutions_map = {}
        data_dir = StarTools.get_data_dir("astrbot_plugin_big_banana")
        sub_path = data_dir / "avatar_substitutions.json"
        if sub_path.exists():
            import json

            try:
                with open(sub_path, encoding="utf-8") as f:
                    self.avatar_substitutions_map = json.load(f)
            except Exception:
                logger.warning("[BIG BANANA] Failed to load avatar_substitutions.json")

        # Update sub-configurations if instantiated
        if hasattr(self, "downloader"):
            self.preference_config = PreferenceConfig(
                **self.conf.get("preference_config", {})
            )
            self.image_hosting_config = ImageHostingConfig(
                **self.conf.get("image_hosting", {})
            )
            self.downloader.common_config = self.common_config
            self.image_hoster.config = self.image_hosting_config

        # Check and update LLM tools registry dynamically
        if getattr(self, "context", None):
            remove_tools(self.context)
            if self.conf.get("llm_tool_settings", {}).get("llm_tool_enabled", False):
                self.context.add_llm_tools(BigBananaReferenceTool(plugin=self))
                logger.info(
                    "已注册函数调用工具: banana_image_generation_with_reference"
                )
                self.context.add_llm_tools(BigBananaAvatarTool(plugin=self))
                logger.info("已注册函数调用工具: banana_image_generation_with_avatar")
                self.context.add_llm_tools(BigBananaPromptTool(plugin=self))
                logger.info("已注册函数调用工具: banana_preset_prompt")

    async def initialize(self):
        """Optional async initialization method called after class instantiation."""
        # Initialize file directories
        os.makedirs(self.refer_images_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        if self.save_images:
            os.makedirs(self.save_dir, exist_ok=True)

        # Instantiate services
        self.preference_config = PreferenceConfig(
            **self.conf.get("preference_config", {})
        )
        self.image_hosting_config = ImageHostingConfig(
            **self.conf.get("image_hosting", {})
        )
        self.http_manager = HttpManager()
        curl_session = self.http_manager._get_curl_session()
        aiohttp_session = self.http_manager._get_aiohttp_session()
        self.downloader = Downloader(curl_session, self.common_config)
        self.image_hoster = R2ImageHoster(aiohttp_session, self.image_hosting_config)
        self.dispatcher = ProviderDispatcher(self)

        # Register provider type instances
        self.init_providers()

    def init_providers(self):
        """解析提供商配置"""
        # 提供商配置列表
        self.providers_config: dict[str, ProviderConfig] = {}
        # 提供商实例映射
        self.provider_map: dict[str, BaseProvider] = {}

    def init_prompts(self):
        """初始化提示词配置"""
        # 预设提示词列表
        self.prompt_list = self.conf.get("prompt", [])
        self.prompt_dict = {}
        self.params_alias_map = {}
        # 处理参数别名映射
        for item in self.params_alias:
            alias, _, param = item.partition(":")
            if alias and param:
                self.params_alias_map[alias] = param
            elif not alias or not param:
                logger.warning(
                    f"参数别名映射配置错误，未指定参数名称：{item}，跳过处理"
                )
        # 解析预设提示词
        for item in self.prompt_list:
            cmd_list, params = self.parsing_prompt_params(item)
            for cmd in cmd_list:
                self.prompt_dict[cmd] = params

    def parsing_prompt_params(self, prompt: str) -> tuple[list[str], dict]:
        """解析提示词中的参数，若没有指定参数则使用默认值填充。必须是包括命令和参数的完整提示词"""

        # 以空格分割单词
        tokens = prompt.split()
        # 第一个单词作为命令或命令列表
        cmd_raw = tokens[0]

        # 解析多触发词
        if cmd_raw.startswith("[") and cmd_raw.endswith("]"):
            # 移除括号并按逗号分割
            cmd_list = cmd_raw[1:-1].split(",")
        else:
            cmd_list = [cmd_raw]

        # 迭代器跳过第一个单词
        tokens_iter = iter(tokens[1:])
        # 提示词传递参数列表
        params = {}
        # 过滤后的提示词单词列表
        filtered = []

        # 解析参数
        while True:
            token = next(tokens_iter, None)
            if token is None:
                break
            if token.startswith("--"):
                key = token[2:]
                # 处理参数别称映射
                if key in self.params_alias_map:
                    key = self.params_alias_map[key]
                # 仅处理已知参数
                if key in PARAMS_LIST:
                    value = next(tokens_iter, None)
                    if value is None:
                        params[key] = True
                        break
                    value = value.strip()
                    if value.startswith("--"):
                        params[key] = True
                        # 将被提前迭代的单词放回迭代流的最前端
                        tokens_iter = itertools.chain([value], tokens_iter)
                        continue
                    elif value.lower() == "true":
                        params[key] = True
                    elif value.lower() == "false":
                        params[key] = False
                    # 处理字符串数字类型
                    elif value.isdigit():
                        params[key] = int(value)
                    else:
                        params[key] = value
                    continue
            filtered.append(token)

        # 重新组合提示词
        prompt = " ".join(filtered)
        params["prompt"] = prompt
        return cmd_list, params

    # === 辅助功能：判断管理员，用于静默跳出 ===
    def is_global_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为全局管理员"""
        admin_ids = self.context.get_config().get("admins_id", [])
        # logger.info(f"全局管理员列表：{admin_ids}")
        return event.get_sender_id() in admin_ids

    # === 管理指令：白名单管理 ===
    @filter.command("lm白名单添加", alias={"lmawl"})
    async def add_whitelist_command(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ):
        """lm白名单添加 <用户/群组> <ID>"""
        if not self.is_global_admin(event):
            logger.info(
                f"用户 {event.get_sender_id()} 试图执行管理员命令 lm白名单添加，权限不足"
            )
            return

        if not cmd_type or not target_id:
            yield event.plain_result(
                "❌ 格式错误。\n用法：lm白名单添加 (用户/群组) (ID)"
            )
            return

        msg_type = ""
        if cmd_type in ["用户", "user"] and target_id not in self.user_whitelist:
            msg_type = "用户"
            self.user_whitelist.append(target_id)
        elif cmd_type in ["群组", "group"] and target_id not in self.group_whitelist:
            msg_type = "群组"
            self.group_whitelist.append(target_id)
        elif cmd_type not in ["用户", "user", "群组", "group"]:
            yield event.plain_result("❌ 类型错误，请使用「用户」或「群组」。")
            return
        else:
            yield event.plain_result(f"⚠️ {target_id} 已在名单列表中。")
            return

        self.conf.save_config()
        yield event.plain_result(f"✅ 已添加{msg_type}白名单：{target_id}")

    @filter.command("lm白名单删除", alias={"lmdwl"})
    async def del_whitelist_command(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ):
        """lm白名单删除 <用户/群组> <ID>"""
        if not self.is_global_admin(event):
            logger.info(
                f"用户 {event.get_sender_id()} 试图执行管理员命令 lm白名单删除，权限不足"
            )
            return

        if not cmd_type or not target_id:
            yield event.plain_result(
                "❌ 格式错误。\n用法：lm白名单删除 (用户/群组) (ID)"
            )
            return

        if cmd_type in ["用户", "user"] and target_id in self.user_whitelist:
            msg_type = "用户"
            self.user_whitelist.remove(target_id)
        elif cmd_type in ["群组", "group"] and target_id in self.group_whitelist:
            msg_type = "群组"
            self.group_whitelist.remove(target_id)
        elif cmd_type not in ["用户", "user", "群组", "group"]:
            yield event.plain_result("❌ 类型错误，请使用「用户」或「群组」。")
            return
        else:
            yield event.plain_result(f"⚠️ {target_id} 不在名单列表中。")
            return

        self.conf.save_config()
        yield event.plain_result(f"🗑️ 已删除{msg_type}白名单：{target_id}")

    @filter.command("lm白名单列表", alias={"lmwll"})
    async def list_whitelist_command(self, event: AstrMessageEvent):
        """lm白名单列表"""
        if not self.is_global_admin(event):
            logger.info(
                f"用户 {event.get_sender_id()} 试图执行管理员命令 lm白名单列表，权限不足"
            )
            return

        msg = f"""📋 白名单配置状态：
=========
🏢 群组限制：{"✅ 开启" if self.group_whitelist_enabled else "⬜ 关闭"}
列表：{self.group_whitelist}
=========
👤 用户限制：{"✅ 开启" if self.user_whitelist_enabled else "⬜ 关闭"}
列表：{self.user_whitelist}"""

        yield event.plain_result(msg)

    # === 管理指令：添加/更新提示词 ===
    @filter.command("lm添加", alias={"lma"})
    async def add_prompt_command(self, event: AstrMessageEvent, trigger_word: str = ""):
        """lm添加 <触发词> <提示词内容>"""
        if not self.is_global_admin(event):
            logger.info(
                f"用户 {event.get_sender_id()} 试图执行管理员命令 lm添加，权限不足"
            )
            return

        if not trigger_word:
            yield event.plain_result("❌ 格式错误：lm添加 (触发词)")
            return

        yield event.plain_result(
            f"🍌 正在为触发词 「{trigger_word}」 添加/更新提示词\n✦ 请在60秒内输入完整的提示词内容（不含触发词，包含参数）\n✦ 输入「取消」可取消操作。"
        )

        # 记录操作员账号
        operator_id = event.get_sender_id()

        @session_waiter(timeout=60, record_history_chains=False)  # type: ignore
        async def waiter(controller: SessionController, event: AstrMessageEvent):
            # 判断消息来源是否是同一用户（同一用户不需要鉴权了吧）
            if event.get_sender_id() != operator_id:
                return

            if event.message_str.strip() == "取消":
                await event.send(event.plain_result("🍌 操作已取消。"))
                controller.stop()
                return

            build_prompt = f"{trigger_word} {event.message_str.strip()}"

            action = "添加"
            # 直接从字典中查重
            if trigger_word in self.prompt_dict:
                action = "更新"
                # 从提示词列表中找出对应项进行更新
                for i, v in enumerate(self.prompt_list):
                    cmd, _, prompt_str = v.strip().partition(" ")
                    if cmd == trigger_word:
                        self.prompt_list[i] = build_prompt
                        break
                    # 处理多触发词
                    if cmd.startswith("[") and cmd.endswith("]"):
                        # 移除括号并按逗号分割
                        cmd_list = cmd[1:-1].split(",")
                        if trigger_word in cmd_list:
                            # 将这个提示词从多触发提示词中移除
                            cmd_list.remove(trigger_word)
                            # 重新构建提示词字符串
                            if len(cmd_list) == 1:
                                # 仅剩一个触发词，改为单触发词形式
                                new_config_item = f"{cmd_list[0]} {prompt_str}"
                            else:
                                new_cmd = "[" + ",".join(cmd_list) + "]"
                                new_config_item = f"{new_cmd} {prompt_str}"
                            self.prompt_list[i] = new_config_item
                            # 最后为新的提示词添加一项
                            self.prompt_list.append(build_prompt)
                            break
            # 新增提示词
            else:
                self.prompt_list.append(build_prompt)

            self.conf.save_config()
            self.init_prompts()
            await event.send(
                event.plain_result(f"✅ 已成功{action}提示词：「{trigger_word}」")
            )
            controller.stop()

        try:
            await waiter(event)
        except TimeoutError as _:
            yield event.plain_result("❌ 超时了，操作已取消！")
        except Exception as e:
            logger.error(f"大香蕉添加提示词出现错误: {e}", exc_info=True)
            yield event.plain_result("❌ 处理时发生了一个内部错误。")
        finally:
            event.stop_event()

    @filter.command("lm列表", alias={"lml"})
    async def list_prompts_command(self, event: AstrMessageEvent):
        """lm列表"""
        if not self.is_global_admin(event):
            logger.info(
                f"用户 {event.get_sender_id()} 试图执行管理员命令 lm列表，权限不足"
            )
            return

        prompts = list(self.prompt_dict.keys())
        if not prompts:
            yield event.plain_result("当前没有预设提示词。")
            return

        msg = "📜 当前预设提示词列表：\n" + "、".join(prompts)
        yield event.plain_result(msg)

    @filter.command("lm提示词", alias={"lmc", "lm详情"})
    async def prompt_details(self, event: AstrMessageEvent, trigger_word: str):
        """获取提示词详情字符串"""
        if trigger_word not in self.prompt_dict:
            yield event.plain_result(f"❌ 未找到提示词：「{trigger_word}」")
            return

        params = self.prompt_dict[trigger_word]
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

    @filter.command("lm删除", alias={"lmd"})
    async def del_prompt_command(self, event: AstrMessageEvent, trigger_word: str = ""):
        """lm删除 <触发词>"""
        if not self.is_global_admin(event):
            logger.info(
                f"用户 {event.get_sender_id()} 试图执行管理员命令 lm删除，权限不足"
            )
            return

        if not trigger_word:
            yield event.plain_result("❌ 格式错误：lm删除 (触发词)")
            return

        if trigger_word not in self.prompt_dict:
            yield event.plain_result(f"❌ 未找到提示词：「{trigger_word}」")
            return

        # 从提示词列表中找出对应项进行更新
        for i, v in enumerate(self.prompt_list):
            cmd, _, prompt_str = v.strip().partition(" ")
            if cmd == trigger_word:
                del self.prompt_list[i]
                self.init_prompts()
                self.conf.save_config()
                yield event.plain_result(f"🗑️ 已删除提示词：「{trigger_word}」")
                return
            # 处理多触发词
            if cmd.startswith("[") and cmd.endswith("]"):
                # 移除括号并按逗号分割
                cmd_list = cmd[1:-1].split(",")
                if trigger_word not in cmd_list:
                    continue

                yield event.plain_result(
                    "⚠️ 检测到该提示词为多触发词配置，请选择删除方案\nA. 单独删除该触发词\nB. 删除该多触发词\nC. 取消操作"
                )

                # 删除多触发词时，进行二次确认
                @session_waiter(timeout=30, record_history_chains=False)  # type: ignore
                async def waiter(
                    controller: SessionController, event: AstrMessageEvent
                ):
                    # 先鉴权
                    if not self.is_global_admin(event):
                        logger.info(
                            f"用户 {event.get_sender_id()} 试图执行管理员命令 lm删除，权限不足"
                        )
                        return

                    # 获取用户回复内容
                    reply_content = event.message_str.strip().upper()
                    if reply_content not in ["A", "B", "C"]:
                        await event.send(
                            event.plain_result("❌ 请输入有效的选项：A、B 或 C。")
                        )
                        return

                    if reply_content == "C":
                        await event.send(event.plain_result("🍌 操作已取消。"))
                        controller.stop()
                        return
                    if reply_content == "B":
                        # 删除整个多触发词配置
                        del self.prompt_list[i]
                        await event.send(
                            event.plain_result(f"🗑️ 已删除多触发提示词：{cmd}")
                        )
                        self.conf.save_config()
                        controller.stop()
                        return
                    if reply_content == "A":
                        # 将这个提示词从多触发提示词中移除
                        cmd_list.remove(trigger_word)
                        # 重新构建提示词字符串
                        if len(cmd_list) == 1:
                            # 仅剩一个触发词，改为单触发词形式
                            new_config_item = f"{cmd_list[0]} {prompt_str}"
                        else:
                            new_cmd = "[" + ",".join(cmd_list) + "]"
                            new_config_item = f"{new_cmd} {prompt_str}"
                        self.prompt_list[i] = new_config_item
                        # 最后更新字典
                        del self.prompt_dict[trigger_word]
                        # 更新内存字典
                        self.init_prompts()
                        await event.send(
                            event.plain_result(
                                f"🗑️ 已从多触发提示词中移除：「{trigger_word}」"
                            )
                        )
                        self.conf.save_config()
                        controller.stop()
                        return

                try:
                    await waiter(event)
                except TimeoutError as _:
                    yield event.plain_result("❌ 超时了，操作已取消！")
                except Exception as e:
                    logger.error(f"大香蕉删除提示词出现错误: {e}", exc_info=True)
                    yield event.plain_result("❌ 处理时发生了一个内部错误。")
                finally:
                    event.stop_event()
        else:
            logger.error(
                f"提示词列表和提示词字典不一致，未找到提示词：「{trigger_word}」"
            )
            yield event.plain_result(f"❌ 未找到提示词：「{trigger_word}」")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_message(self, event: AstrMessageEvent):
        """绘图命令消息入口"""

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
        for prefix in self.prefix_list:
            if message_str.startswith(prefix):
                message_str = message_str.removeprefix(prefix).lstrip()
                matched_prefix = True
                break

        # 若未@机器人且未开启混合模式，且配置了前缀列表但消息未匹配到任何前缀，则跳过处理
        if (
            not event.is_at_or_wake_command
            and not self.coexist_enabled
            and self.prefix_list
            and not matched_prefix
        ):
            return

        cmd = message_str.split(" ", 1)[0]
        # 检查命令是否在提示词配置中
        if cmd not in self.prompt_dict:
            return

        # 群白名单判断
        if (
            self.group_whitelist_enabled
            and event.unified_msg_origin not in self.group_whitelist
        ):
            logger.info(f"群 {event.unified_msg_origin} 不在白名单内，跳过处理")
            return

        # 用户白名单判断
        if (
            self.user_whitelist_enabled
            and event.get_sender_id() not in self.user_whitelist
        ):
            logger.info(f"用户 {event.get_sender_id()} 不在白名单内，跳过处理")
            return

        # 冷却时间判断 (Group Cooldown)
        group_id = event.get_group_id()
        cooldown_seconds = getattr(self.preference_config, "group_cooldown", 0)
        if group_id and cooldown_seconds > 0:
            import time

            last_sent_time = self.group_cooldowns.get(group_id, 0)
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
        params = self.prompt_dict.get(cmd, {}).copy()
        # 先从预设提示词参数字典字典中取出提示词
        preset_prompt = params.get("prompt", "{{user_text}}")

        # 处理预设提示词补充参数preset_append
        if (
            params.get("preset_append", self.common_config.preset_append)
            and "{{user_text}}" not in preset_prompt
        ):
            preset_prompt += " {{user_text}}"

        # 检查预设提示词中是否包含动态参数占位符
        if "{{user_text}}" in preset_prompt:
            # 存在动态参数，解析用户消息
            _, user_params = self.parsing_prompt_params(message_str)
            # 将用户参数差分覆盖预设参数
            params.update(user_params)
            # 解析到用户的提示词和配置参数
            user_prompt = user_params.get("prompt", "anything").strip()
            # 替换占位符，更新提示词
            new_prompt = preset_prompt.replace("{{user_text}}", user_prompt)
            params["prompt"] = new_prompt

        # 处理收集模式
        image_urls = []
        if params.get("gather_mode", self.prompt_config.gather_mode):
            # 记录操作员账号
            operator_id = event.get_sender_id()
            # 取消标记
            is_cancel = False
            yield event.plain_result(f"""📝 绘图收集模式已启用：
文本：{params["prompt"]}
图片：{len(image_urls)} 张

💡 继续发送图片或文本，或者：
• 发送「开始」开始生成
• 发送「取消」取消操作
• 60 秒内有效
""")

            @session_waiter(timeout=60, record_history_chains=False)  # type: ignore
            async def waiter(controller: SessionController, event: AstrMessageEvent):
                nonlocal is_cancel
                # 判断消息来源是否是同一用户
                if event.get_sender_id() != operator_id:
                    return

                if event.message_str.strip() == "取消":
                    is_cancel = True
                    await event.send(event.plain_result("✅ 操作已取消。"))
                    controller.stop()
                    return
                if event.message_str.strip() == "开始":
                    controller.stop()
                    return
                # 开始收集文本和图片
                for comp in event.get_messages():
                    if isinstance(comp, Comp.Plain) and comp.text:
                        # 追加文本到提示词
                        params["prompt"] += " " + comp.text.strip()
                    elif isinstance(comp, Comp.Image) and comp.url:
                        image_urls.append(comp.url)
                    elif (
                        isinstance(comp, Comp.File)
                        and comp.url
                        and comp.url.startswith("http")
                        and comp.url.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
                    ):
                        image_urls.append(comp.url)
                await event.send(
                    event.plain_result(f"""📝 绘图追加模式已收集内容：
文本：{params["prompt"]}
图片：{len(image_urls)} 张

💡 继续发送图片或文本，或者：
• 发送「开始」开始生成
• 发送「取消」取消操作
• 60 秒内有效
""")
                )
                controller.keep(timeout=60, reset_timeout=True)

            try:
                await waiter(event)
            except TimeoutError as _:
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

        logger.info(f"正在生成图片，提示词: {params['prompt'][:60]}")
        logger.debug(
            f"生成图片应用参数: { {k: v for k, v in params.items() if k != 'prompt'} }"
        )
        # 调用作图任务
        task = asyncio.create_task(self.job(event, params, image_urls=image_urls))
        task_id = event.message_obj.message_id
        self.running_tasks[task_id] = task

        try:
            results, err_msg = await task
            result_urls = getattr(task, "result_urls", None)
            if err_msg:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(f"❌ 图片生成失败：{err_msg}"),
                    ]
                )
                return

            # 组装消息链
            msg_chain = self.build_message_chain(
                event,
                results or [],
                result_urls=result_urls,
                url_only=bool(params.get("url", False)),
            )

            yield event.chain_result(msg_chain)

            # 记录成功后的冷却时间
            if group_id and cooldown_seconds > 0:
                import time

                self.group_cooldowns[group_id] = time.time()
        except asyncio.CancelledError:
            logger.info(f"{task_id} 任务被取消")
            return
        finally:
            self.running_tasks.pop(task_id, None)
            # 目前只有 telegram 平台需要清理缓存
            if event.platform_meta.name == "telegram":
                clear_cache(self.temp_dir)

    async def job(
        self,
        event: AstrMessageEvent,
        params: dict,
        image_urls: list[str] | None = None,
        referer_id: list[str] | None = None,
        is_llm_tool: bool = False,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        """负责参数处理、调度提供商、保存图片等逻辑，返回图片和错误信息（通过 task.result_urls 传递 URL）"""
        # 副脑提示词优化
        if self.sub_brain_config.enabled and is_llm_tool:
            orig_prompt = params.get("prompt", "")
            if orig_prompt:
                provider_id = self.sub_brain_config.provider_id
                if not provider_id:
                    umo = event.unified_msg_origin if event else None
                    try:
                        using_provider = self.context.get_using_provider(umo)
                        provider_id = using_provider.meta().id if using_provider else None
                    except Exception as e:
                        logger.warning(f"[BIG BANANA] 获取当前会话正在使用的提供商失败: {e}")

                if provider_id:
                    try:
                        logger.info(
                            f"[BIG BANANA] 正在使用副脑进行提示词优化，模型提供商: {provider_id}"
                        )
                        resp = await self.context.llm_generate(
                            chat_provider_id=provider_id,
                            prompt=orig_prompt,
                            system_prompt=self.sub_brain_config.system_prompt,
                        )
                        optimized_prompt = resp.completion_text
                        if optimized_prompt:
                            optimized_prompt = optimized_prompt.strip()
                            logger.info(
                                f"[BIG BANANA] 副脑优化完成，优化后提示词: {optimized_prompt}"
                            )
                            params["prompt"] = optimized_prompt
                        else:
                            logger.warning("[BIG BANANA] 副脑优化返回了空文本，将使用原始提示词")
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
            if target_id in self.avatar_substitutions_map:
                ref_imgs = self.avatar_substitutions_map[target_id]
            elif target_id == self_id:
                for key in (self_id, "bot", "self"):
                    if key in self.avatar_substitutions_map:
                        ref_imgs = self.avatar_substitutions_map[key]
                        break
            elif target_id in ("bot", "self"):
                for key in (self_id, "bot", "self"):
                    if key in self.avatar_substitutions_map:
                        ref_imgs = self.avatar_substitutions_map[key]
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
                    (qq == reply_sender_id and self.preference_config.skip_quote_first)
                    or (
                        qq == self_id
                        and event.is_at_or_wake_command
                        and self.preference_config.skip_at_first
                    )  # Skipped first At wake
                    or (
                        qq == self_id
                        and self.preference_config.skip_llm_at_first
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

        min_required_images = params.get("min_images", self.prompt_config.min_images)
        max_allowed_images = params.get("max_images", self.prompt_config.max_images)
        # If total images are less than minimum required, fall back to sender avatar
        if (
            len(image_urls) + len(bot_local_refs) < min_required_images
            and event.platform_meta.name == "aiocqhttp"
        ):
            image_urls.append(
                f"https://q.qlogo.cn/g?b=qq&s=0&nk={event.get_sender_id()}"
            )

        # Base64 images list
        image_b64_list: list[tuple[str, str]] = []

        # Load local bot reference images first
        for filename in bot_local_refs:
            if len(image_b64_list) >= max_allowed_images:
                break
            filename = filename.strip()
            if filename:
                path = self.refer_images_dir / filename
                mime_type, b64_data = await asyncio.to_thread(read_file, path)
                if mime_type and b64_data:
                    image_b64_list.append((mime_type, b64_data))

        # Load refer_images configurations
        refer_images = params.get("refer_images", self.prompt_config.refer_images)
        if refer_images:
            for filename in refer_images.split(","):
                if len(image_b64_list) >= max_allowed_images:
                    break
                filename = filename.strip()
                if filename:
                    path = self.refer_images_dir / filename
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
            fetched = await self.downloader.fetch_images(image_urls[:append_count])
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
        if getattr(self.preference_config, "enable_drawing_message", True):
            import re

            text = self.preference_config.drawing_message
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
                            import importlib

                            # Get the package name by removing the module name suffix (e.g. '.main')
                            if "." in meme_manager_module_name:
                                package_name = meme_manager_module_name.rsplit(".", 1)[
                                    0
                                ]
                            else:
                                package_name = meme_manager_module_name

                            config_mod = importlib.import_module(
                                f"{package_name}.config"
                            )
                            MEMES_DIR = config_mod.MEMES_DIR

                            handler_mod = importlib.import_module(
                                f"{package_name}.backend.core.emotion_handler"
                            )
                            get_direct_trigger_memes = (
                                handler_mod.get_direct_trigger_memes
                            )

                            helper_mod = importlib.import_module(
                                f"{package_name}.backend.core.helpers"
                            )
                            convert_to_gif = helper_mod.convert_to_gif

                            selected_memes = await get_direct_trigger_memes(
                                meme_manager, event, raw_tags
                            )
                            if selected_memes:
                                meme_file = os.path.join(MEMES_DIR, selected_memes[0])
                                final_meme_file = convert_to_gif(
                                    meme_file, meme_manager
                                )
                                img = Comp.Image.fromFileSystem(final_meme_file)
                                object.__setattr__(img, "sub_type", 1)
                                if clean_text:
                                    await event.send(
                                        MessageChain([Comp.Plain(clean_text)])
                                    )
                                await event.send(MessageChain([img]))
                                sent_meme = True
                except Exception as e:
                    logger.warning(
                        f"[BIG BANANA] 尝试从 meme_manager 获取表情包失败: {e}"
                    )

            if not sent_meme:
                await event.send(MessageChain().message(clean_text))

        # 调度提供商生成图片
        images_result, err, result_urls = await self.dispatcher.dispatch(
            event=event, params=params, image_b64_list=image_b64_list
        )

        # 再次检查图片结果是否为空
        valid_results = [(mime, b64) for mime, b64 in (images_result or []) if b64]

        # 确定最终的 result_urls
        final_urls = result_urls
        if params.get("url", False) and not final_urls and valid_results:
            final_urls = await self._upload_results_for_url_mode(valid_results)

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
        if self.save_images:
            save_images(valid_results, self.save_dir)

        return valid_results, None

    async def _upload_results_for_url_mode(
        self, results: list[tuple[str, str]]
    ) -> list[str] | None:
        if not self.image_hoster.is_enabled():
            logger.warning("[BIG BANANA] 未配置图床上传，无法将图片转换为URL返回")
            return None
        try:
            return await self.image_hoster.upload_images(results)
        except Exception as e:
            logger.error(f"[BIG BANANA] 图床上传失败: {e}")
            return None
    def build_message_chain(
        self,
        event: AstrMessageEvent,
        results: list[tuple[str, str]],
        result_urls: list[str] | None = None,
        url_only: bool = False,
    ) -> list[BaseMessageComponent]:
        """构建消息链"""
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
            save_results = save_images(results, self.temp_dir)
            for name_, path_ in save_results:
                msg_chain.append(Comp.File(name=name_, file=str(path_)))
            return msg_chain

        # 其他平台直接发送图片
        msg_chain.extend(Comp.Image.fromBase64(b64) for _, b64 in results)
        return msg_chain

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        # 取消所有生成任务
        for task in list(self.running_tasks.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.running_tasks.values(), return_exceptions=True)
        self.running_tasks.clear()
        # 清理网络客户端会话
        await self.http_manager.close_session()
        # 卸载函数调用工具
        remove_tools(self.context)
