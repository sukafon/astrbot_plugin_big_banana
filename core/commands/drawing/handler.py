from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain

from ...drawing import DrawingPipeline, ImageSaver, parse_params
from ...drawing.collector import ImageCollector
from ...schemas import MAX_SIZE_B64_LEN, GenerationResult
from .gather_session import DrawingGatherSession

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.message.components import BaseMessageComponent
    from astrbot.core.message.message_event_result import MessageEventResult

    from ....main import BigBanana
    from .progress_meme import ProgressMemeHandler


class DrawingCommandHandler:
    """负责接收消息、解析绘图命令，管理并发任务生命周期并发送结果给用户。"""

    def __init__(
        self,
        plugin: BigBanana,
        drawing_pipeline: DrawingPipeline,
        meme_handler: ProgressMemeHandler,
    ) -> None:
        """初始化绘图命令处理器所需的依赖和状态。"""
        self.plugin = plugin
        self.drawing_pipeline = drawing_pipeline
        self.meme_handler = meme_handler
        self.image_saver = ImageSaver()

    async def handle_on_message(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """解析消息、执行命令前置检查并启动绘图任务。"""
        # 解析提示词，未命中返回None
        params = parse_params(self.plugin, event)
        if params is None:
            return

        # 检查白名单
        access_check = self.plugin.whitelist_guard.check(event, is_command=True)
        if not access_check.allowed:
            logger.info(access_check.log_message)
            return

        # 检查冷却时间
        cooldown_check = self.plugin.cooldown_guard.check(event)
        if not cooldown_check.allowed:
            logger.info(cooldown_check.log_message)
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(f"❌ {cooldown_check.message}"),
                ]
            )
            return

        # 检查是否重复执行
        task_id = self.plugin.task_manager.build_task_id(event)
        if self.plugin.task_manager.is_running(task_id):
            logger.warning(f"任务 ID {task_id} 已在运行中，防止重复执行。")
            return

        # 在这里创建的协程对象，取消任务时向下传播不包括后台任务（后台任务特意重建了任务对象来逃避取消）
        current_task = asyncio.current_task()
        if current_task:
            self.plugin.task_manager.start(task_id, current_task)

        try:
            # 提交绘图任务
            async for msg_chain in self.submit_drawing_task(event, params):
                yield msg_chain
        finally:
            # 如果任务对象没变，证明当前任务没有进入后台任务，可以清理掉
            if self.plugin.task_manager.running_tasks.get(task_id) is current_task:
                # 无论是否成功，同步任务已经结束，可以清理占位对象
                self.plugin.task_manager.finish(task_id)

    async def submit_drawing_task(
        self, event: AstrMessageEvent, params: dict
    ) -> AsyncGenerator[MessageEventResult, None]:
        """提交绘图任务，返回结果消息链"""
        # 收集图片
        image_collector = ImageCollector(plugin=self.plugin, event=event, params=params)
        # 收集参考图
        await image_collector.add_refer_images()
        # 收集消息图片（含AT头像、回复的图片）
        await image_collector.add_msg_images(event)

        # 收集模式处理
        if params.get("gather_mode", self.plugin.params_config.gather_mode):
            # 创建一个实例用于管理状态
            gather_session = DrawingGatherSession(
                plugin=self.plugin,
                event=event,
                params=params,
                collector=image_collector,
            )
            # 进入收集模式
            async for res in gather_session.run():
                yield res
            # 收集模式退出后，检查是否取消或失败
            if gather_session.cancelled:
                return

        # Check collected image references before downloading and supplement avatars.
        if not image_collector.check_urls_limit():
            await image_collector.supplement_avatars()
        if not image_collector.check_urls_limit():
            yield event.chain_result(
                [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(
                        f"❌ 图片数量不足，当前仅 {len(image_collector.get_final_urls())} 张，最少需要 {image_collector.min_images} 张"
                    ),
                ]
            )
            return

        # 先发送开始画图消息，再开始下载图片，防止用户体感卡顿
        if self.plugin.preference_config.enable_drawing_message:
            if params.get("capability", "image_generation") == "video_generation":
                text = self.plugin.preference_config.video_generation_message
            else:
                text = self.plugin.preference_config.drawing_message
            if text.strip():
                async for start_msg in self._build_start_msg(event, text):
                    yield start_msg

        # 判断前台还是后台执行
        use_bg = self.plugin.preference_config.command_use_background_task

        if use_bg:
            # 后台任务处理：使用 Task 包装
            task = asyncio.create_task(
                self.generate_and_send_result(
                    event=event,
                    params=params,
                    collector=image_collector,
                )
            )
            # 重置任务对象，避免被上游函数清理任务映射，同时确保后台任务能被正常取消
            task_id = self.plugin.task_manager.build_task_id(event)
            self.plugin.task_manager.start(task_id, task)
        else:
            # 前台任务处理
            await self.generate_and_send_result(
                event=event,
                params=params,
                collector=image_collector,
            )

    async def generate_and_send_result(
        self,
        event: AstrMessageEvent,
        params: dict,
        collector: ImageCollector,
    ) -> None:
        """生成图片并发送结果"""
        temporary_paths: list[Path] = []
        try:
            # 下载收集到的图片
            image_list = await collector.fetch_collected_images()
            # Check successfully downloaded images and incrementally download supplements.
            if not collector.check_images_limit():
                await collector.supplement_avatars(use_downloaded_images=True)
                image_list = await collector.fetch_collected_images()

            # Perform the final check after downloading supplemental avatars.
            if not collector.check_images_limit():
                result = GenerationResult(
                    error_message=(
                        f"图片数量不足，当前仅 {len(image_list)} 张，"
                        f"最少需要 {collector.min_images} 张"
                    )
                )
            else:
                # 副脑优化
                prompt = params.get("prompt", "")
                if prompt and params.get(
                    "sub_brain", self.plugin.sub_brain_config.cmd_enabled
                ):
                    optimized_prompt = (
                        await self.plugin.sub_brain_optimizer.optimize_prompt(
                            event, prompt
                        )
                    )
                    if optimized_prompt is not None:
                        params["prompt"] = optimized_prompt
                # 添加at头像备注
                if self.plugin.preference_config.enable_at_avatar_note:
                    params["prompt"] = self._append_image_supplement_note(
                        params.get("prompt", "draw a picture"),
                        collector.image_supplement_infos,
                    )
                # Route the request through the matching media pipeline.
                if params.get("capability", "image_generation") == "video_generation":
                    result = await self.plugin.video_pipeline.run(
                        params,
                        image_list=image_list,
                    )
                else:
                    result = await self.drawing_pipeline.run(
                        params,
                        image_list=image_list,
                    )

            # 构建消息链
            if result.error_message:
                media_name = (
                    "视频"
                    if params.get("capability", "image_generation")
                    == "video_generation"
                    else "图片"
                )
                msg_chain: list[BaseMessageComponent] = [
                    Comp.Reply(id=event.message_obj.message_id),
                    Comp.Plain(f"❌ {media_name}生成失败：{result.error_message}"),
                ]
            else:
                # 成功，标记冷却时间
                self.plugin.cooldown_guard.mark_cooldown(event.get_group_id())
                # 构建消息链
                msg_chain = self._build_result_message_chain(
                    event,
                    result=result,
                    url_only=params.get("url", self.plugin.params_config.url),
                    temporary_paths=temporary_paths,
                )

            # 包装消息链类型
            msg_chain_obj = MessageChain(msg_chain)

            # 根据前台后台任务决定发送消息的方式
            if (
                self.plugin.preference_config.command_use_background_task
                and self.plugin.preference_config.background_task_send_type == "active"
            ):
                # 后台任务且配置为主动消息发送
                await self.plugin.context.send_message(
                    event.unified_msg_origin, msg_chain_obj
                )
            else:
                # 同步任务，或后台任务且配置为事件消息发送（默认）
                await event.send(msg_chain_obj)
        finally:
            # 任务结束，清理
            if self.plugin.preference_config.command_use_background_task:
                task_id = self.plugin.task_manager.build_task_id(event)
                self.plugin.task_manager.finish(task_id)
            if event.platform_meta.name == "telegram":
                for path in temporary_paths:
                    try:
                        path.unlink(missing_ok=True)
                        logger.debug(f"[BIG BANANA] 已删除当前任务缓存文件: {path}")
                    except Exception as e:
                        logger.error(f"[BIG BANANA] 删除缓存文件 {path} 失败: {e}")

    @staticmethod
    def _append_image_supplement_note(
        prompt: str, image_supplement_infos: list[str]
    ) -> str:
        """Append supplemental image-reference mappings to the generation prompt."""
        if not image_supplement_infos:
            return prompt
        image_supplement_text = "\n".join(image_supplement_infos)
        at_avatar_note = (
            "The following @-mention avatar references correspond to the final "
            "input image list. Image indices start from 1:\n"
            f"{image_supplement_text}"
        )
        prompt = prompt.rstrip()
        return f"{prompt}\n\n{at_avatar_note}" if prompt else at_avatar_note

    def _build_result_message_chain(
        self,
        event: AstrMessageEvent,
        result: GenerationResult,
        url_only: bool = False,
        temporary_paths: list[Path] | None = None,
    ) -> list[BaseMessageComponent]:
        """构造适配平台限制的绘图结果消息链。

        Args:
            event: 当前绘图任务的消息事件。
            result: 图片生成结果。
            url_only: 是否只发送图片 URL。
            temporary_paths: 用于记录本次任务创建的临时文件。

        Returns:
            可直接发送到消息平台的消息组件列表。
        """
        msg_chain: list[BaseMessageComponent] = [
            Comp.Reply(id=event.message_obj.message_id)
        ]
        result_urls = result.urls
        video_urls = [video.url for video in result.videos if video.url]
        # 如果仅url，这里尝试检查有无url，无则报错
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
        # 对tg做特殊处理
        if event.platform_meta.name == "telegram" and any(
            (image.base64 and len(image.base64) > MAX_SIZE_B64_LEN)
            for image in images_with_bytes
        ):
            save_results = self.image_saver.save_images_to_local(
                images_with_bytes, self.plugin.temp_dir
            )
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
        # 只有urls，那么应该是下载失败了，直接发送url吧
        elif result_urls:
            msg_chain.append(Comp.Plain("\n".join(result_urls)))
        else:
            msg_chain.append(Comp.Plain("❌ 图片生成失败：响应中未包含图片数据"))
        return msg_chain

    async def _build_start_msg(
        self, event: AstrMessageEvent, text: str
    ) -> AsyncGenerator[MessageEventResult]:
        """构建绘图中提示信息"""
        clean_text, raw_tags = self.meme_handler.parse_start_message(text)

        if clean_text:
            yield event.chain_result([Comp.Plain(clean_text)])

        if raw_tags:
            img = await self.meme_handler.get_meme(event, raw_tags)
            if img:
                yield event.chain_result([img])
