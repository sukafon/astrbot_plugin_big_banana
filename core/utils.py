import base64
import mimetypes
import random
import shutil
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from astrbot.api import logger


def get_key_index(current_index: int, item_len: int) -> int:
    """获取key索引"""
    return (current_index + 1) % item_len


def save_images(
    image_result: list[tuple[str, str]], path_dir: Path
) -> list[tuple[str, Path]]:
    """保存图片到本地文件系统，返回 元组(文件名, 文件路径) 列表"""
    # 假设它支持返回多张图片
    saved_paths: list[tuple[str, Path]] = []
    for mime, b64 in image_result:
        if not b64:
            continue
        # 构建文件名
        now = datetime.now()
        current_time_str = (
            now.strftime("%Y%m%d%H%M%S") + f"{int(now.microsecond / 1000):03d}"
        )
        ext = mimetypes.guess_extension(mime) or ".jpg"
        file_name = f"banana_{current_time_str}{ext}"
        # 构建文件保存路径
        save_path = path_dir / file_name
        # 转换成bytes
        image_bytes = base64.b64decode(b64)
        # 保存到文件系统
        with open(save_path, "wb") as f:
            f.write(image_bytes)
        saved_paths.append((file_name, save_path))
        logger.info(f"[BIG BANANA] 图片已保存到 {save_path}")
    return saved_paths


def read_file(path) -> tuple[str | None, str | None]:
    try:
        with open(path, "rb") as f:
            file_data = f.read()
            mime_type, _ = mimetypes.guess_type(path)
            b64_data = base64.b64encode(file_data).decode("utf-8")
            return mime_type, b64_data
    except Exception as e:
        logger.error(f"[BIG BANANA] 读取参考图片 {path} 失败: {e}")
        return None, None


def clear_cache(temp_dir: Path):
    """清理缓存文件，应当在图片发送完成后调用"""
    if not temp_dir.exists():
        logger.warning(f"[BIG BANANA] 缓存目录 {temp_dir} 不存在")
        return
    for file in temp_dir.iterdir():
        try:
            if file.is_file():
                file.unlink()
                logger.debug(f"[BIG BANANA] 已删除缓存文件: {file}")
        except Exception as e:
            logger.error(f"[BIG BANANA] 删除缓存文件 {file} 失败: {e}")


def random_string(length: int) -> str:
    return "".join(
        random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(length)
    )


def copy_local_file(src: str, temp_dir: Path) -> str:
    """If the source path is local, copy it to the temp directory to prevent it from being deleted during the event lifecycle.

    Args:
        src: The source file path or URL.
        temp_dir: The destination directory to copy to.

    Returns:
        The copied file path if local, otherwise the original src.
    """
    if not src:
        return src
    if src.startswith(("http://", "https://")):
        return src

    path = src
    if path.startswith("file://"):
        path = urllib.request.url2pathname(urllib.parse.urlparse(path).path)

    src_path = Path(path)
    if src_path.exists() and src_path.is_file():
        # Copy to temp_dir with a unique name to avoid conflicts
        dest_filename = f"local_{random_string(8)}_{src_path.name}"
        dest_path = temp_dir / dest_filename
        try:
            shutil.copy2(src_path, dest_path)
            logger.debug(f"[BIG BANANA] Copied local temp image {src_path} to {dest_path}")
            return str(dest_path)
        except Exception as e:
            logger.error(f"[BIG BANANA] Failed to copy local image {src_path} to {dest_path}: {e}")

    return src

