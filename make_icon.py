#!/usr/bin/env python3
"""Draw Classifier's icon: a magnifying glass looking at 0s and 1s.

Writes icons/classifier.png (512) and icons/classifier-{256,128,64,48}.png.
Needs Pillow.  Uses DejaVu Sans Mono Bold (free) for the digits.
"""

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
S = 512
OUT = HERE / "icons"


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # background tile
    d.rounded_rectangle((16, 16, S - 16, S - 16), radius=88, fill=(24, 38, 56, 255))
    # rows of binary digits
    rng = random.Random(7)
    small = font(46)
    rows = []
    for r in range(8):
        rows.append("".join(rng.choice("01") for _ in range(9)))
    for r, row in enumerate(rows):
        y = 44 + r * 54
        d.text((48, y), "  ".join(row)[:17], font=small, fill=(70, 110, 140, 255))

    # magnified area: brighter, bigger digits inside the lens
    cx, cy, rad = 208, 206, 132
    lens = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lens)
    ld.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(222, 240, 250, 255))
    big = font(92)
    for k, (dx, dy, ch) in enumerate([(-96, -112, "1"), (-10, -112, "0"), (76, -112, "1"),
                                      (-96, -36, "0"), (-10, -36, "1"), (76, -36, "1"),
                                      (-96, 40, "1"), (-10, 40, "0"), (76, 40, "0")]):
        col = (46, 125, 50, 255) if ch == "1" else (25, 70, 120, 255)
        ld.text((cx + dx - 4, cy + dy + 6), ch, font=big, fill=col)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=255)
    img.paste(lens, (0, 0), mask)

    # handle (drawn as a thick rotated line with round ends)
    hx0, hy0 = cx + int(rad * 0.72), cy + int(rad * 0.72)
    hx1, hy1 = 452, 452
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.line((hx0 + 8, hy0 + 10, hx1 + 8, hy1 + 10), fill=(0, 0, 0, 120), width=58)
    sd.ellipse((cx - rad - 22 + 8, cy - rad - 22 + 10, cx + rad + 22 + 8, cy + rad + 22 + 10),
               outline=(0, 0, 0, 110), width=30)
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(8)))
    d = ImageDraw.Draw(img)
    d.line((hx0, hy0, hx1, hy1), fill=(120, 72, 36, 255), width=56)
    for (x, y) in ((hx1, hy1),):
        d.ellipse((x - 28, y - 28, x + 28, y + 28), fill=(120, 72, 36, 255))
    d.line((hx0 + 6, hy0 + 2, hx1 - 10, hy1 - 14), fill=(160, 104, 60, 255), width=14)
    # rim
    d.ellipse((cx - rad - 22, cy - rad - 22, cx + rad + 22, cy + rad + 22),
              outline=(196, 160, 72, 255), width=30)
    d.ellipse((cx - rad - 10, cy - rad - 10, cx + rad + 10, cy + rad + 10),
              outline=(236, 206, 120, 255), width=6)
    # glint
    d.arc((cx - rad + 24, cy - rad + 24, cx + rad - 24, cy + rad - 24), 200, 250,
          fill=(255, 255, 255, 200), width=12)
    return img


def main():
    OUT.mkdir(exist_ok=True)
    img = draw()
    img.save(OUT / "classifier.png")
    for size in (256, 128, 64, 48):
        img.resize((size, size), Image.LANCZOS).save(OUT / f"classifier-{size}.png")
    print(f"Wrote {OUT}/classifier.png and smaller sizes")


if __name__ == "__main__":
    main()
