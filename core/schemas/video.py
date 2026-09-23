from dataclasses import dataclass
from pathlib import Path


@dataclass(repr=False, slots=True)
class VideoResource:
    """A generated video."""

    url: str
    local_path: Path | None = None
    download_enabled: bool = False
