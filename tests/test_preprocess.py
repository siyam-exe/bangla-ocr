from PIL import Image, ImageDraw

from bangla_ocr.preprocess import preprocess_page


def _config() -> dict:
    return {
        "enabled": True,
        "maximum_deskew_degrees": 3.0,
        "minimum_deskew_degrees": 0.18,
        "crop_padding_ratio": 0.035,
        "minimum_crop_savings_ratio": 0.04,
        "maximum_crop_per_edge_ratio": 0.12,
        "minimum_contrast_stddev": 36.0,
    }


def _page_with_blank_border() -> Image.Image:
    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    for y in range(120, 1280, 30):
        draw.rectangle((100, y, 900, y + 8), fill="black")
    return image


def test_blank_border_crop_is_disabled_when_not_explicitly_enabled():
    image = _page_with_blank_border()

    result = preprocess_page(image, _config())

    assert result.selected_name == "original"
    assert result.selected.size == image.size


def test_blank_border_crop_can_still_be_enabled_explicitly():
    image = _page_with_blank_border()
    config = _config()
    config["crop_blank_borders"] = True

    result = preprocess_page(image, config)

    assert result.selected_name == "conservative_crop"
    assert result.selected.size[0] < image.size[0]
    assert result.selected.size[1] < image.size[1]
