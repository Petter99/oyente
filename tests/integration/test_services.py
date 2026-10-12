"""Servicios de la app de punta a punta, sin hardware ni red (T7.1, T13.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes import FakeLLM
from tests.integration.test_pipeline import SCRIPT, _class_audio

from transcriptor.asr.fake import FakeTranscriber
from transcriptor.audio.sources import FileAudioSource
from transcriptor.audio.vad import EnergyDetector
from transcriptor.config.store import ConfigStore
from transcriptor.services import AppServices
from transcriptor.speakers.embedder import SpectralEmbedder


@pytest.fixture
def services(tmp_path: Path, vault: Path) -> AppServices:
    svc = AppServices(ConfigStore(tmp_path / "config.yaml"))
    svc.cfg.general.vault_path = str(vault)
    svc._speaker_embedder = SpectralEmbedder()  # sin descargar modelos en las pruebas rápidas
    svc.add_course("Cálculo II", "Ing. Pérez", ["LIATE"])
    return svc


def _record(svc: AppServices) -> str:
    clase, mic = _class_audio()
    course = svc.cfg.courses[0]
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
    assert svc.recording
    for state in svc.live.recorder._channels.values():  # type: ignore[union-attr]
        state.source.finished.wait(10)  # type: ignore[attr-defined]
    sid = svc.stop_recording(background=False, final_transcriber=FakeTranscriber(SCRIPT))
    assert sid and not svc.recording
    return sid


def test_record_finalize_and_chat_without_api_key(services: AppServices, vault: Path) -> None:
    sid = _record(services)
    state = services.finalizing[sid]
    assert state.done and state.error is None
    session = services.store.get(sid)
    assert session and session.status == "done" and (vault / session.note_path).exists()
    # Chat: tareas sin LLM; preguntas abiertas en modo búsqueda.
    ans = services.ask("¿Qué tareas tengo?")
    assert "hoja de ejercicios" in ans.text.lower() and not ans.used_llm
    ans2 = services.ask("¿Qué dijo de la regla LIATE?")
    assert ans2.text.startswith("Modo búsqueda")
    assert services.pending_llm_sessions()


def test_process_pending_after_adding_key(services: AppServices, vault: Path) -> None:
    sid = _record(services)
    from transcriptor.llm.composite import BudgetedProvider

    services._provider = BudgetedProvider(FakeLLM(), services.ledger, None)
    assert services.process_pending() == 1
    session = services.store.get(sid)
    assert session and session.llm_processed and session.summary and session.summary.title
    assert services.tokens_this_month() > 0


def test_task_done_updates_note_and_index(services: AppServices, vault: Path) -> None:
    sid = _record(services)
    session = services.store.get(sid)
    assert session and session.summary and session.summary.tasks
    task = session.summary.tasks[0]
    services.set_task_done(sid, task.id, True)
    text = (vault / session.note_path).read_text(encoding="utf-8")
    assert f"- [x] {task.description}" in text
    assert not any(t["id"] == task.id for t in services.tasks_pending())


def test_storage_estimate_follows_settings(services: AppServices) -> None:
    services.cfg.audio.format = "flac"
    flac = services.storage_estimate()["per_hour_mb"]
    services.cfg.audio.format = "opus"
    opus = services.storage_estimate()["per_hour_mb"]
    assert flac == 120 and opus == 22
    services.cfg.audio.retention.mode = "never"
    assert services.storage_estimate()["total_mb"] == 0


def test_provider_test_restores_previous_key_on_failure(services: AppServices) -> None:
    from transcriptor.config.secrets import Secret

    services.secrets.set(Secret.GEMINI_API_KEY, "AIzaClaveAnteriorValida1234567890")
    ok, msg = services.test_provider("ollama", None)  # sin Ollama en ejecución
    assert not ok
    assert services.secrets.get(Secret.GEMINI_API_KEY) == "AIzaClaveAnteriorValida1234567890"


def test_recover_interrupted_session(services: AppServices, vault: Path, tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    from transcriptor.session.models import RecordingSession

    course = services.cfg.courses[0]
    s = RecordingSession(course_id=course.id, course_name=course.name, status="recording")
    work = services.store.dir(s.id)
    s.work_dir = str(work)
    clase, _ = _class_audio()
    sf.write(str(work / "clase.flac"), clase, 16000, format="FLAC")
    services.store.save(s)
    services._transcribers = type(
        "P", (), {"final": FakeTranscriber(SCRIPT), "live": FakeTranscriber()}
    )()
    assert services.recover_interrupted() == [s.id]
    recovered = services.store.get(s.id)
    assert recovered and recovered.status == "done" and (vault / recovered.note_path).exists()
    assert np.any(clase)
