"""Capturas de todas las vistas (revisión visual / regresión, T5.3).

Uso (con la app corriendo en modo --headless):
    uv run --with playwright python scripts/screenshots.py [carpeta] [--dark]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = [
    ("inicio", "/"),
    ("clases", "/clases"),
    ("chat", "/chat?q=¿Qué tareas tengo?"),
    ("cursos", "/cursos"),
    ("ajustes-audio", "/configuracion?tab=audio"),
    ("ajustes-ia", "/configuracion?tab=ia"),
    ("ajustes-google", "/configuracion?tab=google"),
    ("ajustes-universidad", "/configuracion?tab=universidad"),
    ("ajustes-transcripcion", "/configuracion?tab=transcripcion"),
    ("bienvenida", "/bienvenida"),
]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0] if args else "build/capturas")
    dark = "--dark" in sys.argv
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1280, "height": 820}, color_scheme="dark" if dark else "light"
        )
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name, path in PAGES:
            page.goto(f"http://127.0.0.1:8765{path}")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(out / f"{name}{'-oscuro' if dark else ''}.png"))
        # Detalle de la primera clase
        page.goto("http://127.0.0.1:8765/clases")
        page.wait_for_timeout(800)
        link = page.locator("a[href^='/clase/']").first
        if link.count():
            link.click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(out / f"clase{'-oscuro' if dark else ''}.png"), full_page=True)
        # Hoja «Nueva grabación»
        page.goto("http://127.0.0.1:8765/")
        page.wait_for_timeout(800)
        page.get_by_text("Grabar ahora").click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / f"nueva-grabacion{'-oscuro' if dark else ''}.png"))
        browser.close()
    print("Errores JS:", errors or "ninguno")


if __name__ == "__main__":
    main()
