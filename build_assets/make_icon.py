"""
Generates the app icon from scratch — a rounded square with the same
blue-to-purple gradient and checkmark as the GUI's sidebar brand mark,
so the dock icon and the in-app mark are visibly the same thing.

Run once locally (or in CI) to produce icon.png, then platform tools
turn that into icon.icns (macOS) and icon.ico (Windows) — see
build_icons.sh alongside this file.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
OUT = Path(__file__).parent / "icon.png"


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_icon() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Diagonal gradient background, masked to a rounded-square shape —
    # matches the sidebar brand mark's `linear-gradient(155deg, accent, purple)`.
    top_left = (0, 122, 255)      # macOS system blue
    bottom_right = (124, 58, 237)  # violet
    gradient = Image.new("RGB", (SIZE, SIZE))
    for y in range(SIZE):
        for_row = gradient.load()
        t_base = y / SIZE
        for x in range(0, SIZE, 4):  # stride 4: smooth enough, 4x faster
            t = (t_base + x / SIZE) / 2
            color = _lerp(top_left, bottom_right, t)
            for dx in range(4):
                if x + dx < SIZE:
                    for_row[x + dx, y] = color

    mask = Image.new("L", (SIZE, SIZE), 0)
    mdraw = ImageDraw.Draw(mask)
    margin = int(SIZE * 0.06)
    radius = int(SIZE * 0.22)
    mdraw.rounded_rectangle(
        [margin, margin, SIZE - margin, SIZE - margin], radius=radius, fill=255
    )

    img.paste(gradient, (0, 0), mask)

    # Checkmark, thick rounded strokes.
    draw = ImageDraw.Draw(img)
    stroke = int(SIZE * 0.075)
    pts = [
        (SIZE * 0.28, SIZE * 0.53),
        (SIZE * 0.44, SIZE * 0.69),
        (SIZE * 0.74, SIZE * 0.34),
    ]
    draw.line(pts, fill=(255, 255, 255, 255), width=stroke, joint="curve")
    r = stroke / 2
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 255))

    img.save(OUT)
    print(f"wrote {OUT} ({SIZE}x{SIZE})")


if __name__ == "__main__":
    make_icon()
