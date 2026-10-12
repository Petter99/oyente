"""Genera una bóveda de demostración con clases sintéticas para revisar el resultado en Obsidian.

Uso:  uv run python scripts/demo_vault.py <carpeta-destino>
No usa micrófono, modelos ni internet (transcriptor y voces simulados).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import FakeLLM  # noqa: E402
from tests.integration.test_pipeline import _deps, record_class  # noqa: E402
from transcriptor.config.models import Course  # noqa: E402
from transcriptor.llm.composite import NullLLMProvider  # noqa: E402


def main() -> None:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "boveda-demo").resolve()
    vault = dest / "Bóveda"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "Mis apuntes").mkdir(exist_ok=True)
    (vault / "Mis apuntes" / "Regla LIATE.md").write_text("Mis apuntes sobre LIATE.\n", encoding="utf-8")
    calc = Course(name="Cálculo II", teacher="Ing. Pérez", glossary=["LIATE"])
    fis = Course(name="Física I", teacher="Dra. Ruiz")
    local = _deps(dest / "datos", vault, NullLLMProvider())
    ia = _deps(dest / "datos", vault, FakeLLM())
    record_class(local, calc, datetime(2026, 10, 5, 18, 0))
    record_class(ia, calc, datetime(2026, 10, 12, 18, 0))
    record_class(ia, fis, datetime(2026, 10, 8, 9, 0))
    print(f"Bóveda de demostración en: {vault}")


if __name__ == "__main__":
    main()
