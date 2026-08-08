from __future__ import annotations

from typing import TYPE_CHECKING, Any

import astrbot.api.message_components as Comp

from ..schemas import MAX_SIZE_B64_LEN, GenerationResult

if TYPE_CHECKING:
    from pathlib import Path

    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.message.components import BaseMessageComponent

    from ..drawing.saver import ImageSaver


def get_message_id(event: AstrMessageEvent) -> str | None:
    """安全获取事件关联的消息 ID，若不存在或不是有效 message_obj 则返回 None。"""
    message_obj = getattr(event, "message_obj", None)
    if message_obj is None:
        return None
    message_id = getattr(message_obj, "message_id", None)
    if message_id is None:
        return None
    id_str = str(message_id).strip()
    return id_str if id_str != "" else None


def build_reply_component(
    event: AstrMessageEvent,
    quote_reply_mode: str = "both",
    is_command: bool = True,
) -> BaseMessageComponent | None:
    """若满足配置的回复引用条件且事件中包含有效 message_id，则构造 Reply 引用组件，否则返回 None。"""
    if quote_reply_mode == "none":
        return None
    if is_command and quote_reply_mode == "tool_only":
        return None
    if not is_command and quote_reply_mode == "command_only":
        return None

    message_id = get_message_id(event)
    if message_id is not None:
        return Comp.Reply(id=message_id)
    return None


def build_message_chain(
    event: AstrMessageEvent,
    components: BaseMessageComponent | list[BaseMessageComponent] | None = None,
    quote_reply_mode: str = "both",
    is_command: bool = True,
) -> list[BaseMessageComponent]:
    """构造包含可选 Reply 引用和目标组件的完整消息组件列表。"""
    chain: list[BaseMessageComponent] = []
    if reply := build_reply_component(
        event, quote_reply_mode=quote_reply_mode, is_command=is_command
    ):
        chain.append(reply)
    if components is not None:
        if isinstance(components, list):
            chain.extend(components)
        else:
            chain.append(components)
    return chain


def build_result_message_chain(
    event: AstrMessageEvent,
    result: GenerationResult,
    url_only: bool = False,
    quote_reply_mode: str = "both",
    is_command: bool = True,
    temporary_paths: list[Path] | None = None,
    image_saver: ImageSaver | None = None,
    temp_dir: Path | str | None = None,
) -> list[BaseMessageComponent]:
    """构造适配平台限制的媒体生成结果消息链。"""
    msg_chain = build_message_chain(
        event,
        components=None,
        quote_reply_mode=quote_reply_mode,
        is_command=is_command,
    )
    result_urls = [url for url in result.urls if url is not None]
    video_urls = [video.url for video in result.videos if video.url]

    # 如果仅 url，这里尝试检查有无 url，无则报错
    if url_only:
        urls = video_urls or result_urls
        if urls:
            msg_chain.append(Comp.Plain("\n".join(urls)))
        else:
            msg_chain.append(Comp.Plain("❌ 生成失败：没有可用的媒体 URL"))
        return msg_chain

    if video_urls:
        msg_chain.extend(Comp.Video.fromURL(url) for url in video_urls)
        return msg_chain

    images_with_bytes = [image for image in result.images if image.bytes]
    # 对 telegram 做特殊处理
    platform_meta = getattr(event, "platform_meta", None)
    platform_name = getattr(platform_meta, "name", None) if platform_meta else None
    if platform_name == "telegram" and any(
        (image.base64 and len(image.base64) > MAX_SIZE_B64_LEN)
        for image in images_with_bytes
    ):
        if image_saver is not None and temp_dir is not None:
            save_results = image_saver.save_images_to_local(images_with_bytes, temp_dir)
            if temporary_paths is not None:
                temporary_paths.extend(path for _name, path in save_results)
            for name_, path_ in save_results:
                msg_chain.append(Comp.File(name=name_, file=str(path_)))
            return msg_chain

    # 其他平台目前默认不特殊处理图片大小限制
    if images_with_bytes:
        msg_chain.extend(
            Comp.Image.fromBase64(image.base64) for image in images_with_bytes
        )
    elif result_urls:
        msg_chain.append(Comp.Plain("\n".join(result_urls)))
    else:
        msg_chain.append(Comp.Plain("❌ 图片生成失败：响应中未包含图片数据"))
    return msg_chain
