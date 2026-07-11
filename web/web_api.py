from quart import jsonify
from quart import request as qreq

from astrbot.api import logger

PLUGIN_NAME = "astrbot_plugin_big_banana"


class BigBananaWebApi:
    """Web API handlers for the Big Banana plugin configuration dashboard."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.context = plugin.context
        self.logger = logger

    def register_routes(self):
        """Register routes to the AstrBot context web server."""
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
        """GET handler: return the current configuration as JSON."""
        try:
            config_data = {}
            if self.plugin.conf is not None:
                config_data = dict(self.plugin.conf)
            return jsonify({"status": "ok", "data": config_data})
        except Exception as e:
            self.logger.exception(f"Failed to get config: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_config_set(self):
        """POST handler: update config dictionary and save it."""
        try:
            body = await qreq.get_json()
            if not body:
                return jsonify({"status": "error", "message": "Request body is empty"})

            # Update configuration keys
            for k, v in body.items():
                self.plugin.conf[k] = v

            # Save the updated config to disk
            self.plugin.conf.save_config()

            # Trigger the plugin config reload to apply changes dynamically
            self.plugin.refresh_config()
            return jsonify({"status": "ok"})
        except Exception as e:
            self.logger.exception(f"Failed to save config: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_providers_list(self):
        """GET handler: return a list of active model providers."""
        try:
            providers = []
            if hasattr(self.context, "provider_manager") and hasattr(
                self.context.provider_manager, "provider_insts"
            ):
                for p in self.context.provider_manager.provider_insts:
                    p_id = getattr(p, "id", None)
                    if not p_id and hasattr(p, "meta"):
                        meta = p.meta()
                        p_id = getattr(meta, "id", None)
                    if p_id:
                        providers.append({"id": p_id, "name": p_id})
            return jsonify({"status": "ok", "data": providers})
        except Exception as e:
            self.logger.exception(f"Failed to get providers list: {e}")
            return jsonify({"status": "ok", "data": []})

    async def api_substitutions_get(self):
        """GET handler: return the current avatar substitutions from the JSON file."""
        try:
            substitutions = {}
            import json
            import os

            path = self.plugin.refer_images_dir.parent / "avatar_substitutions.json"
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        substitutions = json.load(f)
                except Exception:
                    self.logger.warning(
                        "Failed to parse avatar_substitutions.json, defaulting to empty."
                    )
            return jsonify({"status": "ok", "data": substitutions})
        except Exception as e:
            self.logger.exception(f"Failed to get substitutions: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_substitutions_set(self):
        """POST handler: update and save avatar substitutions map."""
        try:
            body = await qreq.get_json()
            if not isinstance(body, dict):
                return jsonify(
                    {"status": "error", "message": "Request body must be a JSON object"}
                )

            import json

            path = self.plugin.refer_images_dir.parent / "avatar_substitutions.json"

            # Save mapping to file
            with open(path, "w", encoding="utf-8") as f:
                json.dump(body, f, indent=4, ensure_ascii=False)

            # Reload configuration in the plugin memory
            self.plugin.refresh_config()
            return jsonify({"status": "ok"})
        except Exception as e:
            self.logger.exception(f"Failed to save substitutions: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_upload_image(self):
        """POST handler: receive an uploaded file and save it to refer_images directory."""
        try:
            import base64
            import os
            import uuid

            # 1. Check if request is JSON (base64 upload)
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
                    with open(file_path, "wb") as f:
                        f.write(file_bytes)
                    return jsonify({"status": "ok", "data": {"filename": filename}})

            # 2. Fallback to multipart file upload
            files = await qreq.files
            file = files.get("file")
            if not file:
                return jsonify({"status": "error", "message": "No file uploaded"})

            # Secure filename and prevent path traversal
            filename = os.path.basename(file.filename)
            if not filename:
                filename = f"upload_{uuid.uuid4().hex}.jpg"

            os.makedirs(self.plugin.refer_images_dir, exist_ok=True)
            file_path = self.plugin.refer_images_dir / filename
            await file.save(str(file_path))

            return jsonify({"status": "ok", "data": {"filename": filename}})
        except Exception as e:
            self.logger.exception(f"Failed to upload image: {e}")
            return jsonify({"status": "error", "message": str(e)})

    async def api_serve_image(self):
        """GET handler: return base64 encoded data of an image file from the refer_images directory."""
        try:
            filename = qreq.args.get("filename", "")
            if not filename:
                return jsonify({"status": "error", "message": "Missing filename"})

            import base64
            import mimetypes
            import os

            filename = os.path.basename(filename)
            file_path = self.plugin.refer_images_dir / filename
            if not file_path.exists():
                return jsonify({"status": "error", "message": "File not found"}), 404

            with open(file_path, "rb") as f:
                file_bytes = f.read()

            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                mime_type = "image/jpeg"

            b64_data = base64.b64encode(file_bytes).decode("utf-8")
            data_url = f"data:{mime_type};base64,{b64_data}"

            return jsonify({"status": "ok", "data": {"base64": data_url}})
        except Exception as e:
            self.logger.exception(f"Failed to serve image: {e}")
            return jsonify({"status": "error", "message": str(e)})
