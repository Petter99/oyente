"""Auditoría de licencias (lista para vender, plan-fases §6.1, T16.5).

Falla si una dependencia que se distribuye dentro de la app tiene licencia copyleft fuerte
(GPL/AGPL) sin excepción conocida. Genera THIRD_PARTY_NOTICES.md.

Uso:  uv run python scripts/check_licenses.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Herramientas de desarrollo: no se distribuyen dentro de la app.
DEV_ONLY = {
    "pytest", "pytest-asyncio", "pytest-cov", "hypothesis", "ruff", "mypy", "mypy_extensions",
    "pip-licenses", "prettytable", "types-PyYAML", "coverage", "iniconfig", "pluggy", "pathspec",
    "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "macholib", "pefile", "librt",
    "sortedcontainers",
}
# Licencias revisadas a mano: permitidas aunque el texto contenga «GPL».
REVIEWED = {
    "pyinstaller": "GPL con excepción para el *bootloader*: permite distribuir apps cerradas.",
    "certifi": "MPL-2.0: archivo de datos sin modificar.",
    "pycaw": "MIT.",
    "docutils": "Núcleo de dominio público/BSD; solo herramientas opcionales GPL que la app no usa.",
}
FORBIDDEN = ("GPL", "AGPL")
ALLOWED_GPL_LIKE = ("LGPL", "Lesser General Public", "GPL with exception", "Classpath")


def main() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--with-urls", "--with-license-file",
         "--no-license-path", "--from=mixed"],
        capture_output=True, text=True, check=True,
    ).stdout
    packages = json.loads(out)
    problems = []
    lines = ["# Avisos de licencias de terceros", "",
             "Este programa incluye los siguientes componentes de código abierto. "
             "Los modelos de voz y texto se descargan aparte con sus propias licencias "
             "(Whisper: MIT; Silero VAD: MIT; ECAPA de SpeechBrain: Apache-2.0; BGE-M3: MIT).", ""]
    for pkg in sorted(packages, key=lambda p: p["Name"].lower()):
        name, lic = pkg["Name"], pkg["License"]
        if name in DEV_ONLY or name == "transcriptor":
            continue
        is_gpl = any(f in lic for f in FORBIDDEN) and not any(a in lic for a in ALLOWED_GPL_LIKE)
        if is_gpl and name.lower() not in REVIEWED:
            problems.append(f"{name} ({lic})")
        lines.append(f"- **{name}** {pkg['Version']} — {lic}" + (f" — {pkg['URL']}" if pkg.get("URL") not in (None, "UNKNOWN") else ""))
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if problems:
        print("Licencias incompatibles con distribución cerrada:\n  " + "\n  ".join(problems))
        return 1
    print(f"OK: {len(lines) - 5} componentes revisados. THIRD_PARTY_NOTICES.md actualizado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
