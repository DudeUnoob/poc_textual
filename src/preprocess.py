"""
Image preparation for the Gemini census OCR pipeline.

Two philosophies, deliberately kept separate:

1. Full-page input (default): send a *lightly* enhanced, high-resolution
   version of the ORIGINAL scan. Frontier vision models read handwriting best
   from natural grayscale/colour images -- aggressive binary thresholding
   (the old Claude/Qwen path) destroys stroke information and hurts accuracy.
   So the default model input only downscales to a sane max edge and mildly
   boosts contrast; it never binarizes.

2. Overlapping row-block crops (accuracy mode / retry): a 1950 form has ~30
   dense rows. Splitting the data area into horizontal bands with vertical
   overlap lets the model focus on fewer rows at higher effective resolution,
   which recovers lines a single full-page pass drops. Each crop keeps the
   far-left line-number margin so every band is self-locating.

All functions return bytes + mime type ready for ``google.genai`` image parts.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Gemini high-resolution tier caps the long edge around 2576px; staying under
# that keeps every pixel useful without wasting visual tokens.
DEFAULT_MAX_EDGE = 2400
JPEG_QUALITY = 95


@dataclass
class Crop:
    """One row-block crop of a page, with the pixel band it covers."""

    index: int
    image_bytes: bytes
    mime_type: str
    y_start: int
    y_end: int
    label: str  # e.g. "crop_2" -- used as the diagnostic source id


def _read_color(image_path: str) -> np.ndarray:
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    return img


def _resize_max_edge(img: np.ndarray, max_edge: int = DEFAULT_MAX_EDGE) -> np.ndarray:
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_edge:
        return img
    scale = max_edge / long_edge
    new_size = (int(round(w * scale)), int(round(h * scale)))
    return cv2.resize(img, new_size, interpolation=cv2.INTER_AREA)


def _deskew(img: np.ndarray) -> np.ndarray:
    """Straighten a rotated scan. Geometry only -- no pixel-value changes.

    Estimates the skew angle from a grayscale view of dark (ink/pencil)
    pixels via ``minAreaRect``, then rotates the ORIGINAL color image to
    correct it. Unlike binarization/denoising (deliberately not used here --
    see module docstring), this doesn't touch stroke intensity or contrast,
    so it doesn't reintroduce the accuracy regression those caused. A page
    that's already within 0.5 degrees of straight is returned unchanged.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    coords = np.column_stack(np.where(gray < 128))
    if len(coords) == 0:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    # minAreaRect's angle convention/range varies across OpenCV versions;
    # normalizing into (-45, 45] makes the correction robust to both.
    angle = angle % 90
    if angle > 45:
        angle -= 90
    if abs(angle) < 0.5:
        return img
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    # Rotate by -angle: `angle` is the shape's measured tilt, so correcting
    # it means rotating the image the opposite way.
    matrix = cv2.getRotationMatrix2D(center, -angle, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _gentle_enhance(img: np.ndarray) -> np.ndarray:
    """Mild, non-destructive contrast boost. Preserves handwriting strokes.

    Uses CLAHE on the luminance channel only -- no thresholding, no denoise
    that would smear faint pencil marks.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def _encode_jpeg(img: np.ndarray) -> tuple[bytes, str]:
    ok, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("cv2.imencode failed to encode JPEG")
    return buffer.tobytes(), "image/jpeg"


def prepare_full_page(image_path: str, max_edge: int = DEFAULT_MAX_EDGE) -> tuple[bytes, str]:
    """Return lightly-enhanced full-page bytes for the primary extraction pass."""
    img = _read_color(image_path)
    img = _resize_max_edge(img, max_edge)
    img = _deskew(img)
    img = _gentle_enhance(img)
    return _encode_jpeg(img)


def make_row_block_crops(
    image_path: str,
    n_blocks: int = 3,
    overlap_frac: float = 0.12,
    top_frac: float = 0.0,
    bottom_frac: float = 1.0,
    max_edge: int = DEFAULT_MAX_EDGE,
) -> list[Crop]:
    """Split the page vertically into ``n_blocks`` overlapping horizontal bands.

    Args:
        n_blocks: number of vertical bands (e.g. 3 -> top/middle/bottom thirds).
        overlap_frac: fraction of band height to extend into neighbours, so a
            row straddling a boundary appears whole in at least one crop.
        top_frac / bottom_frac: restrict cropping to the data area of the page
            (skip the printed header/sample-line footer) if known; defaults
            cover the whole image.
        max_edge: resize cap applied to each crop's long edge after cropping.

    Returns crops in top-to-bottom order. Each keeps full page width so the
    left-margin line numbers stay visible.
    """
    if n_blocks < 1:
        raise ValueError("n_blocks must be >= 1")

    img = _read_color(image_path)
    img = _deskew(img)
    img = _gentle_enhance(img)
    h, w = img.shape[:2]

    data_top = int(round(top_frac * h))
    data_bottom = int(round(bottom_frac * h))
    data_height = max(1, data_bottom - data_top)
    band_height = data_height / n_blocks
    overlap_px = int(round(band_height * overlap_frac))

    crops: list[Crop] = []
    for i in range(n_blocks):
        y0 = int(round(data_top + i * band_height)) - overlap_px
        y1 = int(round(data_top + (i + 1) * band_height)) + overlap_px
        y0 = max(0, y0)
        y1 = min(h, y1)
        band = img[y0:y1, 0:w]
        band = _resize_max_edge(band, max_edge)
        image_bytes, mime = _encode_jpeg(band)
        crops.append(
            Crop(index=i, image_bytes=image_bytes, mime_type=mime,
                 y_start=y0, y_end=y1, label=f"crop_{i + 1}")
        )
    return crops


def make_targeted_crop(
    image_path: str,
    line_fraction_start: float,
    line_fraction_end: float,
    pad_frac: float = 0.05,
    max_edge: int = DEFAULT_MAX_EDGE,
    label: str = "retry",
) -> Crop:
    """Crop a specific vertical fraction of the page for a focused retry.

    ``line_fraction_start``/``end`` are 0..1 positions down the page estimated
    from which line numbers are missing (e.g. lines 20-25 of 30 -> ~0.63-0.83).
    """
    img = _read_color(image_path)
    img = _deskew(img)
    img = _gentle_enhance(img)
    h, w = img.shape[:2]
    y0 = max(0, int(round((line_fraction_start - pad_frac) * h)))
    y1 = min(h, int(round((line_fraction_end + pad_frac) * h)))
    band = img[y0:y1, 0:w]
    band = _resize_max_edge(band, max_edge)
    image_bytes, mime = _encode_jpeg(band)
    return Crop(index=-1, image_bytes=image_bytes, mime_type=mime,
                y_start=y0, y_end=y1, label=label)
