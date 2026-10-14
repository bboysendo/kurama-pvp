"""Detecta el marco real de cada carta por contorno (no por alpha) y la centra en un lienzo 450x550 transparente."""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

CARDS_DIR = Path(__file__).resolve().parent.parent / "CARDS"
CANVAS_SIZE = (450, 550)
MARGIN = 6


def detect_frame_bbox(bgr: np.ndarray) -> tuple[int, int, int, int]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0, 0, bgr.shape[1], bgr.shape[0]
    x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
    x = max(0, x - MARGIN)
    y = max(0, y - MARGIN)
    w = min(bgr.shape[1] - x, w + 2 * MARGIN)
    h = min(bgr.shape[0] - y, h + 2 * MARGIN)
    return x, y, w, h


def process_card(path: Path) -> None:
    img = Image.open(path).convert("RGBA")
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR)
    x, y, w, h = detect_frame_bbox(bgr)
    img = img.crop((x, y, x + w, y + h))

    scale = min(CANVAS_SIZE[0] / img.width, CANVAS_SIZE[1] / img.height)
    new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    img = img.resize(new_size, Image.LANCZOS)

    canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    offset = ((CANVAS_SIZE[0] - img.width) // 2, (CANVAS_SIZE[1] - img.height) // 2)
    canvas.paste(img, offset, img)

    if path.suffix.lower() in (".jpg", ".jpeg"):
        canvas.convert("RGB").save(path)
    else:
        canvas.save(path)


def main() -> None:
    files = sorted(
        p for p in CARDS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")
    )
    for path in files:
        process_card(path)
        print(f"ok: {path.name}")

    assert all(Image.open(p).size == CANVAS_SIZE for p in files), "alguna imagen no quedo en 450x550"
    print(f"{len(files)} imagenes unificadas a {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}")


if __name__ == "__main__":
    main()
