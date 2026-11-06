"""Draws the app's icon: stacked pages, in the brand's indigo."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

INDIGO = "#4F46E5"
SIZE = 1024


def main(out: Path) -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((64, 64, SIZE - 64, SIZE - 64), radius=200, fill=INDIGO)
    for i, x in enumerate((270, 410, 550)):
        top = 260 + i * 20
        draw.rounded_rectangle((x, top, x + 100, 764), radius=24, fill="white")
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
