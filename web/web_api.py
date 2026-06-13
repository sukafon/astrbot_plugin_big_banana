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
