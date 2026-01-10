import random
from abc import ABC, abstractmethod
from typing import ClassVar

from curl_cffi import AsyncSession

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig

from .data import CommonConfig, PromptConfig, ProviderConfig
from .downloader import Downloader
from .utils import get_key_index


class BaseProvider(ABC):
    """提供商抽象基类"""

    api_type: str
    """提供商 API 类型标识符"""

    _registry: ClassVar[dict[str, type["BaseProvider"]]] = {}
    """提供商类注册表"""

    session: AsyncSession
    def_common_config: CommonConfig
    def_prompt_config: PromptConfig
    downloader: Downloader

    # 可重试状态码
    RETRY_STATUS_CODES = frozenset({408, 500, 502, 503, 504})
    # 不可重试状态码
    NO_RETRY_STATUS_CODES = frozenset({401, 402, 403, 422, 429})

    def __init__(
        self,
        config: AstrBotConfig,
        common_config: CommonConfig,
        prompt_config: PromptConfig,
        session: AsyncSession,
        downloader: Downloader,
    ):
        self.conf = config
        self.def_prompt_config = prompt_config
        self.def_common_config = common_config
        self.session = session
        self.downloader = downloader

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if (
            hasattr(cls, "api_type")
            and cls.api_type
            and not cls._registry.get(cls.api_type)
        ):
            cls._registry[cls.api_type] = cls
            logger.debug(f"已注册提供商类: {cls.api_type}")

    @classmethod
    def get_provider_class(cls, api_type: str) -> type["BaseProvider"] | None:
        return cls._registry.get(api_type, None)

    async def generate_images(
        self,
        provider_config: ProviderConfig,
        params: dict,
        image_b64_list: list[tuple[str, str]] | None = None,
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        """图片生成调度方法"""
        key_list_len = len(provider_config.keys)
        if key_list_len == 0:
            return None, "图片生成失败：未配置 API Key"
        current_index = random.randrange(key_list_len)
        # 轮询使用 API Key
        err = None
        for key_ in range(key_list_len):
            # 获取下一个 Key 索引
            current_index = get_key_index(current_index, key_list_len)
            # 重试机制
            for i in range(self.def_common_config.max_retry):
                if provider_config.stream:
                    images_result, status, err = await self._call_stream_api(
                        provider_config=provider_config,
                        api_key=provider_config.keys[current_index],
                        params=params,
                        image_b64_list=image_b64_list,
                    )
                else:
                    images_result, status, err = await self._call_api(
                        provider_config=provider_config,
                        api_key=provider_config.keys[current_index],
                        params=params,
                        image_b64_list=image_b64_list,
                    )
                if images_result:
                    return images_result, None
                if self.def_common_config.smart_retry and not self.should_retry(status):
                    break
                logger.warning(
                    f"图片生成失败，正在重试 {provider_config.api_name} 当前Key ({i + 1}/ {self.def_common_config.max_retry})"
                )
            else:
                if key_ < key_list_len - 1:
                    logger.warning(
                        f"图片生成失败，切换到 {provider_config.api_name} 下一个Key"
                    )
        return None, err or "图片生成失败：所有 Key 均已用尽或不可用"

    def should_retry(self, status) -> bool:
        if status in self.RETRY_STATUS_CODES:
            return True
        return False

    @abstractmethod
    async def _call_api(
        self, **kwargs
    ) -> tuple[list[tuple[str, str]], int | None, str | None]:
        """调用同步 API 方法"""
        pass

    @abstractmethod
    async def _call_stream_api(
        self, **kwargs
    ) -> tuple[list[tuple[str, str]], int | None, str | None]:
        """调用流式 API 方法"""
        pass
