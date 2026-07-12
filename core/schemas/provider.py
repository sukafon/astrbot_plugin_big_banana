from dataclasses import dataclass, field
from typing import Any

from .image import ImageResource


@dataclass(repr=False, slots=True)
class ProviderConfig:
    """提供商配置信息"""

    provider_type: str = ""
    """API 格式类型"""
    capability: str = "image_generation"
    """能力类型：如 image_generation、video_generation"""
    enabled: bool = True
    """是否启用"""
    name: str = ""
    """提供商名称"""
    keys: list[str] = field(default_factory=list)
    """API 密钥列表"""
    base_url: str = ""
    """API 地址"""
    model: str = ""
    """模型名称"""
    stream: bool = False
    """是否启用流式响应"""
    enable_proxy: bool = False
    """是否启用代理配置"""
    max_images: int = 6
    """该提供商支持的最大输入图片数量"""
    raw_config: dict[str, Any] = field(default_factory=dict)
    """原始提供商配置，可以用于查询提供商的额外配置参数"""


@dataclass(repr=False, slots=True)
class ProviderCallResult:
    """单次 provider API 调用结果。"""

    images: list[ImageResource] | None = None
    """返回的图片资源列表。"""
    status_code: int = 0
    """请求状态码，用于重试策略判断。"""
    error_message: str | None = None
    """给上层流程使用的错误信息。"""
