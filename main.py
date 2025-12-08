import base64
import itertools
import mimetypes
import os
import random
from datetime import datetime

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .llm_tools import BigBananaTool
from .utils import Utils

PARAMS_LIST = [
    "min_images",
    "max_images",
    "refer_images",
    "image_size",
    "aspect_ratio",
    "google_search",
    "only_image_response",
]


class BigBanana(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config

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
        self.refer_images_dir = (
            StarTools.get_data_dir("astrbot_plugin_big_banana") / "refer_images"
        )
        self.save_dir = (
            StarTools.get_data_dir("astrbot_plugin_big_banana") / "save_images"
        )

        # 预设提示词列表
        self.prompt_list = self.conf.get("prompt", [])

        # 图片保存
        self.save_image = self.conf.get("save_image", False)

        # 默认参数
        def_params = self.conf.get("def_params", {})
        self.min_images = def_params.get("min_images", 1)
        self.max_images = def_params.get("max_images", 3)
        self.refer_images = def_params.get("refer_images", "")

        # 偏好配置
        preference_settings = self.conf.get("preference_settings", {})
        self.skip_at_first = preference_settings.get("skip_at_first", False)
        self.skip_quote_first = preference_settings.get("skip_quote_first", True)

        # 初始化工具类
        retry_config = self.conf.get("retry_config", {})
        proxy = self.conf.get("proxy", "")
        self.utils = Utils(
            retry_config=retry_config, def_params=def_params, proxy=proxy
        )

        # 检查配置是否启用函数调用工具
        if self.conf.get("llm_tool_settings", {}).get("llm_tool_enabled", False):
            logger.info("已注册函数调用工具: banana_image_generation")
            self.context.add_llm_tools(BigBananaTool(instance=self))

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
                    elif value == "true":
                        params[key] = True
                    elif value == "false":
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

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        # 初始化文件目录
        os.makedirs(self.refer_images_dir, exist_ok=True)
        os.makedirs(self.save_dir, exist_ok=True)

        # 构建可用提供商列表
        self.provider_list = []
        # 解析主提供商配置
        main_provider = self.conf.get("main_provider", {})
        if main_provider.get("enabled", False):
            self.provider_list.append(main_provider)
        # 解析备用提供商配置
        back_provider = self.conf.get("back_provider", {})
        if back_provider.get("enabled", False):
            self.provider_list.append(back_provider)

        # 初始化提示词配置
        self.init_prompts()

    def init_prompts(self):
        """初始化提示词配置"""
        self.prompt_dict = {}
        for item in self.prompt_list:
            cmd_list, params = self.parsing_prompt_params(item)
            for cmd in cmd_list:
                self.prompt_dict[cmd] = params

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

        msg = f"""
📋 白名单配置状态：
=========
🏢 群组限制：{"✅ 开启" if self.group_whitelist_enabled else "⬜ 关闭"}
列表：{self.group_whitelist}
=========
👤 用户限制：{"✅ 开启" if self.user_whitelist_enabled else "⬜ 关闭"}
列表：{self.user_whitelist}
"""
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
            # 先鉴权
            # if not self.is_global_admin(event):
            #     logger.info(
            #         f"用户 {event.get_sender_id()} 试图执行管理员命令 lm添加，权限不足"
            #     )
            #     return
            # 判断消息来源是否是同一用户
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

    @filter.command("lm详情", alias={"lmc"})
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
                    yield event.plain_result("超时了，操作已取消！")
                except Exception as e:
                    logger.error(f"大香蕉删除提示词出现错误: {e}", exc_info=True)
                    yield event.plain_result("处理时发生了一个内部错误。")
                finally:
                    event.stop_event()
        else:
            logger.error(
                f"提示词列表和提示词字典不一致，未找到提示词：「{trigger_word}」"
            )
            yield event.plain_result(f"❌ 未找到提示词：「{trigger_word}」")

    async def _dispatch_generate_image(
        self, event: AstrMessageEvent, params: dict, prompt: str
    ):
        """负责参数处理、调度提供商、密钥轮询等逻辑"""
        # 收集图片URL，后面统一处理
        image_urls = []
        # 小标记，用于优化At头像。当At对象是被引用消息的发送者时，跳过一次。
        skipped_at_qq = False
        reply_sender_id = ""
        for comp in event.get_messages():
            if isinstance(comp, Comp.Reply) and comp.chain:
                reply_sender_id = str(comp.sender_id)
                for quote in comp.chain:
                    if isinstance(quote, Comp.Image):
                        image_urls.append(quote.url)
            # 处理At对象的QQ头像（对于艾特机器人的问题，还没有特别好的解决方案）
            elif (
                isinstance(comp, Comp.At)
                and comp.qq
                and event.platform_meta.name == "aiocqhttp"
            ):
                # 如果At对象是被引用消息的发送者，跳过一次
                if not skipped_at_qq and (
                    (str(comp.qq) == reply_sender_id and self.skip_at_first)
                    or (str(comp.qq) == event.get_self_id() and self.skip_quote_first)
                ):
                    skipped_at_qq = True
                    continue
                image_urls.append(f"https://q.qlogo.cn/g?b=qq&s=0&nk={comp.qq}")
            elif isinstance(comp, Comp.Image) and comp.url:
                image_urls.append(comp.url)

        min_required_images = params.get("min_images", self.min_images)
        max_allowed_images = params.get("max_images", self.max_images)
        # 如果图片数量不满足最小要求，且消息平台是Aiocqhttp，取QQ头像作为参考图片
        if (
            len(image_urls) < min_required_images
            and event.platform_meta.name == "aiocqhttp"
        ):
            # 优先取At对象头像
            for comp in event.get_messages():
                if isinstance(comp, Comp.At) and comp.qq:
                    image_urls.append(f"https://q.qlogo.cn/g?b=qq&s=0&nk={comp.qq}")
                if len(image_urls) >= min_required_images:
                    break

            # 如果图片数量仍然不足，取消息发送者头像
            if len(image_urls) < min_required_images:
                image_urls.append(
                    f"https://q.qlogo.cn/g?b=qq&s=0&nk={event.get_sender_id()}"
                )

        # 图片b64列表，每个元素是 (mime_type, b64_data) 元组
        image_b64_list = []
        # 处理 refer_images 参数
        refer_images = params.get("refer_images", self.refer_images)
        if refer_images:
            for filename in refer_images.split(","):
                if len(image_b64_list) >= max_allowed_images:
                    break
                filename = filename.strip()
                if filename:
                    try:
                        with open(self.refer_images_dir / filename, "rb") as f:
                            file_data = f.read()
                            mime_type, _ = mimetypes.guess_type(filename)
                            b64_data = base64.b64encode(file_data).decode("utf-8")
                            image_b64_list.append((mime_type, b64_data))
                    except Exception as e:
                        logger.error(f"读取参考图片 {filename} 失败: {e}")

        # 判断图片数量是否满足最小要求
        if len(image_urls) + len(image_b64_list) < min_required_images:
            return [
                Comp.Reply(id=event.message_obj.message_id),
                Comp.Plain(
                    f"🍌 图片数量不足，最少需要 {min_required_images} 张图片，当前仅 {len(image_urls) + len(image_b64_list)} 张"
                ),
            ]

        # 检查图片数量是否超过最大允许数量，不超过则可从url中下载图片
        append_count = max_allowed_images - len(image_b64_list)
        if append_count > 0 and image_urls:
            # 取前n张图片，下载并转换为Base64，追加到b64图片列表
            if len(image_b64_list) + len(image_urls) > max_allowed_images:
                logger.warning(
                    f"参考图片数量超过或等于最大图片数量，将只使用前 {max_allowed_images} 张参考图片"
                )
            fetched = await self.utils.fetch_images(image_urls[:append_count])
            if fetched:
                image_b64_list.extend(fetched)

            # 如果 min_required_images 为 0，列表为空是允许的
            if not image_b64_list and min_required_images > 0:
                return [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain("❌ 全部图片下载失败"),
                ]

        image_result = None
        err = None
        # 发起绘图请求
        for provider in self.provider_list:
            # 读取提供商配置
            api_type = provider.get("api_type", "Gemini")
            api_url = provider.get(
                "api_url",
                "https://generativelanguage.googleapis.com/v1beta/models",
            )
            model = provider.get("model", "gemini-2.5-flash-image")
            stream = provider.get("stream", False)

            # 浅拷贝，确保线程安全
            key_list = provider.get("key", []).copy()
            # 随机打乱Key顺序，避免每次都从第一个Key开始使用
            random.shuffle(key_list)

            if not key_list:
                warn_msg = f"提供商 {provider.get('name', 'unknown')} 未配置API Key，请先在插件配置中添加或者关闭此提供商"
                logger.warning(warn_msg)
                return [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(f"❌ {warn_msg}"),
                ]

            for key in key_list:
                image_result, err = await self.utils.generate_images(
                    api_type=api_type,
                    stream=stream,
                    api_url=api_url,
                    model=model,
                    api_key=key,
                    prompt=prompt,
                    image_b64_list=image_b64_list,
                    params=params,
                )
                if image_result:
                    break
                logger.warning("图片生成失败，尝试更换Key重试...")
            if image_result:
                break

        # 发送消息
        if err or not image_result:
            return [
                Comp.Reply(id=event.message_obj.message_id),
                Comp.Plain(err or "❌ 图片生成失败，响应中未包含图片数据"),
            ]

        # 假设它支持返回多张图片
        reply_result = []
        for mime, b64 in image_result:
            reply_result.append(Comp.Image.fromBase64(b64))
            # 保存图片到本地
            if self.save_image:
                # 构建文件名
                now = datetime.now()
                current_time_str = (
                    now.strftime("%Y%m%d%H%M%S") + f"{int(now.microsecond / 1000):03d}"
                )
                ext = mimetypes.guess_extension(mime) or ".jpg"
                file_name = f"banana_{current_time_str}{ext}"
                # 构建文件保存路径
                save_path = self.save_dir / file_name
                # 转换成bytes
                image_bytes = base64.b64decode(b64)
                # 保存到文件系统
                with open(save_path, "wb") as f:
                    f.write(image_bytes)
                logger.info(f"图片已保存到 {save_path}")

        return [
            Comp.Reply(id=event.message_obj.message_id),
            *reply_result,
        ]

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def main(self, event: AstrMessageEvent):
        """绘图命令消息入口"""

        message_str = event.message_str

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

        # 返回信息
        yield event.plain_result("🎨 在画了，请稍等一会...")

        # 获取提示词配置 (使用 .copy() 防止修改污染全局预设)
        params = self.prompt_dict.get(cmd, {}).copy()
        # 先从预设提示词参数字典字典中取出提示词
        prompt = params.get("prompt", "{{user_text}}")

        # 检查预设提示词中是否包含动态参数占位符
        # 注意：anything 占位符可能会被废弃
        if "{{user_text}}" in prompt or prompt == "anything":
            # 存在动态参数，解析用户消息
            _, user_params = self.parsing_prompt_params(message_str)
            # 将用户参数差分覆盖预设参数
            params.update(user_params)
            # 解析到用户的提示词和配置参数
            user_prompt = user_params.get("prompt", "")
            # 打算移除 anything 占位符，但是缺乏必要性，暂时保留
            if prompt == "anything":
                # logger.info(
                #     "检测到预设提示词使用了即将废弃的占位符 anything，请尽快更新为 {{user_text}} 占位符"
                # )
                prompt = user_prompt
            # 替换占位符，更新提示词
            prompt = prompt.replace("{{user_text}}", user_prompt)

        logger.info(f"正在生成图片，提示词: {prompt[:60]}...")
        logger.debug(
            f"生成图片应用参数: { {k: v for k, v in params.items() if k != 'prompt'} }"
        )
        msg_chain = await self._dispatch_generate_image(event, params, prompt)
        yield event.chain_result(msg_chain)

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await self.utils.close()
