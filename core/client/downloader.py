import asyncio
import base64
import binascii
import ipaddress
import socket
import urllib.parse
import urllib.request
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from aiohttp import (
    ClientConnectorCertificateError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
)
from PIL import Image

from astrbot.api import logger

from ..schemas.image import ImageResource

# 转换白名单
_MIME_MAP = {
    "webp": "image/webp",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


_GIF_MIME_MAP = {
    **_MIME_MAP,
    "gif": "image/gif",
}

_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


async def _read_image_response(response: ClientResponse) -> bytes | None:
    """Read a complete response body while enforcing the image size limit."""
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > _MAX_IMAGE_BYTES:
                logger.warning("[BIG BANANA] 图片超过 50MB，跳过处理")
                return None
        except ValueError:
            pass

    content = bytearray()
    async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
        content.extend(chunk)
        if len(content) > _MAX_IMAGE_BYTES:
            logger.warning("[BIG BANANA] 图片超过 50MB，跳过处理")
            return None

    return bytes(content) if content else None


class Downloader:
    def __init__(self, session: ClientSession, http_proxy: str | None = None):
        """下载管理器"""
        self.session = session
        self.http_proxy = http_proxy

    async def fetch_image(
        self,
        url: str | Path,
        *,
        use_proxy: bool = False,
        convert: bool = False,
        headers: dict[str, str] | None = None,
        restrict_private_network: bool = False,
        allowed_local_roots: Sequence[Path] | None = None,
        local_base_dir: Path | None = None,
    ) -> ImageResource | None:
        """读取本地或远程单图并返回 ImageResource。"""
        source_ref: str | Path = url

        # 如果是本地文件路径字符串，统一转换为 Path 对象
        if isinstance(url, str):
            url = url.strip()
            source_ref = url
            if not url:
                return None
            if url.startswith("base64://"):
                content = await asyncio.to_thread(
                    decode_base64_image,
                    url.removeprefix("base64://"),
                    convert,
                )
                return build_image_resource(content, source_ref)
            if url.lower().startswith("data:image/"):
                content = await asyncio.to_thread(read_data_url, url, convert)
                return build_image_resource(content, source_ref)

        if isinstance(url, str) and not url.lower().startswith(("http://", "https://")):
            if url.lower().startswith("file://"):
                # Python 3.13+
                from_uri = getattr(Path, "from_uri", None)
                try:
                    if from_uri is not None:
                        url = from_uri(url)
                    # 兼容旧版本 Python
                    else:
                        url2pathname = getattr(urllib.request, "url2pathname")
                        url = Path(url2pathname(urllib.parse.urlparse(url).path))
                except (OSError, RuntimeError, ValueError) as e:
                    logger.warning(
                        f"[BIG BANANA] 本地图片引用格式无效，已跳过：{source_ref}，错误：{e}"
                    )
                    return None
            elif "://" in url:
                logger.warning(
                    f"[BIG BANANA] 不支持的图片引用协议，已跳过：{source_ref}"
                )
                return None
            else:
                # 字符串路径，转换成 Path 对象
                url = Path(url)

        # 统一处理 Path：本地文件读取
        if isinstance(url, Path):
            try:
                if not url.is_absolute() and local_base_dir is not None:
                    url = local_base_dir / url
                if allowed_local_roots is not None:
                    resolved_path = url.resolve()
                    resolved_roots = tuple(
                        root.resolve() for root in allowed_local_roots
                    )
                    if not any(
                        resolved_path == root or root in resolved_path.parents
                        for root in resolved_roots
                    ):
                        logger.warning(
                            "[BIG BANANA] 本地图片引用超出允许目录，已跳过："
                            f"{resolved_path}"
                        )
                        return None
                    url = resolved_path
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning(
                    f"[BIG BANANA] 本地图片引用格式无效，已跳过：{source_ref}，错误：{e}"
                )
                return None
            content = await asyncio.to_thread(read_file, url, convert)
            return build_image_resource(content, source_ref)

        # 非文件系统路径，下载远程图片
        for _ in range(3):
            content, success = await self._download_image(
                url,
                use_proxy=use_proxy,
                convert=convert,
                headers=headers,
                restrict_private_network=restrict_private_network,
            )
            if content is not None:
                return build_image_resource(content, source_ref)
            if content is None and success:
                return None
        return None

    async def fetch_images(
        self,
        image_urls: Sequence[str | Path],
        *,
        use_proxy: bool = False,
        convert: bool = False,
        headers: dict[str, str] | None = None,
        restrict_private_network: bool = False,
        allowed_local_roots: Sequence[Path] | None = None,
        local_base_dir: Path | None = None,
    ) -> list[ImageResource]:
        """
        批量读取本地或远程图片并返回 ImageResource 列表（并发下载）。

        Args:
            image_urls: 图片引用列表，支持远程 URL、本地路径、file://、
                data:image/...;base64,... 和 base64://。
            use_proxy: 是否对远程 URL 下载使用 Downloader 初始化时传入的代理；
                本地路径、data URL 和 base64 不受影响。
            convert: 是否把不在允许列表内的图片格式转换为 JPEG。
            headers: 下载远程 URL 时附加的请求头；本地路径、data URL 和
                base64 不受影响。

        Returns:
            成功读取的图片资源列表，失败项会被过滤。
        """
        results = await self.fetch_images_keep_none(
            image_urls,
            use_proxy=use_proxy,
            convert=convert,
            headers=headers,
            restrict_private_network=restrict_private_network,
            allowed_local_roots=allowed_local_roots,
            local_base_dir=local_base_dir,
        )
        return [res for res in results if res is not None]

    async def fetch_images_keep_none(
        self,
        image_urls: Sequence[str | Path],
        *,
        use_proxy: bool = False,
        convert: bool = False,
        headers: dict[str, str] | None = None,
        restrict_private_network: bool = False,
        allowed_local_roots: Sequence[Path] | None = None,
        local_base_dir: Path | None = None,
    ) -> list[ImageResource | None]:
        """
        批量读取本地或远程图片并保留失败项。

        返回列表与 image_urls 一一对应，下载或解析失败的位置为 None。
        参数含义与 fetch_images 相同。
        """
        tasks = [
            self.fetch_image(
                url,
                use_proxy=use_proxy,
                convert=convert,
                headers=headers,
                restrict_private_network=restrict_private_network,
                allowed_local_roots=allowed_local_roots,
                local_base_dir=local_base_dir,
            )
            for url in image_urls
        ]
        return list(await asyncio.gather(*tasks))

    async def _download_image(
        self,
        url: str,
        *,
        use_proxy: bool = False,
        convert: bool = False,
        headers: dict[str, str] | None = None,
        restrict_private_network: bool = False,
    ) -> tuple[tuple[str, bytes] | None, bool]:
        """执行远程图片下载并返回处理结果及请求成功标记。"""
        if restrict_private_network and not await is_public_http_url(url):
            logger.warning(f"[BIG BANANA] 已拒绝访问非公网图片地址：{url}")
            return None, True
        try:
            async with self.session.get(
                url,
                headers=headers,
                timeout=ClientTimeout(connect=30, total=60),
                proxy=self.http_proxy if use_proxy else None,
                allow_redirects=not restrict_private_network,
            ) as response:
                if response.status != 200:
                    logger.warning(
                        f"[BIG BANANA] 图片下载失败，状态码: {response.status}"
                    )
                    return None, False
                content_bytes = await _read_image_response(response)
                if content_bytes is None:
                    return None, True

                content = await asyncio.to_thread(handle_image, content_bytes, convert)
                return content, True
        except ClientConnectorCertificateError:
            # 证书校验失败时回退为关闭 SSL 验证重试一次。
            try:
                async with self.session.get(
                    url,
                    headers=headers,
                    timeout=ClientTimeout(connect=30, total=60),
                    proxy=self.http_proxy if use_proxy else None,
                    ssl=False,
                    allow_redirects=not restrict_private_network,
                ) as response:
                    if response.status != 200:
                        logger.warning(
                            f"[BIG BANANA] 图片下载失败，状态码: {response.status}"
                        )
                        return None, False

                    content_bytes = await _read_image_response(response)
                    if content_bytes is None:
                        return None, True

                    content = await asyncio.to_thread(
                        handle_image, content_bytes, convert
                    )
                    return content, True
            except Exception as e:
                logger.error(f"[BIG BANANA] 下载图片失败(重试): {url}，错误信息：{e}")
                return None, False
        except asyncio.TimeoutError as e:
            logger.error(f"[BIG BANANA] 网络请求超时: {url}，错误信息：{e}")
            return None, False
        except Exception as e:
            logger.error(f"[BIG BANANA] 下载图片失败: {url}，错误信息：{e}")
            return None, False


async def is_public_http_url(url: str) -> bool:
    """Check whether an HTTP URL resolves exclusively to public addresses.

    Args:
        url: Remote image URL to validate.

    Returns:
        True when the URL uses HTTP(S) and all resolved addresses are public.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address_infos = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
        addresses: set[str] = set()
        for info in address_infos:
            address = info[4][0]
            if isinstance(address, str) and address:
                addresses.add(address.split("%", 1)[0])
        return bool(addresses) and all(
            ipaddress.ip_address(address).is_global for address in addresses
        )
    except (OSError, ValueError):
        return False


def handle_image(
    image_bytes: bytes, convert: bool = False, allow_gif: bool = True
) -> tuple[str, bytes] | None:
    """把图片字节标准化。不在允许格式内且 convert=True 时转换为 JPEG。"""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            fmt = (img.format or "").lower()
            if not fmt:
                # 猜不到格式,无法处理
                return None
            mime_map = _GIF_MIME_MAP if allow_gif else _MIME_MAP
            if not convert or fmt in mime_map:
                # 优先使用 mime_map 中的类型映射，没有再使用 fmt
                mime_type = mime_map.get(fmt, f"image/{fmt}")
                return mime_type, image_bytes
            else:
                # 处理多帧图片，取第一帧
                if getattr(img, "is_animated", False):
                    img.seek(0)
                # 转换成jpeg格式图片并处理透明背景（转为白色）
                if img.mode in ("RGBA", "LA") or (
                    img.mode == "P" and "transparency" in img.info
                ):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img, mask=img.convert("RGBA").split()[3])
                    img = background
                else:
                    img = img.convert("RGB")
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=95, optimize=True)
                return ("image/jpeg", buf.getvalue())
    except Exception as e:
        logger.warning(f"[BIG BANANA] 图片处理失败: {e}")
        return None


def build_image_resource(
    content: tuple[str, bytes] | None,
    url: str | Path,
) -> ImageResource | None:
    """把内部 mime/bytes 结果包装成图片资源。"""
    if content is None:
        return None
    mime, data_bytes = content
    return ImageResource(mime=mime, data_bytes=data_bytes, url=url)


def decode_base64_image(
    data_base64: str, convert: bool = False, allow_gif: bool = True
) -> tuple[str, bytes] | None:
    """Decode a base64 image and return mime/bytes."""
    normalized = "".join(data_base64.split())
    if not normalized:
        return None

    padding = -len(normalized) % 4
    if padding:
        normalized = f"{normalized}{'=' * padding}"

    try:
        image_bytes = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError) as e:
        logger.warning(f"[BIG BANANA] Image base64 decode failed: {e}")
        return None
    return handle_image(image_bytes, convert, allow_gif)


def read_data_url(
    data_url: str, convert: bool = False, allow_gif: bool = True
) -> tuple[str, bytes] | None:
    """Read a data:image/...;base64,... image."""
    header, sep, payload = data_url.partition(",")
    if not sep:
        return None
    if not header.lower().startswith("data:image/"):
        return None

    _, _, metadata = header.removeprefix("data:").partition(";")
    if "base64" not in metadata.lower().split(";"):
        logger.warning("[BIG BANANA] Non-base64 data URL image is not supported")
        return None
    return decode_base64_image(urllib.parse.unquote(payload), convert, allow_gif)


def read_file(
    path: str | Path, convert: bool = False, allow_gif: bool = True
) -> tuple[str, bytes] | None:
    """读取本地文件并返回 mime/bytes。"""
    try:
        file_data = Path(path).read_bytes()
        return handle_image(file_data, convert, allow_gif)
    except Exception as e:
        logger.error(f"[BIG BANANA] 读取文件 {path} 失败: {e}")
        return None
