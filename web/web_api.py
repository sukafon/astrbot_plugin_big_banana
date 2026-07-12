import base64
import json
import mimetypes
import os
import uuid

from quart import jsonify
from quart import request as qreq

from astrbot.api import logger

PLUGIN_NAME = "astrbot_plugin_big_banana"


class BigBananaWebApi:
    """大香蕉插件配置面板的 Web API 处理器。"""

    def __init__(self, plugin):
        """保存插件实例、上下文和日志器引用。"""
        self.plugin = plugin
        self.context = plugin.context
        self.logger = logger

    def register_routes(self):
        """把配置面板所需的后端接口注册到 AstrBot Web 服务。"""
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/config",
            self.api_config_get,
            ["GET"],
            "获取画图插件配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/config",
            self.api_config_set,
            ["POST"],
            "更新画图插件配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/providers",
            self.api_providers_list,
            ["GET"],
            "获取可用的模型供应商",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/substitutions",
            self.api_substitutions_get,
            ["GET"],
            "获取人设头像替换列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/substitutions",
            self.api_substitutions_set,
            ["POST"],
            "更新人设头像替换列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/upload_image",
            self.api_upload_image,
            ["POST"],
            "上传人设参考图片",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/image",
            self.api_serve_image,
            ["GET"],
            "获取人设参考图片内容",
        )

    async def api_config_get(self):
        """返回当前插件配置的 JSON 数据。"""
        try:
            config_data = {}
            if self.plugin.conf is not None:
                config_data = dict(self.plugin.conf)
                config_data["image_generation_providers"] = list(
                    self.plugin.provider_config_manager.default_providers
                )
                config_data["video_generation_providers"] = list(
                    self.plugin.provider_config_manager.default_video_providers
                )
            return jsonify({"status": "ok", "data": config_data})
        except Exception as e:
            self.logger.exception(f"获取配置失败: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_config_set(self):
        """保存前端提交的配置并标记需要重新加载插件。"""
        try:
            body = await qreq.get_json()
            if not isinstance(body, dict) or not body:
                return jsonify({"status": "error", "message": "请求体不能为空"})

            body = dict(body)
            params_config = body.get("params_config")
            if params_config is not None:
                if not isinstance(params_config, dict):
                    return jsonify(
                        {"status": "error", "message": "params_config 必须是对象。"}
                    )
                for key in ("min_images", "max_images"):
                    value = params_config.get(key)
                    if value is not None and (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 0
                    ):
                        return jsonify(
                            {
                                "status": "error",
                                "message": f"{key} 必须是非负整数。",
                            }
                        )

            prompt_items = body.get("prompt")
            invalid_prompt_item = not isinstance(prompt_items, list) or any(
                not isinstance(item, str)
                or len(item.split(maxsplit=1)) != 2
                or not item.split(maxsplit=1)[1].strip()
                for item in prompt_items or []
            )
            if prompt_items is not None and invalid_prompt_item:
                return jsonify(
                    {
                        "status": "error",
                        "message": "每条预设都必须包含触发词和非空提示词正文。",
                    }
                )

            selected_by_capability: dict[str, list[str]] = {}
            for config_key, capability in (
                ("image_generation_providers", "image_generation"),
                ("video_generation_providers", "video_generation"),
            ):
                selected_providers = body.pop(config_key, None)
                if not isinstance(selected_providers, list):
                    continue
                selected_names: list[str] = []
                for name in selected_providers:
                    normalized_name = str(name).strip()
                    if normalized_name and normalized_name not in selected_names:
                        selected_names.append(normalized_name)
                selected_by_capability[capability] = selected_names

            if selected_by_capability:
                provider_templates = [
                    dict(item)
                    for item in (
                        body.get(
                            "provider_template",
                            self.plugin.conf.get("provider_template", []),
                        )
                        or []
                    )
                    if isinstance(item, dict)
                ]
                template_names = {
                    item.get("name", "").strip()
                    for item in provider_templates
                    if item.get("name", "").strip()
                    and item.get("capability", "image_generation") == "image_generation"
                }
                for item in provider_templates:
                    capability = item.get("capability", "image_generation")
                    selected_names = selected_by_capability.get(capability)
                    if selected_names is None:
                        continue
                    provider_order = {
                        name: index for index, name in enumerate(selected_names)
                    }
                    name = item.get("name", "").strip()
                    item["enabled_as_default"] = name in provider_order
                    if name in provider_order:
                        item["fallback_order"] = provider_order[name]
                body["provider_template"] = provider_templates
                image_selected_names = selected_by_capability.get("image_generation")
                if image_selected_names is not None:
                    body["default_astr_providers"] = [
                        name
                        for name in image_selected_names
                        if name not in template_names
                    ]

            # 更新配置键值。
            for k, v in body.items():
                self.plugin.conf[k] = v

            # 保存更新后的配置到磁盘。
            self.plugin.conf.save_config()

            return jsonify(
                {
                    "status": "ok",
                    "message": "配置已保存，重新加载插件后生效。",
                    "restart_required": True,
                }
            )
        except Exception as e:
            self.logger.exception(f"保存配置失败: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_providers_list(self):
        """查询当前 AstrBot 中可用的模型提供商列表。"""
        try:
            providers = []
            seen = set()
            for config in self.plugin.provider_config_manager.provider_configs.values():
                providers.append(
                    {
                        "id": config.name,
                        "name": f"{config.name} ({config.provider_type})",
                        "capability": config.capability,
                    }
                )
                seen.add(config.name.lower())
            if hasattr(self.context, "provider_manager") and hasattr(
                self.context.provider_manager, "provider_insts"
            ):
                for p in self.context.provider_manager.provider_insts:
                    p_id = getattr(p, "id", None)
                    if not p_id and hasattr(p, "meta"):
                        meta = p.meta()
                        p_id = getattr(meta, "id", None)
                    if p_id and p_id.lower() not in seen:
                        providers.append(
                            {
                                "id": p_id,
                                "name": p_id,
                                "capability": "image_generation",
                            }
                        )
                        seen.add(p_id.lower())
            return jsonify({"status": "ok", "data": providers})
        except Exception as e:
            self.logger.exception(f"获取提供商列表失败: {e}")
            return jsonify({"status": "error", "message": str(e), "data": []})

    async def api_substitutions_get(self):
        """读取头像替换映射配置并返回给前端。"""
        try:
            substitutions = {}
            path = self.plugin.refer_images_dir.parent / "avatar_substitutions.json"
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        substitutions = json.load(f)
                except Exception:
                    self.logger.warning(
                        "解析 avatar_substitutions.json 失败，已按空映射处理。"
                    )
            return jsonify({"status": "ok", "data": substitutions})
        except Exception as e:
            self.logger.exception(f"获取头像替换配置失败: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_substitutions_set(self):
        """保存头像替换映射并刷新插件内存配置。"""
        try:
            body = await qreq.get_json()
            if not isinstance(body, dict):
                return jsonify({"status": "error", "message": "请求体必须是 JSON 对象"})

            path = self.plugin.refer_images_dir.parent / "avatar_substitutions.json"

            # 保存映射到文件。
            with open(path, "w", encoding="utf-8") as f:
                json.dump(body, f, indent=4, ensure_ascii=False)

            avatar_map = {}
            for key, value in body.items():
                if isinstance(value, str):
                    references = [value.strip()] if value.strip() else []
                elif isinstance(value, list):
                    references = [
                        item.strip()
                        for item in value
                        if isinstance(item, str) and item.strip()
                    ]
                else:
                    references = []
                if references:
                    avatar_map[str(key)] = references
            self.plugin.avatar_map = avatar_map
            return jsonify({"status": "ok"})
        except Exception as e:
            self.logger.exception(f"保存头像替换配置失败: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_upload_image(self):
        """接收前端上传的参考图片并保存到插件目录。"""
        try:
            # 优先处理 JSON 形式的 base64 上传。
            if qreq.is_json:
                body = await qreq.get_json()
                if body and "base64" in body:
                    raw_filename = body.get("filename", "")
                    b64_data = body["base64"]
                    if "," in b64_data:
                        b64_data = b64_data.split(",", 1)[1]

                    file_bytes = base64.b64decode(b64_data)
                    filename = os.path.basename(raw_filename)
                    if not filename:
                        filename = f"upload_{uuid.uuid4().hex}.jpg"

                    os.makedirs(self.plugin.refer_images_dir, exist_ok=True)
                    file_path = self.plugin.refer_images_dir / filename
                    file_path.write_bytes(file_bytes)
                    return jsonify({"status": "ok", "data": {"filename": filename}})

            # 兼容 multipart 文件上传。
            files = await qreq.files
            file = files.get("file")
            if not file:
                return jsonify({"status": "error", "message": "未上传文件"})

            # 清理文件名，避免路径穿越。
            filename = os.path.basename(file.filename)
            if not filename:
                filename = f"upload_{uuid.uuid4().hex}.jpg"

            os.makedirs(self.plugin.refer_images_dir, exist_ok=True)
            file_path = self.plugin.refer_images_dir / filename
            await file.save(str(file_path))

            return jsonify({"status": "ok", "data": {"filename": filename}})
        except Exception as e:
            self.logger.exception(f"上传图片失败: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_serve_image(self):
        """读取本地参考图片并转换为前端可预览的 data URL。"""
        try:
            filename = qreq.args.get("filename", "")
            if not filename:
                return jsonify({"status": "error", "message": "缺少文件名"})

            filename = os.path.basename(filename)
            file_path = self.plugin.refer_images_dir / filename
            if not file_path.exists():
                return jsonify({"status": "error", "message": "文件不存在"}), 404

            file_bytes = file_path.read_bytes()

            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                mime_type = "image/jpeg"

            b64_data = base64.b64encode(file_bytes).decode("utf-8")
            data_url = f"data:{mime_type};base64,{b64_data}"

            return jsonify({"status": "ok", "data": {"base64": data_url}})
        except Exception as e:
            self.logger.exception(f"读取图片失败: {e}")
            return jsonify({"status": "error", "message": str(e)})
