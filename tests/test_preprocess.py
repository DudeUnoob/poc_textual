import cv2
import numpy as np
import pytest

import preprocess


@pytest.fixture
def sample_image(tmp_path):
    # A tall synthetic "form": 1200x800, white with black bands.
    img = np.full((1200, 800, 3), 255, dtype=np.uint8)
    for y in range(0, 1200, 40):
        cv2.line(img, (0, y), (800, y), (0, 0, 0), 2)
    path = tmp_path / "page.jpg"
    cv2.imwrite(str(path), img)
    return str(path)


def test_full_page_returns_jpeg_bytes(sample_image):
    data, mime = preprocess.prepare_full_page(sample_image)
    assert mime == "image/jpeg"
    assert data[:2] == b"\xff\xd8"  # JPEG SOI marker


def test_row_block_crops_cover_page_with_overlap(sample_image):
    crops = preprocess.make_row_block_crops(sample_image, n_blocks=3, overlap_frac=0.1)
    assert len(crops) == 3
    assert [c.label for c in crops] == ["crop_1", "crop_2", "crop_3"]
    # Bands ascend and overlap (each start is before the previous end).
    for prev, nxt in zip(crops, crops[1:]):
        assert nxt.y_start < prev.y_end
    # Full vertical coverage.
    assert crops[0].y_start == 0
    assert crops[-1].y_end >= 1200 - 1


def test_targeted_crop_within_bounds(sample_image):
    crop = preprocess.make_targeted_crop(sample_image, 0.6, 0.8, label="retry")
    assert crop.label == "retry"
    assert 0 <= crop.y_start < crop.y_end <= 1200
    assert crop.image_bytes[:2] == b"\xff\xd8"


def test_missing_image_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        preprocess.prepare_full_page(str(tmp_path / "nope.jpg"))


def _measure_skew_angle(img: np.ndarray) -> float:
    """Test oracle: same technique preprocess._deskew uses to estimate skew."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    coords = np.column_stack(np.where(gray < 128))
    if len(coords) == 0:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1] % 90
    if angle > 45:
        angle -= 90
    return angle


@pytest.fixture
def rotated_image(tmp_path):
    img = np.full((1200, 800, 3), 255, dtype=np.uint8)
    for y in range(0, 1200, 40):
        cv2.line(img, (0, y), (800, y), (0, 0, 0), 2)
    matrix = cv2.getRotationMatrix2D((400, 600), 3.0, 1.0)
    rotated = cv2.warpAffine(
        img, matrix, (800, 1200), borderValue=(255, 255, 255)
    )
    path = tmp_path / "rotated.jpg"
    cv2.imwrite(str(path), rotated)
    return str(path)


def test_deskew_straightens_rotated_page(rotated_image):
    img = preprocess._read_color(rotated_image)
    before_angle = _measure_skew_angle(img)
    corrected = preprocess._deskew(img)
    after_angle = _measure_skew_angle(corrected)
    assert abs(before_angle) > 1.0  # fixture is genuinely rotated
    assert abs(after_angle) < abs(before_angle)


def test_deskew_noop_on_straight_page(sample_image):
    img = preprocess._read_color(sample_image)
    corrected = preprocess._deskew(img)
    assert np.array_equal(img, corrected)
