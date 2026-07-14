from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..schemas import PARAMS_LIST

_INTEGER_PARAMS = {
    "min_images",
    "max_images",
    "num_inference_steps",
    "seed",
    "n",
    "partial_images",
    "fps",
}
_FLOAT_PARAMS = {"guidance_scale"}
_INTEGER_RANGES = {
    "min_images": (0, None),
    "max_images": (0, None),
    "num_inference_steps": (1, None),
    "n": (1, 10),
    "partial_images": (0, 3),
    "fps": (1, 120),
}
_BOOLEAN_PARAMS = {
    "google_search",
    "preset_append",
    "gather_mode",
    "url",
    "sub_brain",
    "with_audio",
    "watermark_enabled",
}

_INTERNAL_PROMPT_CONFIG: dict[str, dict] = {
    "llm_default": {
        "prompt": "{{user_text}}",
        "min_images": 0,
        "max_images": 6,
    },
    "llm_video_default": {
        "prompt": "{{user_text}}",
        "capability": "video_generation",
        "min_images": 0,
        "max_images": 1,
    },
}

if TYPE_CHECKING:
    from astrbot.core import AstrBotConfig


class PromptConfigManager:
    """提示词配置"""

    def __init__(self, conf: AstrBotConfig) -> None:
        self.conf = conf
        self.params_alias = self._build_params_alias()
        self.prompt_config = self._build_prompt_config()

    def _build_params_alias(self) -> dict[str, str]:
        """解析参数别名。"""
        params_alias: dict[str, str] = {}
        for item in self.conf.get("params_alias_map", []):
            alias, _, param = item.partition(":")
            if alias and param:
                params_alias[alias] = param
            else:
                logger.warning(
                    f"[Big Banana] 参数别名映射配置错误，未指定参数名称：{item}，已跳过处理"
                )
        return params_alias

    def _build_prompt_config(self) -> dict[str, dict]:
        """解析预设提示词"""
        result: dict[str, dict] = {}
        for item in self.conf.get("prompt", []):
            if not isinstance(item, str):
                logger.warning(f"[BIG BANANA] 预设提示词不是字符串，已跳过：{item!r}")
                continue
            preset_parts = item.split(maxsplit=1)
            if len(preset_parts) != 2 or not preset_parts[1].strip():
                logger.warning(
                    f"[BIG BANANA] 预设提示词缺少触发词或正文，已跳过：{item!r}"
                )
                continue
            cmd_raw, prompt = preset_parts
            if cmd_raw.startswith("[") and cmd_raw.endswith("]"):
                cmd_list = [
                    cmd.strip() for cmd in cmd_raw[1:-1].split(",") if cmd.strip()
                ]
            else:
                cmd_list = [cmd_raw]

            params = self.parse_prompt_params(prompt)
            for cmd in cmd_list:
                result[cmd] = params

        # 配置中的同名预设优先，仅补齐缺失的 LLM 工具默认预设。
        for name, params in _INTERNAL_PROMPT_CONFIG.items():
            result.setdefault(name, params.copy())
        return result

    def parse_prompt_params(self, prompt: str) -> dict:
        """解析提示词参数，不包含触发词"""
        params: dict = {}

        # 按行记录单词
        filtered_lines: list[str] = []

        # 按行解析，之后按行拼接，从而保留原始提示词的换行符
        for line in prompt.split("\n"):
            if not line.strip():
                filtered_lines.append("")
                continue

            # 迭代器，split会自动处理掉连续空格或者制表符，这里作为自动美化处理
            tokens_iter = iter(line.split())
            # 过滤后的单词列表
            filtered: list[str] = []

            # 解析参数
            while True:
                token = next(tokens_iter, None)
                if token is None:
                    break
                if token.startswith("--"):
                    key = token[2:]
                    # 处理参数别称映射
                    if key in self.params_alias:
                        key = self.params_alias[key]
                    # 仅处理已知参数
                    if key in PARAMS_LIST:
                        value = next(tokens_iter, None)
                        if value is None:
                            if key in _BOOLEAN_PARAMS:
                                params[key] = True
                            else:
                                logger.warning(
                                    f"[BIG BANANA] 参数 --{key} 缺少参数值，已忽略"
                                )
                            break
                        value = value.strip()
                        if value.startswith("--"):
                            if key in _BOOLEAN_PARAMS:
                                params[key] = True
                            else:
                                logger.warning(
                                    f"[BIG BANANA] 参数 --{key} 缺少参数值，已忽略"
                                )
                            # 将被提前迭代的单词放回迭代流的最前端
                            tokens_iter = itertools.chain([value], tokens_iter)
                            continue
                        if key == "providers":
                            # 预解析成列表
                            params[key] = [
                                p.strip() for p in value.split(",") if p.strip()
                            ]
                        elif key in _BOOLEAN_PARAMS:
                            if value.lower() in {"true", "false"}:
                                params[key] = value.lower() == "true"
                            else:
                                params[key] = True
                                tokens_iter = itertools.chain([value], tokens_iter)
                        elif key in _INTEGER_PARAMS:
                            try:
                                parsed_value = int(value)
                            except ValueError:
                                logger.warning(
                                    f"[BIG BANANA] 参数 --{key} 需要整数，"
                                    f"已忽略无效值：{value}"
                                )
                            else:
                                minimum, maximum = _INTEGER_RANGES.get(
                                    key, (None, None)
                                )
                                if (minimum is not None and parsed_value < minimum) or (
                                    maximum is not None and parsed_value > maximum
                                ):
                                    logger.warning(
                                        f"[BIG BANANA] 参数 --{key} 超出允许范围，"
                                        f"已忽略无效值：{value}"
                                    )
                                else:
                                    params[key] = parsed_value
                        elif key in _FLOAT_PARAMS:
                            try:
                                parsed_value = float(value)
                            except ValueError:
                                logger.warning(
                                    f"[BIG BANANA] 参数 --{key} 需要数字，"
                                    f"已忽略无效值：{value}"
                                )
                            else:
                                if math.isfinite(parsed_value):
                                    params[key] = parsed_value
                                else:
                                    logger.warning(
                                        f"[BIG BANANA] 参数 --{key} 需要有限数字，"
                                        f"已忽略无效值：{value}"
                                    )
                        elif value.lower() == "true":
                            params[key] = True
                        elif value.lower() == "false":
                            params[key] = False
                        else:
                            params[key] = value
                        continue
                filtered.append(token)

            # 用空格拼接成一行
            filtered_line = " ".join(filtered)

            # 如果不为空，则保留该行，理论上不会产生多余空格，不需要.strip()
            if filtered_line:
                filtered_lines.append(filtered_line)

        # 归还换行符
        params["prompt"] = "\n".join(filtered_lines).strip() or "draw a picture"
        return params
