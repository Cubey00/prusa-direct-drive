"""Draws the path programmer's logo: assets/icon.png (512 px) and assets/icon.ico (16-256 px).

A sensor head (with its camera lens) touching a printed part on the bed, on a blue tile.
Run: python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent / "assets"
S = 1024                      # drawn large, scaled down for smooth edges


def gradient(size, top, bottom):
    img = Image.new("RGB", (size, size), top)
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / (size - 1)
        d.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def draw():
    tile = gradient(S, (59, 130, 246), (30, 58, 138))          # blue-500 -> blue-900
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(tile, (0, 0), mask)

    # soft light in the top-left corner
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse([-S * 0.3, -S * 0.45, S * 0.9, S * 0.5], fill=60)
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.08))
    light = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    light.putalpha(Image.composite(glow, Image.new("L", (S, S), 0), mask))
    img = Image.alpha_composite(img, light)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = S // 2
    # bed and a printed part
    d.rounded_rectangle([S * 0.16, S * 0.78, S * 0.84, S * 0.83], radius=S * 0.02, fill=(191, 219, 254, 255))
    d.rounded_rectangle([S * 0.34, S * 0.64, S * 0.66, S * 0.78], radius=S * 0.025, fill=(250, 204, 21, 255))
    # sensor head: body, neck and the dome that touches the part
    d.rounded_rectangle([S * 0.30, S * 0.16, S * 0.70, S * 0.40], radius=S * 0.06, fill=white)
    d.polygon([(S * 0.36, S * 0.40), (S * 0.64, S * 0.40), (S * 0.56, S * 0.57), (S * 0.44, S * 0.57)], fill=white)
    d.pieslice([S * 0.42, S * 0.46, S * 0.58, S * 0.66], 0, 180, fill=white)
    # camera lens in the head
    d.ellipse([cx - S * 0.085, S * 0.195, cx + S * 0.085, S * 0.365], fill=(30, 58, 138, 255))
    d.ellipse([cx - S * 0.045, S * 0.235, cx + S * 0.045, S * 0.325], fill=(96, 165, 250, 255))
    d.ellipse([cx - S * 0.03, S * 0.245, cx - S * 0.005, S * 0.27], fill=white)
    # contact: small green waves where the dome meets the part
    green = (34, 197, 94, 255)
    for r in (0.12, 0.18):
        box = [cx - S * r, S * 0.62 - S * r * 0.6, cx + S * r, S * 0.62 + S * r * 0.6]
        for a0, a1 in ((150, 200), (340, 30)):
            d.arc(box, a0, a1, fill=green, width=int(S * 0.022))
    return img


def main():
    ASSETS.mkdir(exist_ok=True)
    img = draw()
    img.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon.png")
    img.resize((256, 256), Image.LANCZOS).save(
        ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {ASSETS / 'icon.png'} and {ASSETS / 'icon.ico'}")


if __name__ == "__main__":
    main()
