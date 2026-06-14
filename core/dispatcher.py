import asyncio
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from .data import ProviderConfig
from .base import BaseProvider


def get_images_url_from_api_base(api_base: str) -> str:
    """从 api_base 中获取图片接口基础地址"""
    if not api_base:
        return "https://api.openai.com/v1/images"
    # Remove trailing slash
    url = api_base.rstrip("/")
    # Remove /chat/completions or /chat
    if url.endswith("/chat/completions"):
        url = url[:-17]
    elif url.endswith("/chat"):
        url = url[:-5]
    url = url.rstrip("/")
    # If it doesn't end with /images, append it
    if not url.endswith("/images"):
        url = f"{url}/images"
    return url


class ProviderDispatcher:
    """提供商调度器，用于解析配置并实例化、调用适当的图像生成 Provider。"""

    def __init__(self, plugin):
        self.plugin = plugin

    async def dispatch(
        self,
        event: AstrMessageEvent,
        params: dict,
        image_b64_list: list[tuple[str, str]] | None = None,
    ) -> tuple[list[tuple[str, str]] | None, str | None, list[str] | None]:
        """提供商调度逻辑，逐个尝试配置的绘图提供商，返回生成的图片数据、错误信息及图片 URL 列表。"""
        last_err = None

        # 1. 获取要尝试的提供商 ID 列表：优先取命令参数中指定的 provider，否则使用配置中的 provider，最后使用当前对话的 provider
        provider_param = params.get("providers")
        provider_ids = []
        if provider_param:
            if isinstance(provider_param, list):
                provider_ids = [str(p).strip() for p in provider_param if p]
            elif isinstance(provider_param, str):
                provider_ids = [
                    p.strip() for p in provider_param.split(",") if p.strip()
                ]
        else:
            conf_providers = self.plugin.conf.get("image_generation_providers", [])
            if isinstance(conf_providers, list):
                provider_ids = [str(p).strip() for p in conf_providers if p]
            elif isinstance(conf_providers, str):
                provider_ids = [
                    p.strip() for p in conf_providers.split(",") if p.strip()
                ]

        if not provider_ids:
            provider_ids = [None]

        # 2. 按顺序遍历提供商列表尝试生成
        for p_id in provider_ids:
            native_prov = None
            if p_id is not None:
                try:
                    native_prov = (
                        await self.plugin.context.provider_manager.get_provider_by_id(p_id)
                    )
                    if not native_prov:
                        # Case-insensitive fallback check for ID/name in inst_map
                        inst_map = getattr(
                            self.plugin.context.provider_manager, "inst_map", {}
                        )
                        for k, inst in inst_map.items():
                            k_lower = k.lower()
                            p_id_lower = p_id.lower()
                            if k_lower == p_id_lower or k_lower.startswith(
                                p_id_lower + "/"
                            ):
                                native_prov = inst
                                break
                            meta = getattr(inst, "meta", None)
                            if meta and callable(meta):
                                try:
                                    m = meta()
                                    m_id = getattr(m, "id", "").lower()
                                    if m and (
                                        m_id == p_id_lower
                                        or m_id.startswith(p_id_lower + "/")
                                    ):
                                        native_prov = inst
                                        break
                                except Exception:
                                    pass
                            prov_config = getattr(inst, "provider_config", {})
                            if prov_config:
                                conf_id = prov_config.get("id", "").lower()
                                conf_name = prov_config.get("name", "").lower()
                                if (
                                    conf_id == p_id_lower
                                    or conf_id.startswith(p_id_lower + "/")
                                    or conf_name == p_id_lower
                                    or conf_name.startswith(p_id_lower + "/")
                                ):
                                    native_prov = inst
                                    break
                except Exception as e:
                    logger.warning(f"[BIG BANANA] 获取原生提供商 {p_id} 失败: {e}")
            else:
                umo = event.unified_msg_origin if event else None
                try:
                    native_prov = self.plugin.context.get_using_provider(umo)
                except Exception as e:
                    logger.warning(
                        f"[BIG BANANA] 获取当前会话正在使用的提供商失败: {e}"
                    )

            if not native_prov:
                last_err = f"未找到可用的提供商实例 (ID: {p_id})"
                logger.warning(f"[BIG BANANA] {last_err}")
                continue

            # 3. 动态解析并构造 ProviderConfig
            native_type = native_prov.meta().type.lower()
            native_keys = native_prov.get_keys()

            model_name = (
                params.get("model")
                or native_prov.get_model()
                or native_prov.provider_config.get("model", "")
            )
            if not model_name:
                if "gemini" in native_type or "google" in native_type:
                    model_name = "gemini-3-pro-image-preview"
                else:
                    model_name = "dall-e-3"

            stream_val = self.plugin.conf.get("stream", False)

            api_type = "OpenAI_Images"
            api_url = ""

            api_base_val = native_prov.provider_config.get("api_base", "") or ""
            prov_id_val = native_prov.meta().id or ""
            prov_name_val = native_prov.provider_config.get("name", "") or ""

            is_agnes = (
                "agnes" in native_type
                or "agnes" in prov_id_val.lower()
                or "agnes" in prov_name_val.lower()
                or "agnes" in api_base_val.lower()
                or (model_name and "agnes" in model_name.lower())
            )

            if "gemini" in native_type or "google" in native_type:
                api_type = "Gemini"
                api_base = native_prov.provider_config.get("api_base")
                if api_base:
                    api_url = api_base.rstrip("/")
                else:
                    api_url = "https://generativelanguage.googleapis.com/v1beta/models"
            elif is_agnes:
                api_type = "Agnes_Images"
                api_base = api_base_val
                if api_base:
                    api_url = api_base.rstrip("/")
                    if api_url.endswith("/chat/completions"):
                        api_url = api_url[:-17]
                    elif api_url.endswith("/chat"):
                        api_url = api_url[:-5]
                    elif api_url.endswith("/images"):
                        api_url = api_url[:-7]
                    elif api_url.endswith("/generations"):
                        api_url = api_url[:-12]
                    api_url = api_url.rstrip("/")
                else:
                    api_url = ""
            else:
                api_type = "OpenAI_Images"
                api_base = native_prov.provider_config.get("api_base", "")
                api_url = get_images_url_from_api_base(api_base)

            provider_config = ProviderConfig(
                api_name=native_prov.meta().id,
                enabled=True,
                api_type=api_type,  # type: ignore
                keys=native_keys,
                api_url=api_url,
                model=model_name,
                stream=stream_val,
            )

            # 4. 动态加载/获取提供商实例
            provider_inst = self.plugin.provider_map.get(api_type)
            if provider_inst is None:
                provider_cls = BaseProvider.get_provider_class(api_type)
                if provider_cls is not None:
                    provider_inst = provider_cls(
                        config=self.plugin.conf,
                        common_config=self.plugin.common_config,
                        prompt_config=self.plugin.prompt_config,
                        session=self.plugin.http_manager._get_curl_session(),
                        downloader=self.plugin.downloader,
                    )
                    self.plugin.provider_map[api_type] = provider_inst

            if provider_inst is not None:
                try:
                    res, err = await provider_inst.generate_images(
                        provider_config=provider_config,
                        params=params,
                        image_b64_list=image_b64_list,
                    )
                    result_urls = getattr(provider_inst, "last_result_urls", None)
                    if res is not None:
                        return res, None, result_urls
                    last_err = err
                except Exception as e:
                    last_err = f"提供商 {p_id or 'default'} 请求异常: {e}"
                    logger.error(f"[BIG BANANA] {last_err}")
            else:
                last_err = f"未找到类型为 {api_type} 的提供商实现"
                logger.error(f"[BIG BANANA] {last_err}")

        return None, last_err, None
