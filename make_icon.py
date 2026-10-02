"""
make_icon.py - Draws the app icon (static/icon.ico, static/icon-preview.png).

A rounded indigo-to-cyan tile (the UI's accent gradient) with a white invoice sheet
(folded corner, text lines) and a green check badge = "validated e-invoice".
Run:  .venv\\Scripts\\python make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "static"
S = 1024  # drawing canvas, downsampled for crispness


def gradient(size, top_left, bottom_right):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top_left, bottom_right))
    return img


def build() -> Image.Image:
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # tile
    tile = gradient(S, (99, 102, 241), (6, 182, 212)).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((20, 20, S - 20, S - 20), radius=230, fill=255)
    canvas.paste(tile, (0, 0), mask)

    # soft shadow of the sheet
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((262, 190, 742, 838), radius=46, fill=(15, 23, 42, 90))
    from PIL import ImageFilter
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))

    d = ImageDraw.Draw(canvas)
    # invoice sheet with folded top-right corner
    x0, y0, x1, y1, fold = 250, 170, 730, 820, 150
    d.polygon([(x0 + 40, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1 - 40), (x1 - 40, y1),
               (x0 + 40, y1), (x0, y1 - 40), (x0, y0 + 40)], fill=(255, 255, 255, 255))
    d.ellipse((x0, y0, x0 + 80, y0 + 80), fill=(255, 255, 255, 255))
    d.ellipse((x0, y1 - 80, x0 + 80, y1), fill=(255, 255, 255, 255))
    d.ellipse((x1 - 80, y1 - 80, x1, y1), fill=(255, 255, 255, 255))
    d.polygon([(x1 - fold, y0), (x1, y0 + fold), (x1 - fold, y0 + fold)], fill=(199, 210, 254, 255))

    # text lines
    for i, (w, col) in enumerate([(300, (99, 102, 241)), (240, (148, 163, 184)), (270, (148, 163, 184)),
                                   (200, (148, 163, 184))]):
        y = 370 + i * 80
        d.rounded_rectangle((x0 + 60, y, x0 + 60 + w, y + 34), radius=17, fill=col + (255,))

    # check badge
    cx, cy, r = 700, 760, 210
    d.ellipse((cx - r - 24, cy - r - 24, cx + r + 24, cy + r + 24), fill=(255, 255, 255, 255))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(16, 185, 129, 255))
    d.line([(cx - 105, cy + 5), (cx - 28, cy + 85), (cx + 118, cy - 85)], fill=(255, 255, 255, 255),
           width=62, joint="curve")
    for px, py in ((cx - 105, cy + 5), (cx + 118, cy - 85)):
        d.ellipse((px - 31, py - 31, px + 31, py + 31), fill=(255, 255, 255, 255))
    return canvas


if __name__ == "__main__":
    big = build()
    OUT.mkdir(exist_ok=True)
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon-preview.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(s, s) for s in sizes])
    print("written:", OUT / "icon.ico")
