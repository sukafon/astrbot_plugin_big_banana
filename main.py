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
        whitelist_config = self.conf.get("whitelist_config", {})
        self.group_whitelist_enabled = whitelist_config.get("enabled", False)
        self.group_whitelist = whitelist_config.get("whitelist", [])

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

        # 图片保存
        self.save_image = self.conf.get("save_image", False)

        # 默认参数
        def_params = self.conf.get("def_params", {})
        self.min_images = def_params.get("min_images", 1)
        self.max_images = def_params.get("max_images", 3)
        self.refer_images = def_params.get("refer_images", "")

        # 初始化工具类
        retry_config = self.conf.get("retry_config", {})
        proxy = self.conf.get("proxy", "")
        self.utils = Utils(
            retry_config=retry_config,
            def_params=def_params,
            proxy=proxy,
        )

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

        # 解析提示词配置
        self.prompt_dict = {}
        for item in self.conf.get("prompt", []):
            cmd_list, params = self.parsing_prompt_params(item)
            for cmd in cmd_list:
                self.prompt_dict[cmd] = params

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def main(self, event: AstrMessageEvent):
        """绘图命令消息入口"""

        # 取出所有 Plain 类型的组件拼接成纯文本内容。不知道为什么，总有At消息混入纯文本中。
        plain_components = [
            comp for comp in event.get_messages() if isinstance(comp, Comp.Plain)
        ]

        # 拼接成一个字符串
        if plain_components:
            message_str = " ".join(comp.text for comp in plain_components)
        else:
            message_str = event.message_str
        # 跳过空消息
        if not message_str.strip():
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

        # 白名单判断
        if (
            self.group_whitelist_enabled
            and event.unified_msg_origin not in self.group_whitelist
        ):
            logger.info(f"群 {event.unified_msg_origin} 不在白名单内，跳过处理")
            return

        # 检查API Key配置
        if not self.provider_list:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain("暂无可用模型提供商，请先在插件配置中启用"),
                ]
            )
            return

        # 返回信息
        yield event.chain_result(
            [
                Comp.Reply(id=event.message_obj.message_id),
                Comp.Plain("🎨 在画了，请稍等一会..."),
            ]
        )

        # 获取提示词配置
        params = self.prompt_dict.get(cmd, {})
        prompt = params.get("prompt", "anything")

        # 处理占位提示词
        if prompt == "anything":
            # 解析message_str获取自定义提示词
            _, params = self.parsing_prompt_params(message_str)
            prompt = params.get("prompt", "anything")
        logger.info(f"正在生成图片，提示词: {prompt[:60]}...")
        logger.debug(
            f"生成图片应用参数: { {k: v for k, v in params.items() if k != 'secret_field'} }"
        )

        # 处理图片
        image_urls = []
        # 收集图片URL
        for comp in event.get_messages():
            if isinstance(comp, Comp.Reply) and comp.chain:
                for quote in comp.chain:
                    if isinstance(quote, Comp.Image):
                        image_urls.append(quote.url)
            # 处理At对象的QQ头像（对于艾特机器人的问题，还没有特别好的解决方案）
            elif isinstance(comp, Comp.At) and comp.qq:
                image_urls.append(
                    f"https://q4.qlogo.cn/headimg_dl?dst_uin={comp.qq}&spec=640"
                )
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
                    image_urls.append(
                        f"https://q4.qlogo.cn/headimg_dl?dst_uin={comp.qq}&spec=640"
                    )
                if len(image_urls) >= min_required_images:
                    break

            # 如果图片数量仍然不足，取消息发送者头像
            if len(image_urls) < min_required_images:
                image_urls.append(
                    f"https://q4.qlogo.cn/headimg_dl?dst_uin={event.get_sender_id()}&spec=640"
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
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(
                        f"❌ 图片数量不足，最少需要 {min_required_images} 张图片，当前仅 {len(image_urls) + len(image_b64_list)} 张"
                    ),
                ]
            )
            return

        # 计算需要下载的图片数量
        append_count = max_allowed_images - len(image_b64_list)
        if append_count > 0:
            # 取前n张图片，下载并转换为Base64，追加到b64图片列表
            fetched = await self.utils.fetch_images(image_urls[:append_count])
            if fetched:
                image_b64_list.extend(fetched)
            if not image_b64_list:
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain("❌ 全部图片下载失败"),
                    ]
                )
                return
        else:
            logger.warning(
                f"参考图片数量超过或等于最大图片数量，将只使用前 {max_allowed_images} 张参考图片"
            )

        image_result = None
        err = None
        # 发起绘图请求
        for provider in self.provider_list:
            api_type = provider.get("api_type", "Gemini")
            api_url = provider.get(
                "api_url",
                "https://generativelanguage.googleapis.com/v1beta/models",
            )
            model = provider.get("model", "gemini-2.5-flash-image")

            key_list = provider.get("key", []).copy()
            if not key_list:
                logger.warning(
                    f"提供商 {provider.get('name', 'unknown')} 未配置API Key，请先在插件配置中添加或者关闭此提供商",
                )
                yield event.chain_result(
                    [
                        Comp.Reply(id=event.message_obj.message_id),
                        Comp.Plain(
                            f"❌ 提供商 {provider.get('name', 'unknown')} 未配置API Key，请先在插件配置中添加或者关闭此提供商"
                        ),
                    ]
                )
                return
            random.shuffle(key_list)
            for key in key_list:
                image_result, err = await self.utils.generate_images(
                    api_type=api_type,
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
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(err or "❌ 图片生成失败，响应中未包含图片数据"),
                ]
            )
            return
        # 假设它支持返回多张图片...
        reply_result = []
        for _, b64 in image_result:
            reply_result.append(Comp.Image.fromBase64(b64))
        yield event.chain_result(
            [
                Comp.Reply(id=event.message_obj.message_id),
                *reply_result,
            ]
        )
        # 保存图片到本地
        if self.save_image:
            for mime, b64 in image_result:
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

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await self.utils.close()
