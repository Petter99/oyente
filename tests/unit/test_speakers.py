"""Identificación del profesor (T4.*), con voces sintéticas y el extractor espectral."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from tests.synth import ESTUDIANTE, PROFESOR, YO, Voice, speech

from transcriptor.asr.base import Segment
from transcriptor.speakers import ME, TEACHER, SpeakerIdentifier, SpeakerProfileStore
from transcriptor.speakers.embedder import SpectralEmbedder


def _segments(turns: list[tuple[Voice, float, str]]) -> tuple[list[Segment], list[np.ndarray]]:
    segs, audio = [], []
    t = 0.0
    for i, (voice, dur, channel) in enumerate(turns):
        segs.append(Segment(t, t + dur, f"frase {i}", channel=channel))  # type: ignore[arg-type]
        audio.append(speech(voice, dur, seed=voice.seed * 1000 + i))
        t += dur + 0.5
    return segs, audio


def _identifier(tmp_path: Path, course: str = "calculo") -> SpeakerIdentifier:
    return SpeakerIdentifier(SpectralEmbedder(), SpeakerProfileStore(tmp_path / "perfiles"), course)


CLASS = [
    (PROFESOR, 6.0, "clase"),
    (ESTUDIANTE, 2.0, "clase"),
    (PROFESOR, 5.0, "clase"),
    (YO, 2.0, "mic"),
    (PROFESOR, 7.0, "clase"),
    (ESTUDIANTE, 1.5, "clase"),
    (PROFESOR, 4.0, "clase"),
]


def test_t4_1_mic_channel_is_always_me(tmp_path: Path) -> None:
    ident = _identifier(tmp_path)
    label, emb = ident.label_live("mic", speech(PROFESOR, 2.0))
    assert label == ME and emb is None


def test_t4_2_and_t4_3_first_class_teacher_is_who_talks_most(tmp_path: Path) -> None:
    ident = _identifier(tmp_path)
    segs, audio = _segments(CLASS)
    embs = [
        ident.embedder.embed(a) if s.channel == "clase" else None
        for s, a in zip(segs, audio, strict=True)
    ]
    result = ident.label_final(segs, embs)
    expected = [TEACHER, "Participante 1", TEACHER, ME, TEACHER, "Participante 1", TEACHER]
    assert result.labels == expected
    assert result.needs_confirmation  # primera clase: pedir confirmación
    accuracy = np.mean([a == b for a, b in zip(result.labels, expected, strict=True)])
    assert accuracy >= 0.95


def test_t4_4_confirmation_creates_profile_used_next_class(tmp_path: Path) -> None:
    ident = _identifier(tmp_path)
    segs, audio = _segments(CLASS)
    embs = [
        ident.embedder.embed(a) if s.channel == "clase" else None
        for s, a in zip(segs, audio, strict=True)
    ]
    result = ident.label_final(segs, embs)
    assert result.teacher_embedding is not None
    ident.confirm_teacher(result.teacher_embedding)

    # Clase siguiente: el estudiante habla MÁS que el profesor, pero el perfil manda.
    nxt = _identifier(tmp_path)
    turns = [(ESTUDIANTE, 8.0, "clase"), (PROFESOR, 3.0, "clase"), (ESTUDIANTE, 6.0, "clase")]
    segs2, audio2 = _segments(turns)
    embs2 = [nxt.embedder.embed(a) for a in audio2]
    res2 = nxt.label_final(segs2, embs2)
    assert res2.labels == ["Participante 1", TEACHER, "Participante 1"]
    assert not res2.needs_confirmation
    live, _ = nxt.label_live("clase", speech(PROFESOR, 3.0, seed=777))
    assert live == TEACHER
    live2, _ = nxt.label_live("clase", speech(ESTUDIANTE, 3.0, seed=778))
    assert live2 == "Participante"


def test_live_without_profile_tracks_dominant_speaker(tmp_path: Path) -> None:
    ident = _identifier(tmp_path)
    labels = [
        ident.label_live("clase", speech(v, d, seed=i))[0]
        for i, (v, d) in enumerate([(PROFESOR, 6), (PROFESOR, 5), (ESTUDIANTE, 2), (PROFESOR, 4)])
    ]
    assert labels == [TEACHER, TEACHER, "Participante", TEACHER]


def test_participants_not_distinguished_when_disabled(tmp_path: Path) -> None:
    ident = SpeakerIdentifier(
        SpectralEmbedder(), SpeakerProfileStore(tmp_path), "c", distinguish_participants=False
    )
    segs, audio = _segments(CLASS)
    embs = [
        ident.embedder.embed(a) if s.channel == "clase" else None
        for s, a in zip(segs, audio, strict=True)
    ]
    assert "Participante" in ident.label_final(segs, embs).labels


def test_t4_6_profiles_are_local_and_deletable(tmp_path: Path) -> None:
    store = SpeakerProfileStore(tmp_path / "perfiles")
    store.update("c1", "spectral", np.ones(4, dtype=np.float32))
    assert store.get("c1", "spectral") is not None
    store.delete("c1")
    assert store.get("c1", "spectral") is None
