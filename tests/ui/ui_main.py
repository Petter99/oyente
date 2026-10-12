"""App de prueba para la simulación de usuario de NiceGUI: datos de ejemplo, sin hardware ni red."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from nicegui import ui
from tests.integration.test_pipeline import SCRIPT, _class_audio

from transcriptor.app import build
from transcriptor.asr.fake import FakeTranscriber
from transcriptor.audio.sources import FileAudioSource
from transcriptor.audio.vad import EnergyDetector
from transcriptor.services import AppServices
from transcriptor.speakers.embedder import SpectralEmbedder

home = Path(os.environ["TRANSCRIPTOR_HOME"])
svc = AppServices()
svc._speaker_embedder = SpectralEmbedder()
svc.warm_up_models = lambda: None  # type: ignore[method-assign]  # sin descargas reales en pruebas
svc.models_status = "listo"
svc.cfg.general.vault_path = str(home / "Bóveda")
Path(svc.cfg.general.vault_path).mkdir(parents=True, exist_ok=True)
if os.environ.get("UI_TEST_ONBOARDED", "1") == "1":
    svc.cfg.ui.onboarding_done = True
    course = svc.add_course("Cálculo II", "Ing. Pérez", ["LIATE"])
    clase, mic = _class_audio()
    svc.start_recording(
        course.id,
        "app",
        sources=[
            FileAudioSource(samples=clase, channel="clase"),
            FileAudioSource(samples=mic, channel="mic"),
        ],
        transcriber=FakeTranscriber(SCRIPT),
        detector=EnergyDetector,
    )
    for state in svc.live.recorder._channels.values():  # type: ignore[union-attr]
        state.source.finished.wait(10)  # type: ignore[attr-defined]
    svc.stop_recording(background=False, final_transcriber=FakeTranscriber(SCRIPT))
svc.save_config()
UI_SERVICES = svc
build(svc, 8765, secrets.token_urlsafe(8))
ui.run(reload=False)
