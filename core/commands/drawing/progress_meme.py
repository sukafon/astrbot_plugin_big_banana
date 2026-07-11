from __future__ import annotations

import importlib
import os
import re
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.star.star import star_map

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


class ProgressMemeHandler:
    """负责处理并发送绘图进度的表情包。"""

    @staticmethod
    def parse_start_message(text: str) -> tuple[str, list[str]]:
        """解析文本，返回清洗后的文本和提取到的表情 tags。"""
        # 提取 tags
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

        clean_text = re.sub(
            r"<emotions>.*?</emotions>", "", text, flags=re.DOTALL | re.IGNORECASE
        ).strip()

        return clean_text, raw_tags
    async def get_meme(
        self, event: AstrMessageEvent, raw_tags: list[str]
    ) -> Comp.Image | None:
        """尝试从表情包插件匹配并获取进度表情图片组件。"""
        try:
            meme_manager = None
            meme_manager_module_name = None
            for star in star_map.values():
                if (
                    star.root_dir_name == "astrbot_plugin_meme_manager"
                    and star.star_cls
                    and star.module
                ):
                    meme_manager = star.star_cls
                    meme_manager_module_name = star.module.__name__
                    break

            if not meme_manager or not meme_manager_module_name:
                return None

            if not raw_tags:
                return None

            package_name = (
                meme_manager_module_name.rsplit(".", 1)[0]
                if "." in meme_manager_module_name
                else meme_manager_module_name
            )
            config_mod = importlib.import_module(f"{package_name}.config")
            handler_mod = importlib.import_module(
                f"{package_name}.backend.core.emotion_handler"
            )
            helper_mod = importlib.import_module(f"{package_name}.backend.core.helpers")

            selected_memes = await handler_mod.get_direct_trigger_memes(
                meme_manager, event, raw_tags
            )
            if not selected_memes:
                logger.debug(f"[BIG BANANA] meme_manager 未匹配到与标签 {raw_tags} 相关的表情包。")
                return None

            selected = selected_memes[0]
            filename = selected.get("filename")
            if not filename:
                logger.warning(f"[BIG BANANA] 解析表情文件名失败，数据中缺失 filename 字段: {selected}")
                return None

            meme_file = os.path.join(config_mod.MEMES_DIR, filename)
            if not os.path.exists(meme_file):
                logger.warning(f"[BIG BANANA] 匹配到的表情包文件不存在: {meme_file}")
                return None

            final_meme_file = helper_mod.convert_to_gif(meme_file, meme_manager)
            img = Comp.Image.fromFileSystem(final_meme_file)
            object.__setattr__(img, "sub_type", 1)
            return img
        except Exception as e:
            logger.warning(f"[BIG BANANA] 尝试从 meme_manager 获取表情包失败: {e}")
            return None
