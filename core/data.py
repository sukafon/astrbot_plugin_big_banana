from dataclasses import dataclass
from typing import Literal

# 常数
DEF_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEF_GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEF_VERTEX_AI_ANONYMOUS_BASE_API = "https://cloudconsole-pa.clients6.google.com"

# 类型枚举
_API_Type = Literal["Gemini", "OpenAI_Chat", "Vertex_AI_Anonymous"]


@dataclass(repr=False, slots=True)
class ProviderConfig:
    """提供商配置信息"""

    api_name: str
    """提供商名称, 用于区分不同提供商(例如主提供商、备用提供商等)"""
    enabled: bool
    """是否启用"""
    api_type: _API_Type
    """API 格式类型"""
    keys: list[str]
    """API 密钥列表, 可选。部分提供商可能不需要此字段"""
    api_url: str
    """API 地址, 可选, 若不提供则使用默认地址。部分提供商可能不需要此字段"""
    model: str = "gemini-3-pro-image-preview"
    """模型名称"""
    stream: bool = False
    """是否启用流式响应"""


@dataclass(repr=False, slots=True)
class PromptConfig:
    """图片生成配置参数"""

    min_images: int = 1
    """最小输入图片数量"""
    max_images: int = 6
    """最大输入图片数量"""
    aspect_ratio: str = "default"
    """图片宽高比"""
    image_size: str = "1K"
    """图片尺寸/分辨率"""
    google_search: bool = False
    """是否启用谷歌搜索功能"""
    refer_images: str | None = None
    """引用参考图片的文件名"""
    gather_mode: bool = False
    """是否启用收集模式"""


@dataclass(repr=False, slots=True)
class CommonConfig:
    """常规配置参数"""

    preset_append: bool = False
    """ 是否在预设提示词后追加用户输入文本 """
    text_response: bool = False
    """是否启用文本响应"""
    smart_retry: bool = True
    """是否启用智能重试"""
    max_retry: int = 3
    """最大重试次数"""
    timeout: float = 300
    """请求超时时间, 单位: 秒"""
    proxy: str | None = None
    """代理"""


@dataclass(repr=False, slots=True)
class PreferenceConfig:
    """偏好配置参数"""

    skip_at_first: bool = True
    """ 跳过第一次@机器人 """
    skip_quote_first: bool = False
    """ 跳过第一次引用@ """
    skip_llm_at_first: bool = False
    """ 跳过第一次LLM@ """


@dataclass(repr=False, slots=True)
class VertexAIAnonymousConfig:
    """Vertex AI Anonymous 配置参数"""

    recaptcha_base_api: str = "https://www.google.com"
    """Recaptcha 基础 API 地址"""
    vertex_ai_anonymous_base_api: str = "https://cloudconsole-pa.clients6.google.com"
    """Vertex AI Anonymous 基础 API 地址"""
    system_prompt: str | None = None
    """系统提示词"""
    max_retry: int = 10
    """最大重试次数"""
