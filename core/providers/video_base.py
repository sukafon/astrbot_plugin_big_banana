from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource, ProviderConfig

if TYPE_CHECKING:
    from ...main import BigBanana


class BaseVideoProvider(ABC):
    """Base class and registry for video generation providers."""

    provider_type: ClassVar[str] = ""
    _registry: ClassVar[dict[str, type[BaseVideoProvider]]] = {}

    def __init__(
        self,
        plugin: BigBanana,
        provider_config: ProviderConfig,
        params: dict,
        image_list: list[ImageResource] | None = None,
    ) -> None:
        """Store the video generation call context.

        Args:
            plugin: Active plugin instance.
            provider_config: Selected provider configuration.
            params: Resolved generation parameters.
            image_list: Optional input images.
        """
        self.plugin = plugin
        self.provider_config = provider_config
        self.params = params
        self.image_list = image_list or []

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        provider_type = cls.__dict__.get("provider_type", "").strip()
        if not provider_type:
            return
        registered_cls = cls._registry.get(provider_type)
        if registered_cls is not None and registered_cls is not cls:
            logger.warning(
                f"[BIG BANANA] 视频提供商类型 {provider_type} 已由 "
                f"{registered_cls.__name__} 注册，跳过 {cls.__name__}"
            )
            return
        cls._registry[provider_type] = cls
        logger.debug(f"[BIG BANANA] 已注册视频提供商类: {provider_type}")

    @classmethod
    def get_provider_class(cls, provider_type: str) -> type[BaseVideoProvider] | None:
        """Look up a registered video provider.

        Args:
            provider_type: Provider API type identifier.

        Returns:
            Registered provider class, or None when unavailable.
        """
        return cls._registry.get(provider_type.strip())

    @abstractmethod
    async def generate_videos(self) -> GenerationResult:
        """Generate videos and wait for the asynchronous job to finish.

        Returns:
            Generated video resources or an error result.
        """
        ...
