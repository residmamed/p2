import io

from PIL import Image

from app.crop import crop_and_encode


def _make_test_image(width=200, height=100) -> bytes:
    img = Image.new("RGB", (width, height), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_crop_and_encode_returns_valid_jpeg_of_expected_size():
    image_bytes = _make_test_image(200, 100)
    result = crop_and_encode(image_bytes, box=(50, 20, 150, 80), pad=False)

    cropped = Image.open(io.BytesIO(result))
    assert cropped.format == "JPEG"
    assert cropped.size == (100, 60)


def test_crop_and_encode_pads_box_by_default():
    image_bytes = _make_test_image(200, 100)
    unpadded = Image.open(io.BytesIO(crop_and_encode(image_bytes, box=(50, 20, 150, 80), pad=False)))
    padded = Image.open(io.BytesIO(crop_and_encode(image_bytes, box=(50, 20, 150, 80), pad=True)))

    assert padded.size[0] > unpadded.size[0]
    assert padded.size[1] > unpadded.size[1]


def test_crop_and_encode_clamps_box_to_image_bounds():
    image_bytes = _make_test_image(200, 100)
    # Box extends far past the image edges — should clamp, not raise.
    result = crop_and_encode(image_bytes, box=(-50, -50, 500, 500), pad=True)

    cropped = Image.open(io.BytesIO(result))
    assert cropped.size[0] <= 200
    assert cropped.size[1] <= 100


def test_crop_and_encode_downscales_large_crops_to_max_dimension():
    image_bytes = _make_test_image(1000, 1000)
    result = crop_and_encode(image_bytes, box=(0, 0, 1000, 1000), pad=False)

    cropped = Image.open(io.BytesIO(result))
    assert max(cropped.size) <= 500
