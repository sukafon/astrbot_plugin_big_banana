from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from astrbot.api import logger

from ..schemas import (
    GenerationResult,
    ImageResource,
    ProviderCallResult,
    ProviderConfig,
)

if TYPE_CHECKING:
    from ...main import BigBanana


class BaseProvider(ABC):
    """提供商抽象基类"""

    provider_type: ClassVar[str] = ""
    """提供商 API 类型标识符"""

    _registry: ClassVar[dict[str, type[BaseProvider]]] = {}
    """提供商类注册表"""

    def __init__(
        self,
        plugin: BigBanana,
        provider_config: ProviderConfig,
        params: dict,
        image_list: list[ImageResource] | None = None,
    ):
        """保存提供商调用上下文。"""
        self.plugin = plugin
        self.provider_config = provider_config
        self.params = params
        self.image_list = image_list or []
        self.text_response_parts: list[str] = []

    async def initialize(self) -> None:
        """可选初始化入口。"""
        pass

    def _missing_image_result(
        self,
        reason: str | None = None,
        *,
        response_text: str = "",
        status_code: int = 200,
    ) -> ProviderCallResult:
        logger.warning(
            f"[BIG BANANA] 请求成功，但未返回图片数据, 响应内容: {response_text[:1024] or '无'}"
        )
        message = reason or "响应中未包含图片数据"
        if (
            not reason
            and self.plugin.preference_config.send_text_when_no_image
            and self.text_response_parts
        ):
            message = "".join(self.text_response_parts).strip() or message
        return ProviderCallResult(
            status_code=status_code,
            error_message=message,
        )

    def __init_subclass__(cls, **kwargs):
        """在子类声明 provider_type 时自动注册提供商类型。"""
        super().__init_subclass__(**kwargs)
        # 检查子类是否重写了provider_type，继承的不算
        if "provider_type" not in cls.__dict__:
            logger.debug("[BIG BANANA] provider_type not in cls.__dict__")
            return
        # 注册键不区分大小写，以兼容配置中的 provider_type 写法。
        provider_type = cls.provider_type.strip()
        # 检查provider_type是否为空
        if not provider_type:
            logger.debug("[BIG BANANA] provider_type is empty")
            return
        registry_key = provider_type.casefold()

        # 检查注册表是否有同provider_type的子类
        registered_cls = cls._registry.get(registry_key)
        # 如果有这个provider_type的子类，且不是同一个类对象，打印个警告，不继续注册
        if registered_cls is not None and registered_cls is not cls:
            logger.warning(
                f"[BIG BANANA] 提供商类型 {provider_type} 已由 "
                f"{registered_cls.__name__} 注册，跳过 {cls.__name__}"
            )
            return
        # 同一个类对象不会多次执行__init_subclass__，不需要再检查
        cls._registry[registry_key] = cls
        logger.debug(f"[BIG BANANA] 已注册提供商类: {provider_type}")

    @classmethod
    def get_provider_class(cls, provider_type: str) -> type[BaseProvider] | None:
        """根据提供商类型获取对应的提供商类对象。"""
        return cls._registry.get(provider_type.strip().casefold())

    @abstractmethod
    async def generate_images(self) -> GenerationResult:
        """声明子类必须实现图片生成入口。"""
        ...
