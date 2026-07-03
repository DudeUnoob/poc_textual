"""
Enhance census scan images before sending to vision LLM.
Goal: improve handwriting legibility, reduce token waste on margins.
"""
import base64

import cv2
import numpy as np


def enhance_census_image(image_path: str, output_path: str | None = None) -> np.ndarray:
    """Deskew, denoise, and enhance contrast on a census scan."""
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    img = _deskew(img)
    img = cv2.fastNlMeansDenoising(img, h=10)
    img = cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, blockSize=15, C=8,
    )
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)
    if output_path:
        cv2.imwrite(output_path, img)
    return img


def _deskew(img: np.ndarray) -> np.ndarray:
    coords = np.column_stack(np.where(img < 128))
    if len(coords) == 0:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:
        return img
    h, w = img.shape
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def image_to_base64(image_path: str) -> tuple[str, str]:
    enhanced = enhance_census_image(image_path)
    _, buffer = cv2.imencode(".jpg", enhanced, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return base64.standard_b64encode(buffer).decode("utf-8"), "image/jpeg"
