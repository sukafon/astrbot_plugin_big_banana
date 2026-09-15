import math
import re
from io import BytesIO

from PIL import Image

from astrbot.api import logger

from ..schemas import ImageResource, ParamsConfig


def dedupe_images(images: list[ImageResource]) -> list[ImageResource]:
    """按图片字节内容去除重复结果。"""
    deduped: list[ImageResource] = []
    seen: set[bytes] = set()
    for image in images:
        if image.bytes in seen:
            continue
        seen.add(image.bytes)
        deduped.append(image)
    if len(images) != len(deduped):
        logger.debug(
            f"[BIG BANANA] 去除重复图片，从 {len(images)} 张减少到 {len(deduped)} 张"
        )
    return deduped


def extract_markdown_images(text: str) -> tuple[list[str], list[str]]:
    """从 Markdown 图片语法中提取 base64 和 URL 图片引用。"""
    base64_sources: list[str] = []
    image_urls: list[str] = []

    # 这里使用finditer遍历，避免search只会返回第一个匹配
    for match in re.finditer(r"!\[.*?\]\((.*?)\)", text):
        img_src = match.group(1).strip()
        # 移除Markdown允许的<..>格式
        if img_src.startswith("<") and img_src.endswith(">"):
            img_src = img_src[1:-1].strip()

        if img_src.startswith("data:image/"):  # base64格式
            base64_sources.append(img_src)
        else:  # url格式
            image_urls.append(img_src)
    return base64_sources, image_urls


def parse_response_modalities(raw: str | list[str]) -> list[str]:
    """解析 Gemini/Vertex 提供商配置中的响应模式。"""
    if isinstance(raw, list):
        return raw
    if raw == "无":
        return []
    return [
        item.strip().strip("\"'")
        for item in raw.strip("[]").split(",")
        if item.strip().strip("\"'")
    ]


def get_openai_size(
    params: dict,
    params_config: ParamsConfig,
    image_list: list[ImageResource],
) -> str:
    """根据参数、提示词或参考图尺寸推导 OpenAI 图片输出大小。"""
    configured_size = params.get("size", params_config.size)
    if configured_size != "default":
        return configured_size

    prompt = params.get("prompt", "")
    for keywords, size in params_config.size_keyword_map.items():
        for keyword in keywords:
            if keyword in prompt:
                return size

    if image_list:
        img = image_list[0]
        raw_bytes = img.bytes
        try:
            with Image.open(BytesIO(raw_bytes)) as img_obj:
                w, h = img_obj.size

            if w > 3 * h:
                w = 3 * h
            elif h > 3 * w:
                h = 3 * w

            max_area = 8294400
            min_area = 655360
            max_edge = 3840

            scale = 1.0
            if w * h > max_area:
                scale = math.sqrt(max_area / (w * h))
            elif w * h < min_area:
                scale = math.sqrt(min_area / (w * h))

            w = int(w * scale)
            h = int(h * scale)

            if w > max_edge:
                scale = max_edge / w
                w = max_edge
                h = int(h * scale)
            if h > max_edge:
                scale = max_edge / h
                h = max_edge
                w = int(w * scale)

            w = max(16, round(w / 16) * 16)
            h = max(16, round(h / 16) * 16)

            if w > 3 * h:
                w = 3 * h
                w = max(16, round(w / 16) * 16)
            elif h > 3 * w:
                h = 3 * w
                h = max(16, round(h / 16) * 16)

            while w * h > max_area or max(w, h) > max_edge:
                if w > h:
                    w -= 16
                else:
                    h -= 16

            while w * h < min_area:
                if w < h:
                    w += 16
                else:
                    h += 16

            return f"{w}x{h}"
        except Exception as e:
            logger.warning(
                f"[BIG BANANA] 获取参考图分辨率失败: {e}，将使用默认尺寸 auto"
            )

    return "auto"
