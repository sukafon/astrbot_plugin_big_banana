from dataclasses import dataclass, field


@dataclass(repr=False, slots=True)
class CommonConfig:
    """常规配置参数"""

    preset_append: bool = True
    """ 是否在预设提示词后追加用户输入文本 """
    smart_retry: bool = True
    """是否启用智能重试"""
    max_retry: int = 3
    """最大重试次数"""
    fallback_on_empty_result: bool = False
    """提供商返回空结果时是否继续回退"""
    timeout: float = 300
    """请求超时时间, 单位: 秒"""
    proxy: str | None = None
    """代理"""
    strip_metadata: bool = True
    """是否在图片处理中抹除所有可能带隐私的元数据"""


@dataclass(repr=False, slots=True)
class PreferenceConfig:
    """偏好配置参数"""

    skip_at_first: bool = True
    """ 跳过第一次@机器人 """
    skip_quote_first: bool = False
    """ 跳过第一次引用@ """
    enable_at_avatar_note: bool = True
    """ 命令调用时是否添加 At 头像参考图编号说明 """
    enable_drawing_message: bool = True
    """ 是否启用图片生成中提示消息 """
    enable_llm_tool_drawing_message: bool = False
    """ 是否在 LLM 工具调用时发送图片生成中提示消息 """
    send_text_when_no_image: bool = False
    """ 未返回图片但返回文本时是否发送文本 """
    drawing_message: str = "🎨 在画了，请稍等一会..."
    """ 图片生成中提示消息 """
    video_generation_message: str = "🎬 正在生成视频，请稍等一会..."
    """ 视频生成中提示消息 """
    group_cooldown: int = 0
    """ 群组冷却时间(秒) """
    command_use_background_task: bool = False
    """ 命令调用时是否使用后台任务执行绘图 """
    background_task_send_type: str = "event"
    """ 后台任务消息发送方式。event：事件消息（被动消息，默认）；active：主动消息 """
    gather_timeout: int = 120
    """ 收集模式超时时间, 单位: 秒 """


@dataclass(repr=False, slots=True)
class PrefixConfig:
    """前缀配置参数"""

    coexist_enabled: bool = False
    """ 是否允许与其他插件共存 """
    prefix_list: list[str] = field(default_factory=list)
    """ 触发前缀列表 """
    provider_prefix: bool = False
    """ 是否允许提供商前缀触发 """


@dataclass(repr=False, slots=True)
class SaveImagesConfig:
    """生成图片保存配置"""

    local_save: bool = False
    """是否保存生成图片到本地"""
    r2_save: bool = False
    """是否保存生成图片到已配置的 R2 图床"""


@dataclass(repr=False, slots=True)
class LlmToolsConfig:
    """LLM函数调用工具配置"""

    enable_preset_tool: bool = True
    """ 启用预设查询工具 """
    enable_image_generation_tool: bool = True
    """ 启用图片生成工具 """
    enable_video_generation_tool: bool = True
    """ 启用视频生成工具 """
    llm_tool_preset_name: str = ""
    """ LLM 工具调用预设参数的触发词 """
    llm_video_tool_preset_name: str = ""
    """ LLM 视频工具调用预设参数的触发词 """
    llm_tool_use_background_task: bool = False
    """ Tools调用使用后台任务 """
    llm_tool_direct_send_result: bool = False
    """ LLM 工具是否直接向聊天窗口发送图片结果 """
    llm_tool_truncate_images: bool = False
    """ 是否按生成数量截断 LLM 图片工具的返回结果 """
    llm_tool_restrict_private_network: bool = True
    """ 是否限制 LLM 工具读取内网图片 URL """
    background_callback_plugin: str = ""
    """ 后台任务完成回调插件 """
    background_callback_method: str = ""
    """ 后台任务完成回调方法 """
