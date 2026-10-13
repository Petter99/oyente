"""Flujo completo Grabar → Detener → nota relacionada (T6.*, T7.1, guardianas 1–4)."""

from __future__ import annotations

import socket
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from tests.fakes import FakeLLM
from tests.synth import ESTUDIANTE, PROFESOR, YO, conversation, silence, speech

from transcriptor.asr.base import TranscribeOptions
from transcriptor.asr.fake import FakeTranscriber
from transcriptor.audio.sources import FileAudioSource
from transcriptor.audio.vad import EnergyDetector
from transcriptor.config.models import AppConfig, Course
from transcriptor.events import EventBus
from transcriptor.llm.composite import NullLLMProvider
from transcriptor.nlp.embed import HashingEmbedder
from transcriptor.notes.markdown import get_block, parse_frontmatter
from transcriptor.notes.writer import VaultLayout
from transcriptor.processing.postprocess import PostProcessor
from transcriptor.search.index import SearchIndex
from transcriptor.session.finalize import FinalizeDeps, Finalizer
from transcriptor.session.live import LiveRecorder
from transcriptor.session.models import RecordingSession
from transcriptor.session.store import SessionStore
from transcriptor.speakers.embedder import SpectralEmbedder
from transcriptor.speakers.profiles import SpeakerProfileStore

SCRIPT = [
    "Hoy vamos a ver integración por partes.",
    "La regla LIATE nos dice qué elegir como u.",
    "Profe, ¿eso aplica para logaritmos?",
    "Sí, la integración por partes con logaritmos es común.",
    "La tarea es la hoja de ejercicios cuatro para el próximo jueves.",
    "La regla LIATE y la integración por partes vienen en el parcial.",
]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def guard(*args: object, **kwargs: object) -> None:
        raise AssertionError("Se intentó usar la red en modo local")

    monkeypatch.setattr(socket.socket, "connect", guard)


def _class_audio() -> tuple[np.ndarray, np.ndarray]:
    clase, _ = conversation(
        [
            (PROFESOR, 4.0),
            (PROFESOR, 3.0),
            (ESTUDIANTE, 2.0),
            (PROFESOR, 3.5),
            (PROFESOR, 4.0),
            (PROFESOR, 3.0),
        ],
        pause=1.0,
    )
    mic = np.concatenate([silence(9.0), speech(YO, 2.0), silence(clase.size / 16000 - 11.0)])
    return clase.astype(np.float32), mic.astype(np.float32)


def _deps(
    tmp_path: Path, vault: Path, provider: object, cfg: AppConfig | None = None
) -> FinalizeDeps:
    cfg = cfg or AppConfig()
    cfg.general.vault_path = str(vault)
    return FinalizeDeps(
        cfg=cfg,
        store=SessionStore(tmp_path / "sesiones"),
        bus=EventBus(),
        index=SearchIndex(tmp_path / "indice.sqlite", HashingEmbedder()),
        layout=VaultLayout.from_config(cfg, vault),
        final_transcriber=FakeTranscriber(SCRIPT),
        postprocessor=PostProcessor(provider),  # type: ignore[arg-type]
        speaker_embedder=SpectralEmbedder(),
        profiles=SpeakerProfileStore(tmp_path / "perfiles"),
    )


def record_class(deps: FinalizeDeps, course: Course, started: datetime) -> RecordingSession:
    clase, mic = _class_audio()
    session = RecordingSession(
        course_id=course.id,
        course_name=course.name,
        teacher=course.teacher,
        started_at=started,
        source_label="Google Chrome",
    )
    work = deps.store.dir(session.id)
    live_txt = work / "transcripcion.live.txt"
    rec = LiveRecorder(
        session,
        [
            FileAudioSource(samples=clase, channel="clase"),
            FileAudioSource(samples=mic, channel="mic"),
        ],
        FakeTranscriber(SCRIPT + ["(yo)"]),
        None,
        EnergyDetector,
        TranscribeOptions(),
        work,
        live_txt,
        deps.bus,
    )
    rec.start()
    for state in rec._channels.values():
        state.source.finished.wait(10)  # type: ignore[attr-defined]
    live = rec.stop()
    assert live, "debe haber transcripción en vivo"
    assert live_txt.read_text(encoding="utf-8").count("\n") == len(live)
    return Finalizer(deps).run(session, course, live, rec.audio_files)


def test_t7_1_full_flow_without_api_key(tmp_path: Path, vault: Path, no_network: None) -> None:
    course = Course(name="Cálculo II", teacher="Ing. Pérez", glossary=["LIATE"])
    deps = _deps(tmp_path, vault, NullLLMProvider())
    session = record_class(deps, course, datetime(2026, 10, 12, 18, 0))

    assert session.status == "done"
    assert session.final_pass_ok
    assert not session.llm_processed
    note = vault / session.note_path
    assert note.exists() and note.parent.name == "Cálculo II"
    text = note.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["tipo"] == "clase" and fm["llm_procesado"] is False
    assert fm["curso"] == "[[Clases/Cálculo II/_Curso|Cálculo II]]"
    assert fm["profesor"] == "[[Ing. Pérez]]"
    transcript = get_block(text, "transcripcion") or ""
    assert "Profesor:" in transcript and "Yo:" in transcript
    assert "Resumen pendiente" in (get_block(text, "resumen") or "")
    # Modo local: temas (TF-IDF) y posibles tareas por reglas, marcadas a confirmar.
    assert any("ntegración por partes" in t for t in fm["temas"])
    tasks = get_block(text, "tareas") or ""
    assert "#a-confirmar" in tasks and "📅 2026-10-15" in tasks
    # Archivos de transcripción en la bóveda (carpeta oculta para Obsidian).
    tdir = vault / session.transcript_dir
    assert (tdir / "transcripcion.txt").read_text(encoding="utf-8").startswith("[00:00:")
    assert (tdir / "segmentos.json").exists()
    # Curso, índice y notas de tema creados.
    assert (vault / "Clases" / "Cálculo II" / "_Curso.md").exists()
    assert (vault / "Clases" / "_Índice.md").exists()
    assert (vault / "Profesores" / "Ing. Pérez.md").exists()
    # Audio: por defecto Opus y retención de 30 días.
    assert session.audio_files and all(p.endswith(".opus") for p in session.audio_files)
    assert session.audio_expires_at is not None
    # Tareas indexadas: «¿qué tareas tengo?» se responde sin LLM.
    assert deps.index.tasks()[0]["due"] == "2026-10-15"


def test_t6_4_prev_next_chain_out_of_order(tmp_path: Path, vault: Path) -> None:
    course = Course(name="Física I", teacher="Dra. Ruiz")
    deps = _deps(tmp_path, vault, NullLLMProvider())
    s3 = record_class(deps, course, datetime(2026, 10, 14, 9, 0))
    s1 = record_class(deps, course, datetime(2026, 10, 7, 9, 0))
    s2 = record_class(deps, course, datetime(2026, 10, 9, 9, 0))
    fm = {
        s.id: parse_frontmatter((vault / s.note_path).read_text(encoding="utf-8"))
        for s in (s1, s2, s3)
    }
    stem = {s.id: Path(s.note_path).stem for s in (s1, s2, s3)}
    assert fm[s1.id]["anterior"] == "" and stem[s2.id] in fm[s1.id]["siguiente"]
    assert stem[s1.id] in fm[s2.id]["anterior"] and stem[s3.id] in fm[s2.id]["siguiente"]
    assert stem[s2.id] in fm[s3.id]["anterior"] and fm[s3.id]["siguiente"] == ""
    nav = get_block((vault / s2.note_path).read_text(encoding="utf-8"), "navegacion") or ""
    assert "Clase anterior" in nav and "Clase siguiente" in nav
    course_note = (vault / "Clases" / "Física I" / "_Curso.md").read_text(encoding="utf-8")
    assert course_note.count("[[Clases/Física I/2026-10-") == 3


def test_guardian_llm_never_changes_transcript(tmp_path: Path, vault: Path) -> None:
    course = Course(name="Cálculo II", teacher="Ing. Pérez")
    a = record_class(
        _deps(tmp_path / "a", vault / "a", NullLLMProvider()), course, datetime(2026, 10, 12, 18, 0)
    )
    b = record_class(
        _deps(tmp_path / "b", vault / "b", FakeLLM()), course, datetime(2026, 10, 12, 18, 0)
    )
    ta = get_block((vault / "a" / a.note_path).read_text(encoding="utf-8"), "transcripcion")
    tb = get_block((vault / "b" / b.note_path).read_text(encoding="utf-8"), "transcripcion")
    assert ta == tb
    assert b.llm_processed and b.summary and b.summary.title == "Integración por partes"
    note_b = vault / "b" / b.note_path
    assert note_b.name == "2026-10-12 Integración por partes.md"
    text = note_b.read_text(encoding="utf-8")
    assert "[!summary] Resumen" in text
    assert "📅 2026-10-15" in text and "#a-confirmar" not in (get_block(text, "tareas") or "")


def test_t6_5_topics_reuse_existing_note(tmp_path: Path, vault: Path) -> None:
    (vault / "Conceptos").mkdir()
    existing = vault / "Conceptos" / "Integración por partes.md"
    existing.write_text("# Integración por partes\nMis apuntes propios.\n", encoding="utf-8")
    deps = _deps(tmp_path, vault, FakeLLM())
    s = record_class(deps, Course(name="Cálculo II"), datetime(2026, 10, 12, 18, 0))
    fm = parse_frontmatter((vault / s.note_path).read_text(encoding="utf-8"))
    assert "[[Conceptos/Integración por partes]]" in fm["temas"]
    # La nota del usuario no se modificó y no se creó un duplicado en Temas/.
    assert (
        existing.read_text(encoding="utf-8") == "# Integración por partes\nMis apuntes propios.\n"
    )
    assert not (vault / "Temas" / "Integración por partes.md").exists()
    assert (vault / "Temas" / "Regla LIATE.md").exists()


def test_user_notes_mentioned_are_linked(tmp_path: Path, vault: Path) -> None:
    note = vault / "Mis apuntes" / "Regla LIATE.md"
    note.parent.mkdir()
    note.write_text("apuntes", encoding="utf-8")
    deps = _deps(tmp_path, vault, NullLLMProvider())
    s = record_class(deps, Course(name="Cálculo II"), datetime(2026, 10, 12, 18, 0))
    related = get_block((vault / s.note_path).read_text(encoding="utf-8"), "relacionado") or ""
    assert "[[Mis apuntes/Regla LIATE|Regla LIATE]] — nota tuya mencionada en clase" in related


def test_t6_7_only_the_work_folder_is_read(tmp_path: Path, vault: Path) -> None:
    """La app trabaja en una subcarpeta de la bóveda: el resto (Privado, Notas…) ni se lee."""
    (vault / ".obsidian").mkdir()
    private = vault / "Privado" / "Regla LIATE.md"
    private.parent.mkdir()
    private.write_text("secreto", encoding="utf-8")
    work = vault / "Oyente"
    work.mkdir()
    deps = _deps(tmp_path, work, NullLLMProvider())
    s = record_class(deps, Course(name="Cálculo II"), datetime(2026, 10, 12, 18, 0))
    assert "Privado" not in (work / s.note_path).read_text(encoding="utf-8")
    assert all(not p.startswith("Privado") for p in deps.index.all_paths())


def test_transcripts_go_to_their_own_folder(tmp_path: Path, vault: Path) -> None:
    work = vault / "Oyente"
    work.mkdir()
    transcripts = tmp_path / "Transcripciones"
    cfg = AppConfig()
    cfg.general.transcripts_path = str(transcripts)
    deps = _deps(tmp_path, work, NullLLMProvider(), cfg)
    s = record_class(deps, Course(name="Cálculo II"), datetime(2026, 10, 12, 18, 0))
    folder = deps.layout.transcript_folder(s)
    assert folder is not None and folder.parent.parent == transcripts
    assert (folder / "transcripcion.txt").exists() and (folder / "segmentos.json").exists()
    # El .md queda en la carpeta de trabajo y no hay .txt ahí.
    assert (work / s.note_path).exists()
    assert not list(work.rglob("*.txt")) and not list(work.rglob("segmentos.json"))


def test_reprocess_pending_class_with_llm(tmp_path: Path, vault: Path) -> None:
    course = Course(name="Cálculo II")
    deps = _deps(tmp_path, vault, NullLLMProvider())
    s = record_class(deps, course, datetime(2026, 10, 12, 18, 0))
    note = vault / s.note_path
    # El usuario escribe sus apuntes en la nota.
    note.write_text(
        note.read_text(encoding="utf-8") + "\n## Mis apuntes\nNo olvidar repasar.\n",
        encoding="utf-8",
    )
    deps.postprocessor = PostProcessor(FakeLLM())
    fin = Finalizer(deps)
    s = fin.reprocess_with_llm(s, course)
    text = note.read_text(encoding="utf-8")
    assert s.llm_processed and "[!summary] Resumen" in text
    assert "## Mis apuntes\nNo olvidar repasar." in text
    assert parse_frontmatter(text)["llm_procesado"] is True


def test_teacher_confirmation_and_relabel(tmp_path: Path, vault: Path) -> None:
    course = Course(name="Cálculo II")
    deps = _deps(tmp_path, vault, NullLLMProvider())
    s = record_class(deps, course, datetime(2026, 10, 12, 18, 0))
    assert s.teacher_needs_confirmation and s.teacher_example_t is not None
    fin = Finalizer(deps)
    fin.relabel_teacher(s, course, "Participante 1")  # «No, el profesor es esta otra voz»
    transcript = get_block((vault / s.note_path).read_text(encoding="utf-8"), "transcripcion") or ""
    first = transcript.splitlines()[0]
    assert "Participante 1:" in first  # quien era «Profesor» ahora es participante
    assert deps.profiles.confirmed(course.id, "spectral")
    assert not deps.store.get(s.id).teacher_needs_confirmation  # type: ignore[union-attr]
