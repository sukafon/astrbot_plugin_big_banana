import json

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core import AstrBotConfig

from .core import (
    CallbackDispatcher,
    Downloader,
    DrawingCommandHandler,
    DrawingPipeline,
    DrawingTaskManager,
    HttpManager,
    ProgressMemeHandler,
    PromptConfigManager,
    PromptHandler,
    ProviderConfigManager,
    ProviderDispatcher,
    R2ImageHoster,
    SubBrainOptimizer,
    VideoPipeline,
    VideoProviderDispatcher,
    WhitelistHandler,
)
from .core.guards import CooldownGuard, WhitelistGuard
from .core.llm_tools import (
    BigBananaImageGenerationTool,
    BigBananaPromptTool,
    BigBananaVideoGenerationTool,
)
from .core.schemas import (
    CommonConfig,
    ImageHostingConfig,
    LlmToolsConfig,
    ParamsConfig,
    PreferenceConfig,
    PrefixConfig,
    SaveImagesConfig,
    SubBrainConfig,
)
from .web.web_api import BigBananaWebApi


class BigBanana(Star):
    """AstrBot 插件入口和服务组装根节点。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        """保存插件上下文并读取静态配置。"""
        super().__init__(context)
        self.conf = config

        # 初始化数据目录
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_big_banana")
        # 初始化引用图片目录
        self.refer_images_dir = self.data_dir / "refer_images"
        self.refer_images_dir.mkdir(parents=True, exist_ok=True)
        # 初始化临时目录
        self.temp_dir = self.data_dir / "temp_images"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        # 初始化保存目录
        self.save_dir = self.data_dir / "save_images"
        self.save_dir.mkdir(parents=True, exist_ok=True)

        # 读取并结构化配置文件
        self.common_config = CommonConfig(**self.conf.get("common_config", {}))
        self.params_config = ParamsConfig(
            **(
                self.conf.get("params_config", {})
                | self.conf.get("gemini_image_config", {})
                | self.conf.get("openai_image_config", {})
            )
        )
        self.preference_config = PreferenceConfig(
            **self.conf.get("preference_config", {})
        )
        self.prefix_config = PrefixConfig(**self.conf.get("prefix_config", {}))
        self.image_hosting_config = ImageHostingConfig(
            **self.conf.get("image_hosting", {})
        )
        self.sub_brain_config = SubBrainConfig(**self.conf.get("sub_brain", {}))
        self.save_images = SaveImagesConfig(**self.conf.get("save_images", {}))
        self.llm_tools_config = LlmToolsConfig(**self.conf.get("llm_tools", {}))

    async def initialize(self):
        """根据已读取的配置创建运行期依赖和单例对象"""

        # 头像替换
        self.avatar_map = {}
        avatar_path = self.data_dir / "avatar_substitutions.json"
        if avatar_path.exists():
            try:
                data = json.loads(avatar_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, str) and v.strip():
                        self.avatar_map[str(k)] = [v.strip()]
                    elif isinstance(v, list):
                        references = [
                            item.strip()
                            for item in v
                            if isinstance(item, str) and item.strip()
                        ]
                        if references:
                            self.avatar_map[str(k)] = references
            except Exception:
                pass

        # 创建全局单例
        self.prompt_config_manager = PromptConfigManager(self.conf)
        self.task_manager = DrawingTaskManager()
        # 初始化安全及限制守卫
        self.whitelist_guard = WhitelistGuard(self.conf)
        self.cooldown_guard = CooldownGuard(self.preference_config)
        # 白名单处理器
        self.whitelist_handler = WhitelistHandler(self.whitelist_guard)
        # 提示词处理器
        self.prompt_handler = PromptHandler(self.prompt_config_manager)
        self.background_callback = CallbackDispatcher(self)
        # HTTP管理器
        self.http_manager = HttpManager()
        # HTTP下载器
        self.downloader = Downloader(
            self.http_manager.get_aiohttp_session(), self.common_config.proxy
        )
        # R2图床上传器
        self.image_hoster = R2ImageHoster(self)
        # 提供商配置
        self.provider_config_manager = ProviderConfigManager(self.conf)
        # 调度器
        self.dispatcher = ProviderDispatcher(self)
        self.video_dispatcher = VideoProviderDispatcher(self)
        # 副脑提示词优化器
        self.sub_brain_optimizer = SubBrainOptimizer(
            context=self.context,
            sub_brain_config=self.sub_brain_config,
        )
        # 绘图管线
        self.drawing_pipeline = DrawingPipeline(self)
        # Video generation pipeline
        self.video_pipeline = VideoPipeline(self)
        # 进度表情包处理器
        self.progress_meme_handler = ProgressMemeHandler()
        # 绘图命令处理器
        self.drawing_command_handler = DrawingCommandHandler(
            self,
            self.drawing_pipeline,
            self.progress_meme_handler,
        )

        # 注册 LLM 函数调用工具
        if self.llm_tools_config.enable_preset_tool:
            self.context.add_llm_tools(BigBananaPromptTool(plugin=self))
            logger.info("[BIG BANANA] 已注册函数调用工具: banana_preset_prompt")
        if self.llm_tools_config.enable_image_generation_tool:
            image_generation_tool = BigBananaImageGenerationTool(plugin=self)
            self.context.add_llm_tools(image_generation_tool)
            logger.info("[BIG BANANA] 已注册函数调用工具: banana_image_generation")
        if self.llm_tools_config.enable_video_generation_tool:
            video_generation_tool = BigBananaVideoGenerationTool(plugin=self)
            self.context.add_llm_tools(video_generation_tool)
            logger.info("[BIG BANANA] 已注册函数调用工具: banana_video_generation")
        # 启动WEB API
        self.web_api = BigBananaWebApi(self)
        self.web_api.register_routes()

    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    @filter.command("lm白名单添加", alias={"lmawl"})
    async def add_whitelist_command(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ):
        """lm白名单添加 用户/群组 ID"""
        async for res in self.whitelist_handler.add_whitelist(
            event, cmd_type, target_id
        ):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    @filter.command("lm白名单删除", alias={"lmdwl"})
    async def del_whitelist_command(
        self, event: AstrMessageEvent, cmd_type: str = "", target_id: str = ""
    ):
        """lm白名单删除 用户/群组 ID"""
        async for res in self.whitelist_handler.del_whitelist(
            event, cmd_type, target_id
        ):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    @filter.command("lm白名单列表", alias={"lmwll"})
    async def list_whitelist_command(self, event: AstrMessageEvent):
        """lm白名单列表"""
        async for res in self.whitelist_handler.list_whitelist(event):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    @filter.command("lm添加", alias={"lma"})
    async def add_prompt_command(self, event: AstrMessageEvent, trigger_word: str = ""):
        """lm添加 触发词 提示词内容"""
        async for res in self.prompt_handler.add_prompt(event, trigger_word):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    @filter.command("lm列表", alias={"lml"})
    async def list_prompts_command(self, event: AstrMessageEvent):
        """lm列表"""
        async for res in self.prompt_handler.list_prompts(event):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    @filter.command("lm提示词", alias={"lmc", "lm详情"})
    async def prompt_details(self, event: AstrMessageEvent, trigger_word: str):
        """获取提示词详情字符串"""
        async for res in self.prompt_handler.prompt_details(event, trigger_word):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    @filter.command("lm删除", alias={"lmd"})
    async def del_prompt_command(self, event: AstrMessageEvent, trigger_word: str = ""):
        """lm删除 触发词"""
        async for res in self.prompt_handler.del_prompt(event, trigger_word):
            yield res

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_message(self, event: AstrMessageEvent):
        """绘图命令消息入口"""
        async for res in self.drawing_command_handler.handle_on_message(event):
            yield res

    async def terminate(self):
        """在插件卸载或停用时清理 Web API、后台任务和 HTTP 会话。"""
        # 注销WEB API
        if self.web_api is not None:
            self.web_api.unregister_routes()
        # 取消所有任务
        if self.task_manager is not None:
            await self.task_manager.cancel_all()
        # 关闭HTTP会话
        if self.http_manager is not None:
            await self.http_manager.close_session()
