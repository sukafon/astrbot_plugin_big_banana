from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..schemas import SUPPORTED_FILE_FORMATS_WITH_DOT

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ...main import BigBanana
    from ..schemas import ImageResource


class ImageCollector:
    """图片收集实例"""

    def __init__(
        self,
        *,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        is_llm_tool: bool = False,
    ) -> None:
        """保存本次任务所需图片"""
        self.plugin = plugin
        self.event = event
        self.params = params
        self.is_llm_tool = is_llm_tool
        self.platform_name = event.platform_meta.name
        self.client = getattr(event, "client", None) or getattr(event, "bot", None)

        self.min_images = params.get("min_images", plugin.params_config.min_images)
        self.max_images = params.get("max_images", plugin.params_config.max_images)

        # 收集普通图片url(无协议路径也做了兼容处理，视为url)，不包括需要备注索引的url
        self.urls: list[str | Path] = []
        # 将用户ID与读取到的头像的url做映射，顺便完成去重
        self.avatar_mappings: dict[str, str | Path] = {}
        # Supplemental avatars are always appended after annotated avatars.
        self.supplement_urls: list[str | Path] = []
        # 图片下载/读取后的缓存对象
        self.images: list[ImageResource] = []

        # 图片补充信息
        self.image_supplement_infos: list[str] = []

        # 防止重复调用
        self._refer_images_loaded = False
        self._processed_events: set[str] = set()
        self._processed_image_urls: set[str | Path] = set()
        self._supplemented_avatar_ids: set[str] = set()

    async def add_refer_images(self) -> None:
        """获取参考图片文件"""

        # 防止重复调用
        if self._refer_images_loaded:
            return
        self._refer_images_loaded = True

        # 读取参考图片文件
        refer_images = self.params.get(
            "refer_images", self.plugin.params_config.refer_images
        )
        if refer_images:
            allowed_root = self.plugin.refer_images_dir.resolve()
            for filename in refer_images.split(","):
                filename = filename.strip()
                if filename:
                    try:
                        path = (allowed_root / filename).resolve()
                    except (OSError, RuntimeError, ValueError) as e:
                        logger.warning(
                            f"[BIG BANANA] 参考图片路径无效，已跳过：{filename}，错误：{e}"
                        )
                        continue
                    if path != allowed_root and allowed_root not in path.parents:
                        logger.warning(
                            f"[BIG BANANA] 参考图片超出 refer_images 目录，已跳过：{filename}"
                        )
                        continue
                    self.urls.append(path)

    async def add_msg_images(self, event: AstrMessageEvent | None = None):
        """
        获取消息、引用消息中的图片、文件图片和 At 头像。
        考虑到收集模式事件，这里设计为允许独立传递event。
        """
        if event is None:
            event = self.event
        # 防止同一个 event 重复操作
        event_id = event.message_obj.message_id
        if event_id in self._processed_events:
            logger.info(f"[BIG BANANA] event {event_id} 已处理过，跳过收集图片")
            return
        self._processed_events.add(event_id)

        # At头像跳过标记
        skipped_at_avatar = False
        reply_sender_id = ""
        for comp in event.get_messages():
            # 引用回复中仅读取图片
            if isinstance(comp, Comp.Reply) and comp.chain:
                reply_sender_id = str(comp.sender_id)
                for quote in comp.chain:
                    if isinstance(quote, Comp.Image) and quote.url:
                        self.urls.append(quote.url)
                    elif isinstance(quote, Comp.File):
                        # File不会自动缓存
                        is_valid_url = quote.url and quote.url.lower().endswith(
                            SUPPORTED_FILE_FORMATS_WITH_DOT
                        )
                        is_valid_name = quote.name and quote.name.lower().endswith(
                            SUPPORTED_FILE_FORMATS_WITH_DOT
                        )
                        if is_valid_url or is_valid_name:
                            file_ref = quote.url or quote.file_
                            if file_ref:
                                self.urls.append(file_ref)
            # 收集@头像
            elif isinstance(comp, Comp.At) and comp.qq:
                user_id = str(comp.qq)
                self_id = event.get_self_id()
                if not skipped_at_avatar and (
                    # 如果At对象是被引用消息的发送者，跳过一次
                    (
                        user_id == reply_sender_id
                        and self.plugin.preference_config.skip_quote_first
                    )
                    or (
                        user_id == self_id
                        and event.is_at_or_wake_command
                        and self.plugin.preference_config.skip_at_first
                    )  # 通过At唤醒机器人，跳过一次
                    or (
                        user_id == self_id
                        and self.plugin.preference_config.skip_llm_at_first
                        and self.is_llm_tool
                    )  # 通过At唤醒机器人，且是函数调用工具，跳过一次
                ):
                    skipped_at_avatar = True
                    continue
                if user_id:
                    avatar_url = await self._get_avatar_url(user_id, event)
                    if avatar_url:
                        self.avatar_mappings[user_id] = avatar_url
            elif isinstance(comp, Comp.Image) and comp.url:
                self.urls.append(comp.url)
            elif isinstance(comp, Comp.File):
                is_valid_url = (
                    comp.url
                    and comp.url.startswith(("http://", "https://"))
                    and comp.url.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
                )
                is_valid_name = comp.name and comp.name.lower().endswith(
                    SUPPORTED_FILE_FORMATS_WITH_DOT
                )

                if is_valid_url or is_valid_name:
                    file_ref = comp.url or comp.file_
                    if file_ref:
                        self.urls.append(file_ref)
            else:
                continue

    async def supplement_avatars(self, *, use_downloaded_images: bool = False) -> None:
        """补充可获取的用户头像。

        Args:
            use_downloaded_images: Whether to base the limit on successfully downloaded
                images instead of collected image references.
        """
        image_count = (
            len(self.images) if use_downloaded_images else len(self.get_final_urls())
        )
        for user_id in (self.event.get_sender_id(), self.event.get_self_id()):
            if image_count >= self.min_images:
                break
            # 确保这个头像没有被收集过
            if (
                not user_id
                or user_id in self.avatar_mappings
                or user_id in self._supplemented_avatar_ids
            ):
                continue

            avatar_url = await self._get_avatar_url(user_id, self.event)
            if avatar_url:
                self.supplement_urls.append(avatar_url)
                self._supplemented_avatar_ids.add(user_id)
                image_count += 1

    async def add_explicit_references(self, references: list[str]) -> None:
        """Add explicit image references or platform user avatars for llm tool.

        Args:
            references: Image URLs, local paths, or numeric user IDs.
        """
        for reference in references:
            ref = reference.strip()
            if not ref:
                continue
            # 带上@表示头像引用是提示词约定的，否则可能会被识别出url或者path
            if ref.startswith("@") or ref.isdigit():
                user_id = ref.removeprefix("@")
                avatar_url = await self._get_avatar_url(user_id, self.event)
                if not avatar_url:
                    logger.warning(
                        f"[BIG BANANA] 无法获取 {self.event.platform_meta.name} "
                        f"用户 {user_id} 的头像，已跳过该引用"
                    )
                else:
                    self.avatar_mappings[user_id] = avatar_url
                continue

            # 支持 Giftia 图片哈希 (16位 xxh3 或 32位 md5)
            import re
            if re.fullmatch(r"[a-fA-F0-9]{16}|[a-fA-F0-9]{32}", ref):
                try:
                    from astrbot.api.star import StarTools
                    giftia_cache_file = StarTools.get_data_dir("astrbot_plugin_giftia") / "media_cache" / ref
                    if giftia_cache_file.exists():
                        self.urls.append(giftia_cache_file)
                        continue
                except Exception:
                    pass

            self.urls.append(ref)

    async def _get_avatar_url(
        self, user_id: str, event: AstrMessageEvent
    ) -> str | None:
        """Resolve an avatar URL through the active platform client.

        Args:
            user_id: Platform-specific user ID.
            event: Event that owns the platform client.

        Returns:
            A downloadable avatar URL, or None when it cannot be resolved.
        """
        # 处理头像映射，能匹配到直接取结果，管它什么平台
        avatar_imgs = self.plugin.avatar_map.get(user_id)
        if avatar_imgs:
            return random.choice(avatar_imgs)

        if self.platform_name == "aiocqhttp":
            # 对于qq来说，直接构建url就足够了，避免走客户端群成员->陌生人两次api额外调用
            # 使用isdigit()排除AtAll的情况，AtAll继承自Comp.At
            return self.qq_avatar_url(user_id) if user_id.isdigit() else None

        if self.platform_name == "telegram":
            if self.client is None:
                logger.warning("[BIG BANANA] Telegram 客户端不可用，无法获取头像")
                return None
            try:
                # telegram允许用户以文本形式@，同样会被解析成At类型，但是不含用户数字ID，无法获取图片
                # 只有一种情况例外: @me即为自己，用户名不区分大小写。
                if not user_id.isdigit():
                    if (
                        not event.get_self_id()
                        or user_id.casefold() != event.get_self_id().casefold()
                    ):
                        # 不是自己，返回None
                        return None
                    current_user = await self.client.get_me()
                    # 统一类型
                    user_id = str(current_user.id)

                # 取最近一张图片
                photos = await self.client.get_user_profile_photos(
                    user_id=int(user_id), limit=1
                )
                # 每一项结构是[小图, 中图, 大图]，确保第一项不是空数组
                if not photos.photos or not photos.photos[0]:
                    return None
                # 取大图
                avatar_file = await photos.photos[0][-1].get_file()
                # 返回文件路径，Astrbot应该已经内置了TG客户端，不存在文件系统隔离问题
                return str(avatar_file.file_path) if avatar_file.file_path else None
            except Exception as e:
                logger.warning(
                    f"[BIG BANANA] 获取 Telegram 用户 {user_id} 头像失败: {e}"
                )
                return None

        if self.platform_name == "discord":
            if not user_id.isdigit():
                return None
            if self.client is None:
                logger.warning("[BIG BANANA] Discord 客户端不可用，无法获取头像")
                return None
            try:
                target_id = int(user_id)
                avatar_user = self.client.get_user(target_id)
                if avatar_user is None:
                    avatar_user = await self.client.fetch_user(target_id)

                avatar = getattr(avatar_user, "display_avatar", None) or getattr(
                    avatar_user, "avatar", None
                )
                avatar_url = getattr(avatar, "url", None)
                return str(avatar_url) if avatar_url else None
            except Exception as e:
                logger.warning(
                    f"[BIG BANANA] 获取 Discord 用户 {user_id} 头像失败: {e}"
                )
                return None

        return None

    def get_final_urls(self) -> list[str | Path]:
        """汇总所有图片url"""
        return [
            *self.urls,
            *self.avatar_mappings.values(),
            *self.supplement_urls,
        ]

    def check_urls_limit(self) -> bool:
        """检查图片url是否满足最低要求，如果不满足则返回 False，否则返回 True"""
        if len(self.get_final_urls()) < self.min_images:
            return False
        return True

    def check_images_limit(self) -> bool:
        """检查下载的图片数量是否满足最低要求"""
        return len(self.images) >= self.min_images

    async def fetch_collected_images(self) -> list[ImageResource]:
        """批量下载/读取已收集的图片，并转换为 ImageResource 列表（增量下载与缓存）"""
        pending_urls: list[str | Path] = []
        for image_url in self.get_final_urls():
            if (
                image_url not in self._processed_image_urls
                and image_url not in pending_urls
            ):
                pending_urls.append(image_url)

        # 下载前按剩余图片槽位截断，避免先下载全部引用再丢弃超出上限的图片。
        remaining_slots = max(self.max_images - len(self.images), 0)
        if len(pending_urls) > remaining_slots:
            skipped_urls = pending_urls[remaining_slots:]
            self._processed_image_urls.update(skipped_urls)
            pending_urls = pending_urls[:remaining_slots]
            logger.warning(
                f"[BIG BANANA] 本次最多还能收集 {remaining_slots} 张图片，"
                f"已跳过 {len(skipped_urls)} 个超出 max_images 限制的图片引用。"
            )
        if not pending_urls:
            return self.images

        # 仅对LLM工具才限制内网访问
        if self.is_llm_tool:
            allowed_roots = [
                self.plugin.data_dir,
                Path(get_astrbot_temp_path()),
            ]
            try:
                from astrbot.api.star import StarTools
                giftia_cache = StarTools.get_data_dir("astrbot_plugin_giftia") / "media_cache"
                allowed_roots.append(giftia_cache)
            except Exception:
                pass

            fetched_results = await self.plugin.downloader.fetch_images_keep_none(
                pending_urls,
                restrict_private_network=(
                    self.plugin.llm_tools_config.llm_tool_restrict_private_network
                ),
                allowed_local_roots=tuple(allowed_roots),
                local_base_dir=self.plugin.refer_images_dir,
            )
        else:
            fetched_results = await self.plugin.downloader.fetch_images_keep_none(
                pending_urls
            )
        image_bytes_to_index = {
            image.bytes: image_index
            for image_index, image in enumerate(self.images, start=1)
        }
        for original_url, fetched in zip(pending_urls, fetched_results):
            self._processed_image_urls.add(original_url)
            if fetched is None:
                logger.warning(
                    f"[BIG BANANA] 图片下载/读取失败，已被跳过: {original_url}"
                )
                continue

            existing_index = image_bytes_to_index.get(fetched.bytes)
            if existing_index is not None:
                logger.info("[BIG BANANA] 图片内容重复，已跳过1张图片。")
                self._record_image_supplement_info(original_url, existing_index)
                continue

            if len(self.images) >= self.max_images:
                logger.warning(
                    f"[BIG BANANA] 已收集 {len(self.images)} 张图片，"
                    f"达到图片数量上限，跳过本次图片收集。"
                )
                continue

            fetched.url = original_url
            self.images.append(fetched)
            image_index = len(self.images)
            image_bytes_to_index[fetched.bytes] = image_index
            self._record_image_supplement_info(original_url, image_index)

        return self.images

    @staticmethod
    def qq_avatar_url(target_id: str) -> str:
        """构造 QQ 头像 URL。"""
        return f"https://q.qlogo.cn/g?b=qq&s=0&nk={target_id}"

    def _record_image_supplement_info(self, url: str | Path, image_index: int) -> None:
        """Record avatar annotations after a reference image has been downloaded."""
        for user_id, avatar_url in self.avatar_mappings.items():
            if avatar_url == url:
                info = f"- @{user_id}: avatar is image {image_index}"
                self.image_supplement_infos.append(info)
