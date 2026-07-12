from dataclasses import dataclass


@dataclass(repr=False, slots=True)
class VideoResource:
    """A generated video."""

    url: str
