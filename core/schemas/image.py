from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image as PILImage
from PIL import ImageOps

from astrbot.api import logger

if TYPE_CHECKING:
    from pathlib import Path

PILImage.init()


@dataclass(repr=False, slots=True, eq=False)
class ImageResource:
    """封装图片元数据和懒加载base64编码数据类型"""

    mime: str
    bytes: bytes
    url: str | Path | None = None
    _b64_cache: str | None = field(default=None, init=False, compare=False)

    def __init__(
        self,
        mime: str,
        data_bytes: bytes,
        url: str | Path | None = None,
    ) -> None:
        self.mime = mime
        self.bytes = data_bytes
        self.url = url
        self._b64_cache = None

    @staticmethod
    def strip_metadata(image_bytes: bytes) -> bytes | None:
        """清理静态图片隐私元数据，并修正 EXIF Orientation。"""
        try:
            with PILImage.open(BytesIO(image_bytes)) as img:
                fmt = img.format
                if not fmt:
                    logger.error("[BIG BANANA] 图片格式无法识别")
                    return None

                if (
                    getattr(img, "is_animated", False)
                    or getattr(img, "n_frames", 1) > 1
                ):
                    logger.error("[BIG BANANA] 多帧图片不支持")
                    return None

                fmt_upper = fmt.upper()
                out = BytesIO()

                # 单帧 GIF 的透明信息不是隐私，提前取出来
                gif_transparency = None
                if fmt_upper == "GIF":
                    gif_transparency = img.info.get("transparency")

                # 修正 EXIF Orientation，再清理元数据
                clean = ImageOps.exif_transpose(img).copy()
                clean.info.clear()

                if fmt_upper in {"JPEG", "JPG"}:
                    if clean.mode in {"RGBA", "LA"}:
                        rgba = clean.convert("RGBA")
                        background = PILImage.new("RGB", rgba.size, (255, 255, 255))
                        background.paste(rgba, mask=rgba.getchannel("A"))
                        clean = background
                    elif clean.mode != "RGB":
                        clean = clean.convert("RGB")

                    clean.save(out, format="JPEG", quality=95, optimize=True)

                elif fmt_upper == "GIF":
                    save_kwargs = {"format": "GIF"}

                    # 保留透明，不保留 comment 等隐私/描述元数据
                    if gif_transparency is not None:
                        save_kwargs["transparency"] = gif_transparency

                    clean.save(out, **save_kwargs)

                else:
                    clean.save(out, format=fmt)

                return out.getvalue()
        except Exception as exc:
            logger.warning(f"[BIG BANANA] 图片元数据清理失败: {exc}")
            return None

    @classmethod
    def from_base64(
        cls,
        data_base64: str,
        *,
        url: str | Path | None = None,
    ) -> ImageResource | None:
        """从 base64 构造图片资源，支持 data URL、base64:// 和裸 base64。"""
        payload = cls._split_base64_source(data_base64)
        if not payload.strip():
            return None
        try:
            data_bytes = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError, TypeError):
            logger.error("[BIG BANANA] base64解码失败")
            return None

        image = cls.from_bytes(data_bytes, url=url)
        if not image:
            return None

        image._b64_cache = payload
        return image

    @classmethod
    def from_bytes(
        cls,
        data_bytes: bytes,
        *,
        url: str | Path | None = None,
    ) -> ImageResource | None:
        """从图片字节构造资源，自动读取 MIME。"""
        detected_mime = cls._sniff_mime(data_bytes)
        if not detected_mime:
            logger.error("[BIG BANANA] 无法识别图片格式")
            return None
        return cls(mime=detected_mime, data_bytes=data_bytes, url=url)

    @staticmethod
    def _split_base64_source(source: str) -> str:
        """拆分常见 base64 图片引用，返回 payload。"""
        text = source.strip().strip("\"'")
        if text.startswith("base64://"):
            return text.removeprefix("base64://")

        header, sep, payload = text.partition(",")
        if sep and header.lower().startswith("data:"):
            _, _, metadata = header.removeprefix("data:").partition(";")
            if "base64" in metadata.lower().split(";"):
                return payload
        if sep and header.lower().startswith("image/"):
            _, _, metadata = header.partition(";")
            if "base64" in metadata.lower().split(";"):
                return payload

        return text

    @staticmethod
    def _sniff_mime(data: bytes) -> str | None:
        """读取图片格式并返回 MIME。"""
        fmt = ""
        try:
            with PILImage.open(BytesIO(data)) as img:
                fmt = (img.format or "").upper()
                img.verify()
        except Exception as e:
            logger.error(f"[BIG BANANA] 图片格式识别失败: {e}")
            return None

        if not fmt:
            logger.error("[BIG BANANA] 未检测到图片格式")
            return None
        return PILImage.MIME.get(fmt, f"image/{fmt.lower()}")

    @property
    def base64(self) -> str:
        """返回图片base64编码后的字符串，并缓存结果"""
        if self._b64_cache is None:
            self._b64_cache = base64.b64encode(self.bytes).decode("utf-8")
        return self._b64_cache

    def to_data_url(self) -> str:
        """返回可直接传给多模态接口的 data URL。"""
        return f"data:{self.mime};base64,{self.base64}"
