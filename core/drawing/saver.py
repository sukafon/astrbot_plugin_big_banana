from __future__ import annotations

import uuid
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image

from astrbot.api import logger

if TYPE_CHECKING:
    from pathlib import Path

    from ..schemas import ImageResource


def save_images_to_local(
    images: list[ImageResource], path_dir: Path
) -> list[tuple[str, Path]]:
    """保存图片到本地文件系统，返回元组(文件名, 文件路径)列表。"""
    saved_paths: list[tuple[str, Path]] = []
    for image in images:
        if not image.bytes:
            logger.warning("[BIG BANANA] 图片数据为空，跳过保存")
            continue

        file_name = _build_filename(image)
        if not file_name:
            logger.warning("[BIG BANANA] 无法识别图片格式，跳过保存")
            continue
        save_path = path_dir / file_name

        save_path.write_bytes(image.bytes)

        saved_paths.append((file_name, save_path))
        logger.info(f"[BIG BANANA] 图片已保存到 {save_path}")
    return saved_paths


def _build_filename(image: ImageResource) -> str | None:
    """生成本地保存文件名：保留原图格式，不转换 bytes。"""
    ext = _detect_original_ext(image.bytes)
    if ext is None:
        return None
    return f"{uuid.uuid4().hex}.{ext}"


def _detect_original_ext(image_bytes: bytes) -> str | None:
    """从图片 bytes 识别原始格式扩展名。"""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            fmt = (img.format or "").lower()
            if not fmt:
                return None
            return "jpg" if fmt == "jpeg" else fmt
    except Exception as e:
        logger.error(f"[BIG BANANA] 图片格式识别失败: {e}")
        return None
