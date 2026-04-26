"""
Generates assets/icon.png and assets/icon.ico for TranslateX.
Run once before building: python assets/create_icon.py
Requires Pillow (already in requirements.txt).
"""
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT_DIR = Path(__file__).parent


def draw_icon(size: int) -> Image.Image:
    """Draw the TranslateX icon at the given pixel size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # --- Background: rounded rectangle, dark navy ---
    bg_color = (26, 26, 46, 255)        # #1a1a2e
    accent   = (79, 195, 247, 255)      # #4fc3f7 (light blue)
    white    = (255, 255, 255, 255)

    radius = max(2, size // 6)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=bg_color)

    # --- Subtle diagonal accent stripe ---
    stripe_w = max(1, size // 16)
    for offset in range(-size, size * 2, max(4, size // 8)):
        x0, y0 = offset, 0
        x1, y1 = offset + size // 3, size
        draw.line([(x0, y0), (x1, y1)], fill=(79, 195, 247, 18), width=stripe_w)

    # --- "TX" text ---
    font_size = max(6, int(size * 0.44))
    font = None
    # Try to load a system font; fall back to default
    for font_name in [
        "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold
        "C:/Windows/Fonts/arialbd.ttf",     # Arial Bold
        "C:/Windows/Fonts/calibrib.ttf",    # Calibri Bold
    ]:
        if os.path.exists(font_name):
            try:
                font = ImageFont.truetype(font_name, font_size)
                break
            except Exception:
                pass

    text = "TX"
    if font:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (size - tw) // 2 - bbox[0]
        ty = (size - th) // 2 - bbox[1]
        # Shadow
        shadow_offset = max(1, size // 32)
        draw.text((tx + shadow_offset, ty + shadow_offset), text,
                  font=font, fill=(0, 0, 0, 120))
        # Main text in white
        draw.text((tx, ty), text, font=font, fill=white)
    else:
        # Fallback: simple centered text with default font
        draw.text((size // 4, size // 4), text, fill=white)

    # --- Bottom accent bar ---
    bar_h = max(2, size // 16)
    margin = max(2, size // 8)
    draw.rounded_rectangle(
        [margin, size - bar_h - margin // 2, size - margin, size - margin // 2],
        radius=bar_h // 2,
        fill=accent,
    )

    return img


def create_png(path: Path) -> None:
    img = draw_icon(256)
    img.save(str(path), "PNG")
    print(f"Created: {path}")


def create_ico(path: Path) -> None:
    frames = [draw_icon(s).convert("RGBA") for s in SIZES]
    # ICO requires the largest first
    frames[0].save(
        str(path),
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=frames[1:],
    )
    print(f"Created: {path}")


if __name__ == "__main__":
    create_png(OUT_DIR / "icon.png")
    create_ico(OUT_DIR / "icon.ico")
    print("Done.")
