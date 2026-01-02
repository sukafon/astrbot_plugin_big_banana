import base64
from io import BytesIO

from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import (
    CertificateVerifyError,
    SSLError,
    Timeout,
)
from PIL import Image

from astrbot.api import logger

from .data import CommonConfig


class Downloader:
    def __init__(self, session: AsyncSession, common_config: CommonConfig):
        self.session = session
        self.def_common_config = common_config

    async def fetch_image(self, url: str) -> tuple[str, str] | None:
        """下载单张图片并转换为 (mime, base64)"""
        # 重试逻辑
        for _ in range(3):
            content = await self._download_image(url)
            if content is not None:
                return content

    async def fetch_images(self, image_urls: list[str]) -> list[tuple[str, str]]:
        """下载多张图片并转换为 (mime, base64) 列表"""
        image_b64_list = []
        for url in image_urls:
            # 重试逻辑
            for _ in range(3):
                content = await self._download_image(url)
                if content is not None:
                    image_b64_list.append(content)
                    break  # 成功就跳出重试
        return image_b64_list

    @staticmethod
    def _handle_image(image_bytes: bytes) -> tuple[str, str] | None:
        if len(image_bytes) > 36 * 1024 * 1024:
            logger.warning("[BIG BANANA] 图片超过 36MB，跳过处理")
            return None
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                fmt = (img.format or "").upper()
                # 如果不是 GIF，直接返回原图
                if fmt != "GIF":
                    mime = f"image/{fmt.lower()}"
                    b64 = base64.b64encode(image_bytes).decode("utf-8")
                    return (mime, b64)
                # 处理 GIF
                buf = BytesIO()
                # 取第一帧
                img.seek(0)
                img = img.convert("RGBA")
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return ("image/png", b64)
        except Exception as e:
            logger.warning(f"[BIG BANANA] GIF 处理失败，返回原图: {e}")
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            return ("image/gif", b64)

    async def _download_image(self, url: str) -> tuple[str, str] | None:
        try:
            response = await self.session.get(url, impersonate="chrome131", proxy=self.def_common_config.proxy, timeout=30)
            content = Downloader._handle_image(response.content)
            return content
        except (SSLError, CertificateVerifyError):
            # 关闭SSL验证
            response = await self.session.get(
                url, impersonate="chrome131", timeout=30, verify=False
            )
            content = Downloader._handle_image(response.content)
            return content
        except Timeout as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {url}，错误信息：{e}")
            return None
        except Exception as e:
            logger.error(f"[BIG BANANA] 下载图片失败: {url}，错误信息：{e}")
            return None
