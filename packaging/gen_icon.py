"""Draw the app icon (the four-bar logo on a rounded square) as icon.png, icon.icns (macOS) and icon.ico (Windows)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
BARS = [(8, 20, 8, 12), (13, 24, 13, 8), (18, 18, 18, 14), (23, 22, 23, 10)]  # x, y0, x, y1 on a 32 grid


def draw(size: int = 1024) -> Image.Image:
    k = size / 32
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=9 * k, fill=(26, 22, 48, 255))
    for x, y0, _, y1 in BARS:
        top, bottom = min(y0, y1), max(y0, y1)
        d.rounded_rectangle((x * k - 1.5 * k, top * k - 1.5 * k, x * k + 1.5 * k, bottom * k + 1.5 * k),
                            radius=1.5 * k, fill=(139, 124, 246, 255))
    return img


def main() -> None:
    img = draw()
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin":
        iconset = OUT / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for s in (16, 32, 128, 256, 512):
            img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "icon.icns")], check=True)


if __name__ == "__main__":
    main()
