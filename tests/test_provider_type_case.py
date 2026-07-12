from core.providers import BaseProvider, BaseVideoProvider


def test_image_provider_type_lookup_is_case_insensitive() -> None:
    expected = BaseProvider.get_provider_class("Gemini")

    assert expected is not None
    assert BaseProvider.get_provider_class("gemini") is expected
    assert BaseProvider.get_provider_class("  GEMINI  ") is expected


def test_video_provider_type_lookup_is_case_insensitive() -> None:
    expected = BaseVideoProvider.get_provider_class("Zhipu_Videos")

    assert expected is not None
    assert BaseVideoProvider.get_provider_class("zhipu_videos") is expected
    assert BaseVideoProvider.get_provider_class("  ZHIPU_VIDEOS  ") is expected
