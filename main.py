import asyncio
import itertools
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import BaseMessageComponent

from .core import BaseProvider, Downloader, HttpManager, R2ImageHoster
from .core.commands import (
    add_prompt_command,
    add_whitelist_command,
    del_prompt_command,
    del_whitelist_command,
    list_prompts_command,
    list_whitelist_command,
    prompt_details,
)
from .core.data import (
    PARAMS_LIST,
    CommonConfig,
    ImageHostingConfig,
    PreferenceConfig,
    PromptConfig,
    ProviderConfig,
    SubBrainConfig,
)
from .core.dispatcher import ProviderDispatcher
from .core.llm_tools import (
    BigBananaAvatarTool,
    BigBananaPromptTool,
    BigBananaReferenceTool,
    remove_tools,
)
from .core.runner import build_message_chain, handle_on_message, job


class BigBanana(Star):
    """The main plugin class for BigBanana image generation star."""

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
        self.whitelist_only_for_commands = self.whitelist_config.get(
            "only_for_commands", False
        )
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

    def is_global_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为全局管理员

        Args:
            event: The message event.

        Returns:
            True if global admin.
        """
        admin_ids = self.context.get_config().get("admins_id", [])
        return event.get_sender_id() in admin_ids

    # === 管理指令：白名单管理 ===
    @filter.command("lm白名单添加", alias={"lmawl"})
    async def add_whitelist_command(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ):
        """lm白名单添加 <用户/群组> <ID>"""
        async for res in add_whitelist_command(self, event, cmd_type, target_id):
            yield res

    @filter.command("lm白名单删除", alias={"lmdwl"})
    async def del_whitelist_command(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ):
        """lm白名单删除 <用户/群组> <ID>"""
        async for res in del_whitelist_command(self, event, cmd_type, target_id):
            yield res

    @filter.command("lm白名单列表", alias={"lmwll"})
    async def list_whitelist_command(self, event: AstrMessageEvent):
        """lm白名单列表"""
        async for res in list_whitelist_command(self, event):
            yield res

    # === 管理指令：添加/更新提示词 ===
    @filter.command("lm添加", alias={"lma"})
    async def add_prompt_command(self, event: AstrMessageEvent, trigger_word: str = ""):
        """lm添加 <触发词> <提示词内容>"""
        async for res in add_prompt_command(self, event, trigger_word):
            yield res

    @filter.command("lm列表", alias={"lml"})
    async def list_prompts_command(self, event: AstrMessageEvent):
        """lm列表"""
        async for res in list_prompts_command(self, event):
            yield res

    @filter.command("lm提示词", alias={"lmc", "lm详情"})
    async def prompt_details(self, event: AstrMessageEvent, trigger_word: str):
        """获取提示词详情字符串"""
        async for res in prompt_details(self, event, trigger_word):
            yield res

    @filter.command("lm删除", alias={"lmd"})
    async def del_prompt_command(self, event: AstrMessageEvent, trigger_word: str = ""):
        """lm删除 <触发词>"""
        async for res in del_prompt_command(self, event, trigger_word):
            yield res

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_message(self, event: AstrMessageEvent):
        """绘图命令消息入口"""
        async for res in handle_on_message(self, event):
            yield res

    async def job(
        self,
        event: AstrMessageEvent,
        params: dict,
        image_urls: list[str] | None = None,
        referer_id: list[str] | None = None,
        is_llm_tool: bool = False,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        """负责参数处理、调度提供商、保存图片等逻辑，返回图片和错误信息

        Args:
            event: The message event.
            params: Parameters for drawing.
            image_urls: URLs of images.
            referer_id: QQ number.
            is_llm_tool: If LLM tool call.

        Returns:
            The image base64 list and warning/error message.
        """
        return await job(self, event, params, image_urls, referer_id, is_llm_tool)

    def build_message_chain(
        self,
        event: AstrMessageEvent,
        results: list[tuple[str, str]],
        result_urls: list[str] | None = None,
        url_only: bool = False,
        params: dict | None = None,
    ) -> list[BaseMessageComponent]:
        """构建消息链

        Args:
            event: The message event.
            results: The base64 list.
            result_urls: Public URLs.
            url_only: Whether only URL is needed.
            params: Parameters dictionary.

        Returns:
            List of message components.
        """
        return build_message_chain(self, event, results, result_urls, url_only, params)

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
