"""Recorta el marco real de cada carta en CARDS/ y la centra en un lienzo 450x550 transparente."""
from pathlib import Path
from PIL import Image

CARDS_DIR = Path(__file__).resolve().parent.parent / "CARDS"
CANVAS_SIZE = (450, 550)


def process_card(path: Path) -> None:
    img = Image.open(path).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

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
