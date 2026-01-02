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

from .core import BaseProvider, Downloader, HttpManager
from .core.data import (
    CommonConfig,
    PreferenceConfig,
    PromptConfig,
    ProviderConfig,
)
from .core.llm_tools import BigBananaPromptTool, BigBananaTool, remove_tools
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
]

# 支持的文件格式
SUPPORTED_FILE_FORMATS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
)

# 提供商配置键列表
provider_list = ["main_provider", "back_provider", "back_provider2"]

# 部分平台对单张图片大小有限制，超过限制需要作为文件发送
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
# 预计算 Base64 长度阈值 (向下取整)，base64编码约为原始数据的4/3倍
MAX_SIZE_B64_LEN = int(MAX_SIZE_BYTES * 4 / 3)


class BigBanana(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        # 初始化常规配置和图片生成配置
        self.common_config = CommonConfig(**self.conf.get("common_config", {}))
        self.prompt_config = PromptConfig(**self.conf.get("prompt_config", {}))
        # 参数别名列表
        self.params_alias = self.conf.get("params_alias_map", {})
        # 初始化提示词配置
        self.init_prompts()
        # 白名单配置
        self.whitelist_config = self.conf.get("whitelist_config", {})
        # 群组白名单，列表是引用类型
        self.group_whitelist_enabled = self.whitelist_config.get("enabled", False)
        self.group_whitelist = self.whitelist_config.get("whitelist", [])
        # 用户白名单
        self.user_whitelist_enabled = self.whitelist_config.get("user_enabled", False)
        self.user_whitelist = self.whitelist_config.get("user_whitelist", [])

        # 前缀配置
        prefix_config = self.conf.get("prefix_config", {})
        self.coexist_enabled = prefix_config.get("coexist_enabled", False)
        self.prefix_list = prefix_config.get("prefix_list", [])

        # 数据目录
        data_dir = StarTools.get_data_dir("astrbot_plugin_big_banana")
        self.refer_images_dir = data_dir / "refer_images"
        self.save_dir = data_dir / "save_images"
        # 临时文件目录
        self.temp_dir = data_dir / "temp_images"

        # 图片持久化
        self.save_images = self.conf.get("save_images", {}).get("local_save", False)

        # 正在运行的任务映射
        self.running_tasks: dict[str, asyncio.Task] = {}

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        # 初始化文件目录
        os.makedirs(self.refer_images_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        if self.save_images:
            os.makedirs(self.save_dir, exist_ok=True)

        # 实例化类
        self.preference_config = PreferenceConfig(
            **self.conf.get("preference_config", {})
        )
        self.http_manager = HttpManager()
        curl_session = self.http_manager._get_curl_session()
        self.downloader = Downloader(curl_session, self.common_config)

        # 注册提供商类型实例
        self.init_providers()

        # 检查配置是否启用函数调用工具
        if self.conf.get("llm_tool_settings", {}).get("llm_tool_enabled", False):
            self.context.add_llm_tools(BigBananaTool(plugin=self))
            logger.info("已注册函数调用工具: banana_image_generation")
            self.context.add_llm_tools(BigBananaPromptTool(plugin=self))
            logger.info("已注册函数调用工具: banana_preset_prompt")

    def init_providers(self):
        """解析提供商配置"""
        # 默认启用的提供商
        self.def_enabled_providers: list[str] = []
        # 提供商配置列表
        self.providers_config: dict[str, ProviderConfig] = {}
        # 提供商实例映射
        self.provider_map: dict[str, BaseProvider] = {}
        # 注册提供商+实例化提供商类
        for item in provider_list:
            provider = self.conf.get(item, {})
            api_type = provider["api_type"]
            provider_cls = BaseProvider.get_provider_class(api_type)
            if provider_cls is None:
                logger.warning(
                    f"未找到提供商类型对应的提供商类：{api_type}，跳过该提供商配置"
                )
                continue
            # 添加到提供商配置列表
            self.providers_config[provider["api_name"]] = ProviderConfig(**provider)
            # 实例化提供商类
            self.provider_map[api_type] = provider_cls(
                config=self.conf,
                common_config=self.common_config,
                prompt_config=self.prompt_config,
                session=self.http_manager._get_curl_session(),
                downloader=self.downloader,
            )
            # 将启用的提供商加入默认提供商列表中
            if provider.get("enabled", False):
                api_name = provider.get("api_name", "")
                if not api_name:
                    logger.warning(f"提供商类型 {api_type} 未设置提供商名称，无法启用")
                    continue
                if api_name in self.def_enabled_providers:
                    logger.warning(
                        f"提供商名称 {api_name} 已存在于启用列表中，跳过重复添加"
                    )
                    continue
                self.def_enabled_providers.append(api_name)
                logger.info(f"已启用提供商：{api_name}")

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
                logger.warning(f"参数别名映射配置错误，未指定参数名称：{item}，跳过处理")
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
                        and comp.url.lower().endswith(SUPPORTED_FILE_FORMATS)
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
            if not results or err_msg:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(f"❌ 图片生成失败：{err_msg}"),
                    ]
                )
                return

            # 组装消息链
            msg_chain = self.build_message_chain(event, results)

            yield event.chain_result(msg_chain)
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
        """负责参数处理、调度提供商、保存图片等逻辑，返回图片b64列表或错误信息"""
        # 收集图片URL，后面统一处理
        if image_urls is None:
            image_urls = []

        if referer_id is None:
            referer_id = []
        # 小标记，用于优化At头像。当At对象是被引用消息的发送者时，跳过一次。
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
                        and quote.url.lower().endswith(SUPPORTED_FILE_FORMATS)
                    ):
                        image_urls.append(quote.url)
            # 处理At对象的QQ头像（对于艾特机器人的问题，还没有特别好的解决方案）
            elif (
                isinstance(comp, Comp.At)
                and comp.qq
                and event.platform_meta.name == "aiocqhttp"
            ):
                qq = str(comp.qq)
                self_id = event.get_self_id()
                if not skipped_at_qq and (
                    # 如果At对象是被引用消息的发送者，跳过一次
                    (qq == reply_sender_id and self.preference_config.skip_quote_first)
                    or (
                        qq == self_id
                        and event.is_at_or_wake_command
                        and self.preference_config.skip_at_first
                    )  # 通过At唤醒机器人，跳过一次
                    or (
                        qq == self_id
                        and self.preference_config.skip_llm_at_first
                        and is_llm_tool
                    )  # 通过At唤醒机器人，且是函数调用工具，跳过一次
                ):
                    skipped_at_qq = True
                    continue
                image_urls.append(f"https://q.qlogo.cn/g?b=qq&s=0&nk={comp.qq}")
            elif isinstance(comp, Comp.Image) and comp.url:
                image_urls.append(comp.url)
            elif (
                isinstance(comp, Comp.File)
                and comp.url
                and comp.url.startswith("http")
                and comp.url.lower().endswith(SUPPORTED_FILE_FORMATS)
            ):
                image_urls.append(comp.url)

        # 处理referer_id参数，获取指定用户头像
        if is_llm_tool and referer_id and event.platform_meta.name == "aiocqhttp":
            for target_id in referer_id:
                target_id = target_id.strip()
                if target_id:
                    build_url = f"https://q.qlogo.cn/g?b=qq&s=0&nk={target_id}"
                    if build_url not in image_urls:
                        image_urls.append(
                            f"https://q.qlogo.cn/g?b=qq&s=0&nk={target_id}"
                        )

        min_required_images = params.get("min_images", self.prompt_config.min_images)
        max_allowed_images = params.get("max_images", self.prompt_config.max_images)
        # 如果图片数量不满足最小要求，且消息平台是Aiocqhttp，取消息发送者头像作为参考图片
        if (
            len(image_urls) < min_required_images
            and event.platform_meta.name == "aiocqhttp"
        ):
            image_urls.append(
                f"https://q.qlogo.cn/g?b=qq&s=0&nk={event.get_sender_id()}"
            )

        # 图片b64列表
        image_b64_list: list[tuple[str, str]] = []
        # 处理 refer_images 参数
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
                logger.error("全部参考图片下载失败")
                return None, "全部参考图片下载失败"
        elif append_count < 0:
            logger.warning(
                f"参考图片数量超过最大允许数量 {max_allowed_images}，跳过下载图片步骤"
            )

        # 发送绘图中提示
        await event.send(MessageChain().message("🎨 在画了，请稍等一会..."))

        # 调度提供商生成图片
        images_result, err = await self._dispatch(
            params=params, image_b64_list=image_b64_list
        )

        # 再次检查图片结果是否为空
        valid_results = [(mime, b64) for mime, b64 in (images_result or []) if b64]

        if not valid_results:
            if not err:
                err = "图片生成失败：响应中未包含图片数据"
                logger.error(err)
            return None, err

        # 保存图片到本地
        if self.save_images:
            save_images(valid_results, self.save_dir)

        return valid_results, None

    async def _dispatch(
        self,
        params: dict,
        image_b64_list: list[tuple[str, str]] = [],
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        """提供商调度器"""
        err = None

        # 处理需要启用的提供商列表参数
        active_providers = params.get("providers", self.def_enabled_providers)
        if isinstance(active_providers, str):
            active_providers = active_providers.split(",")

        # 调度提供商
        for i, api_name in enumerate(active_providers):
            # 获取提供商配置
            provider_config = self.providers_config.get(api_name)
            if not provider_config:
                logger.warning(f"未找到提供商配置：{api_name}，跳过该提供商")
                continue
            # 获取提供商实例，并调用生成方法
            images_result, err = await self.provider_map[
                provider_config.api_type
            ].generate_images(
                provider_config=provider_config,
                params=params,
                image_b64_list=image_b64_list,
            )
            if images_result:
                logger.info(f"{provider_config.api_name} 图片生成成功")
                return images_result, None
            if i < len(active_providers) - 1:
                logger.warning(
                    f"{provider_config.api_name} 生成图片失败，尝试使用下一个提供商..."
                )

        # 处理错误信息
        if len(active_providers) == 0:
            err = "当前无可用提供商，请检查插件配置。"
            logger.error(err)
        return None, err

    def build_message_chain(
        self, event: AstrMessageEvent, results: list[tuple[str, str]]
    ) -> list[BaseMessageComponent]:
        """构建消息链"""
        msg_chain: list[BaseMessageComponent] = [
            Comp.Reply(id=event.message_obj.message_id)
        ]
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
