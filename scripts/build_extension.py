"""Genera la extensión del navegador lista para «Cargar descomprimida» (instalación manual).

Uso:  uv run python scripts/build_extension.py [destino]   (por defecto dist/extension)
El nombre visible sale de branding.toml; el ID de la extensión es fijo (clave en el manifiesto).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from transcriptor.branding import branding  # noqa: E402
from transcriptor.capture_web.hub import EXTENSION_VERSION  # noqa: E402


def build(dest: Path) -> Path:
    src = ROOT / "browser-extension"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in ("background.js", "offscreen.html", "offscreen.js"):
        shutil.copy(src / name, dest / name)
    shutil.copy(
        ROOT / "src" / "transcriptor" / "capture_web" / "static" / "worklet.js", dest / "worklet.js"
    )
    manifest = (src / "manifest.template.json").read_text(encoding="utf-8")
    manifest = manifest.replace("{{NOMBRE}}", branding().nombre).replace(
        "{{VERSION}}", EXTENSION_VERSION
    )
    (dest / "manifest.json").write_text(manifest, encoding="utf-8")
    icons = dest / "icons"
    icons.mkdir()
    from scripts.make_icons import extension_icon

    for size in (16, 32, 48, 128):
        extension_icon(size).save(icons / f"{size}.png")
    return dest


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "extension")
    print(f"Extensión generada en {out}")
