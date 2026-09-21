from io import BytesIO

from core.drawing.saver import save_images_to_local
from core.schemas import ImageResource
from PIL import Image


def test_save_images_to_local_uses_original_format(tmp_path) -> None:
    image_buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(image_buffer, format="PNG")

    saved = save_images_to_local(
        [ImageResource("image/png", image_buffer.getvalue())], tmp_path
    )

    assert len(saved) == 1
    file_name, file_path = saved[0]
    assert file_name.endswith(".png")
    assert file_path == tmp_path / file_name
    assert file_path.exists()
