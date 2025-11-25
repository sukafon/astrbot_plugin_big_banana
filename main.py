import base64
import itertools
import mimetypes
import os
import random
from datetime import datetime
from pathlib import Path

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

        # 数据目录
        self.refer_images_dir = (
            Path(StarTools.get_data_dir("astrbot_plugin_big_banana")) / "refer_images"
        )
        self.save_dir = (
            Path(StarTools.get_data_dir("astrbot_plugin_big_banana")) / "save_images"
        )

        # 图片保存
        self.save_image = self.conf.get("save_image", False)

        # 默认参数
        def_params = self.conf.get("def_params", {})
        self.min_images = def_params.get("min_images", 1)
        self.max_images = def_params.get("max_images", 3)
        self.refer_images = def_params.get("refer_images", "")

        # 初始化工具类
        main_provider = self.conf.get("main_provider", {})
        self.key_list = main_provider.get("key", [])
        network_config = self.conf.get("network_config", {})
        self.max_retry = self.conf.get("retry", 2)
        self.utils = Utils(
            main_provider=main_provider,
            network_config=network_config,
            def_params=def_params,
            max_retry=self.max_retry,
        )

    def parsing_prompt_params(self, prompt: str) -> tuple[str, dict]:
        """解析提示词中的参数，若没有指定参数则使用默认值填充。必须是完整的提示词，第一个单词是命令。"""

        # 以空格分割单词
        tokens = prompt.split()
        # 第一个单词作为命令
        cmd = tokens[0]
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
                    else:
                        params[key] = value
                    continue
            filtered.append(token)

        # 重新组合提示词
        prompt = " ".join(filtered)
        params["prompt"] = prompt
        return cmd, params

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        # 初始化文件目录
        os.makedirs(self.refer_images_dir, exist_ok=True)
        os.makedirs(self.save_dir, exist_ok=True)
        # 解析提示词配置
        self.prompt_dict = {}
        for item in self.conf.get("prompt", []):
            cmd, params = self.parsing_prompt_params(item)
            self.prompt_dict[cmd] = params

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def main(self, event: AstrMessageEvent):
        """绘图命令消息入口"""

        # 取出所有 Plain 类型的组件拼接成纯文本内容，避免event.message_str出现的At组件被字符串化导致命令位置错误
        # 尽管如此，仍然存在某些情况下@xxx被归类为Plain组件字符串的问题。暂时不打算为了解决这个问题引入更高的复杂性。
        plain_components = [
            comp for comp in event.get_messages() if isinstance(comp, Comp.Plain)
        ]

        # 拼接成一个字符串，与event.message_str处理方法一样，但是只处理Plain组件。
        if plain_components:
            message_str = " ".join(comp.text for comp in plain_components)
        else:
            message_str = event.message_str
        # 跳过空消息
        if not message_str.strip():
            return
        # 解析命令
        cmd = message_str.split(" ", 1)[0]

        # 添加前缀唤醒词支持
        prefix = self.conf.get("prefix", "").strip()
        if (
            prefix
            and not message_str.startswith(prefix)
            and not event.is_at_or_wake_command
        ):
            return
        if prefix:
            message_str = message_str.removeprefix(prefix).lstrip()
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
        logger.debug(f"生成图片应用参数: {params}")

        # 处理图片
        image_urls = []
        # 收集图片URL
        for comp in event.get_messages():
            if isinstance(comp, Comp.Reply):
                if comp.chain is not None:
                    for quote in comp.chain:
                        if isinstance(quote, Comp.Image):
                            image_urls.append(quote.url)
            elif isinstance(comp, Comp.Image):
                image_urls.append(comp.url)

        # 如果没有图片，且消息平台是Aiocqhttp，取QQ头像作为参考图片
        if (
            len(image_urls) < self.min_images
            and event.platform_meta.name == "aiocqhttp"
        ):
            # 优先取At对象头像
            for comp in event.get_messages():
                if isinstance(comp, Comp.At):
                    qq = str(comp.qq)
                    image_urls.append(
                        f"https://q4.qlogo.cn/headimg_dl?dst_uin={qq}&spec=640"
                    )
                if len(image_urls) >= self.min_images:
                    break

            # 如果图片数量仍然不足，取消息发送者头像
            if len(image_urls) < self.min_images:
                image_urls.append(
                    f"https://q4.qlogo.cn/headimg_dl?dst_uin={event.get_sender_id()}&spec=640"
                )

        # 图片b64列表，每个元素是 (mime_type, b64_data) 元组
        image_b64_list = []
        # 处理 refer_images 参数
        refer_images = params.get("refer_images", self.refer_images)
        if refer_images:
            for filename in refer_images.split(","):
                if len(image_b64_list) >= self.max_images:
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
        if len(image_urls) + len(image_b64_list) < self.min_images:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(
                        f"❌ 图片数量不足，最少需要 {self.min_images} 张图片，当前仅 {len(image_urls) + len(image_b64_list)} 张"
                    ),
                ]
            )
            return

        # 计算需要下载的图片数量
        max_images = params.get("max_images", self.max_images)
        append_count = max_images - len(image_b64_list)
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
                f"参考图片数量超过或等于最大图片数量，将只使用前 {max_images} 张参考图片"
            )

        # 发起绘图请求
        key_list = self.key_list.copy()
        random.shuffle(key_list)
        for key in key_list:
            image_b64, err = await self.utils.generate_images(
                api_key=key,
                prompt=prompt,
                image_b64_list=image_b64_list,
                params=params,
            )
            if err is None:
                break
            logger.warning("图片生成失败，尝试更换Key重试...")

        # 发送消息
        if err or image_b64 is None:
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(err or "❌ 图片生成失败，响应中未包含图片数据"),
                ]
            )
            return
        # 假设它支持返回多张图片...
        reply_result = []
        for _, b64 in image_b64:
            reply_result.append(Comp.Image.fromBase64(b64))
        yield event.chain_result(
            [
                Comp.Reply(id=event.message_obj.message_id),
                *reply_result,
            ]
        )
        # 保存图片到本地或图床
        if self.save_image:
            for mime, b64 in image_b64:
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
