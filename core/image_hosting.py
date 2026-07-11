import uuid
from datetime import datetime, timezone

from aiohttp import ClientSession

from astrbot.api import logger

from .data import ImageHostingConfig


class R2ImageHoster:
    """基于 Cloudflare Worker + R2 的图床上传器"""

    def __init__(self, session: ClientSession, config: ImageHostingConfig):
        self.session = session
        self.config = config

    def is_enabled(self) -> bool:
        return bool(
            self.config.enabled
            and self.config.upload_url.strip()
            and self.config.public_base_url.strip()
            and self.config.auth_token.strip()
        )

    async def upload_images(self, image_result: list[tuple[str, str]]) -> list[str]:
        urls: list[str] = []
        for mime, b64_data in image_result:
            upload_key = self._build_upload_key(mime)
            image_bytes = self._decode_base64(b64_data)
            await self._upload_bytes(upload_key, image_bytes, mime)
            urls.append(self._build_public_url(upload_key))
        return urls

    def _build_upload_key(self, mime: str) -> str:
        now = datetime.now(timezone.utc)
        ext = self._mime_to_ext(mime)
        date_path = now.strftime("%Y/%m/%d")
        prefix = self.config.path_prefix.strip().strip("/")
        filename = f"{uuid.uuid4().hex}.{ext}"
        if prefix:
            return f"{prefix}/{date_path}/{filename}"
        return f"{date_path}/{filename}"

    async def _upload_bytes(
        self, upload_key: str, image_bytes: bytes, mime: str
    ) -> None:
        upload_url = f"{self.config.upload_url.rstrip('/')}/{upload_key}"
        async with self.session.put(
            upload_url,
            data=image_bytes,
            headers={
                "X-Auth-Token": self.config.auth_token,
                "Content-Type": mime,
            },
        ) as response:
            if response.status < 200 or response.status >= 300:
                resp_text = await response.text()
                logger.error(
                    f"[BIG BANANA] 图床上传失败，状态码: {response.status}, 响应内容: {resp_text[:512]}"
                )
                raise ValueError("图床上传失败")

    def _build_public_url(self, upload_key: str) -> str:
        return f"{self.config.public_base_url.rstrip('/')}/{upload_key}"

    @staticmethod
    def _decode_base64(b64_data: str) -> bytes:
        import base64

        return base64.b64decode(b64_data)

    @staticmethod
    def _mime_to_ext(mime: str) -> str:
        mime_map = {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
            "image/bmp": "bmp",
        }
        return mime_map.get(mime.lower(), "jpg")
