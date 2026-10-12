"""Íconos provisionales de la app (el definitivo queda pendiente en la fase D4).

Uso:  uv run python scripts/make_icons.py
Genera assets/branding/: icon.png (1024), icon.ico (Windows), icon.icns (macOS), tray.png.
Para cambiar el ícono basta con reemplazar esos archivos.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "branding"
RED = (217, 58, 43)
RED_DARK = (176, 38, 28)


def _waves(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, unit: float, color: tuple, width: int
) -> None:
    draw.ellipse([cx - unit * 0.9, cy - unit * 0.9, cx + unit * 0.9, cy + unit * 0.9], fill=color)
    for i, r in enumerate((2.2, 3.5)):
        box = [cx - unit * r, cy - unit * r, cx + unit * r, cy + unit * r]
        draw.arc(box, start=-50, end=50, fill=color, width=width - i * max(1, width // 6))
        draw.arc(box, start=130, end=230, fill=color, width=width - i * max(1, width // 6))


def app_icon(size: int = 1024) -> Image.Image:
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(s * 0.09)
    radius = int(s * 0.22)
    for i in range(s - 2 * pad):  # degradado vertical
        t = i / (s - 2 * pad)
        color = tuple(int(RED[k] * (1 - t) + RED_DARK[k] * t) for k in range(3))
        d.line([(pad, pad + i), (s - pad, pad + i)], fill=color)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, s - pad, s - pad], radius=radius, fill=255)
    img.putalpha(mask)
    _waves(d, s / 2, s / 2, s * 0.085, (255, 255, 255, 255), int(s * 0.045))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def tray_icon(size: int = 64, color: tuple = (0, 0, 0, 255)) -> Image.Image:
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    _waves(ImageDraw.Draw(img), s / 2, s / 2, s * 0.11, color, int(s * 0.07))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def extension_icon(size: int) -> Image.Image:
    return app_icon(max(size, 16)).resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = app_icon(1024)
    icon.save(OUT / "icon.png")
    icon.save(
        OUT / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    try:
        icon.save(OUT / "icon.icns")
    except Exception as exc:  # Pillow sin soporte ICNS en este sistema
        print(f"No se generó icon.icns: {exc}")
    tray_icon(64).save(OUT / "tray.png")
    tray_icon(64, (255, 255, 255, 255)).save(OUT / "tray-light.png")
    print(f"Íconos en {OUT}")


if __name__ == "__main__":
    main()
