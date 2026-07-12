from dataclasses import dataclass, field

from .image import ImageResource
from .video import VideoResource


@dataclass(repr=False, slots=True)
class GenerationResult:
    """统一的图片或视频生成结果。"""

    images: list[ImageResource] = field(default_factory=list)
    """ 生成的图片列表 """
    videos: list[VideoResource] = field(default_factory=list)
    """ 生成的视频列表 """
    urls: list[str] = field(default_factory=list)
    """ 上传到图床的 URL 列表 """
    error_message: str | None = field(default=None, init=True)
    """ 错误消息 """
