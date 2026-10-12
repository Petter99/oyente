"""Prepara un entorno de desarrollo con datos de ejemplo para revisar la interfaz.

Uso:  TRANSCRIPTOR_HOME=build/uihome uv run python scripts/dev_seed.py
Crea la configuración (asistente completado), cursos y dos clases de ejemplo sin grabar audio real.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import FakeLLM  # noqa: E402
from tests.integration.test_pipeline import SCRIPT, _class_audio  # noqa: E402

from transcriptor.asr.fake import FakeTranscriber  # noqa: E402
from transcriptor.audio.sources import FileAudioSource  # noqa: E402
from transcriptor.audio.vad import EnergyDetector  # noqa: E402
from transcriptor.llm.composite import BudgetedProvider  # noqa: E402
from transcriptor.services import AppServices  # noqa: E402
from transcriptor.speakers.embedder import SpectralEmbedder  # noqa: E402


def main() -> None:
    if "TRANSCRIPTOR_HOME" not in os.environ:
        raise SystemExit("Define TRANSCRIPTOR_HOME para no tocar tus datos reales.")
    home = Path(os.environ["TRANSCRIPTOR_HOME"])
    svc = AppServices()
    svc._speaker_embedder = SpectralEmbedder()
    svc.cfg.general.vault_path = str(home / "Bóveda")
    Path(svc.cfg.general.vault_path).mkdir(parents=True, exist_ok=True)
    svc.cfg.ui.onboarding_done = True
    svc.cfg.transcription.model = "tiny"  # modelo pequeño para revisar la interfaz rápido
    if not svc.cfg.courses:
        svc.add_course("Cálculo II", "Ing. Pérez", ["LIATE"])
        svc.add_course("Física I", "Dra. Ruiz")
    svc.save_config()
    for i, (course, llm) in enumerate(
        ((svc.cfg.courses[0], False), (svc.cfg.courses[0], True), (svc.cfg.courses[1], True))
    ):
        if llm:
            svc._provider = BudgetedProvider(FakeLLM(), svc.ledger, None)
        else:
            svc._provider = None
        clase, mic = _class_audio()
        session = svc.start_recording(
            course.id,
            "app",
            sources=[
                FileAudioSource(samples=clase, channel="clase"),
                FileAudioSource(samples=mic, channel="mic"),
            ],
            transcriber=FakeTranscriber(SCRIPT),
            detector=EnergyDetector,
        )
        session.started_at = datetime(2026, 10, 5 + i * 3, 18, 0)
        for state in svc.live.recorder._channels.values():  # type: ignore[union-attr]
            state.source.finished.wait(10)  # type: ignore[attr-defined]
        svc.stop_recording(background=False, final_transcriber=FakeTranscriber(SCRIPT))
    print(f"Listo: {home}")


if __name__ == "__main__":
    main()
