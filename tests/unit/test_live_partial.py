"""Texto provisional de la transcripción en vivo: ``peek`` del segmentador y estado de la UI."""

from __future__ import annotations

import numpy as np
from tests.synth import PROFESOR, silence, speech

from transcriptor.asr.base import Segment
from transcriptor.audio.types import SAMPLE_RATE
from transcriptor.audio.vad import EnergyDetector, StreamingSegmenter
from transcriptor.events import Event
from transcriptor.services import AppServices, LiveState
from transcriptor.session.models import RecordingSession


def test_peek_returns_current_phrase_without_consuming_it() -> None:
    seg = StreamingSegmenter("clase", EnergyDetector())
    audio = np.concatenate([silence(0.5), speech(PROFESOR, 2.0)]).astype(np.float32)
    for i in range(0, audio.size, 1600):
        assert not list(seg.feed(audio[i : i + 1600]))  # aún no hay frase cerrada
    start, samples = seg.peek()
    assert 0.3 <= start <= 0.7  # la voz empieza tras el medio segundo de silencio
    assert samples.size >= 1.5 * SAMPLE_RATE
    assert seg.peek()[1].size == samples.size  # mirar no consume el audio
    assert [u.duration for u in seg.flush()], "la frase sigue ahí al cerrar"


def test_peek_is_empty_in_silence() -> None:
    seg = StreamingSegmenter("clase", EnergyDetector())
    list(seg.feed(silence(1.0).astype(np.float32)))
    assert seg.peek()[1].size == 0


def _services_with_live() -> AppServices:
    svc = AppServices.__new__(AppServices)  # solo hace falta el manejo de eventos
    svc.live = LiveState(session=RecordingSession(course_id="c", course_name="C", teacher=""))
    return svc


def test_partial_is_replaced_by_final_and_stale_partials_are_ignored() -> None:
    svc = _services_with_live()
    svc._on_event(Event("partial", {"channel": "clase", "start": 1.0, "end": 2.5, "text": "hola"}))
    assert svc.live.partials == {"clase": "hola"}
    final = Segment(start=1.0, end=3.0, text="hola a todos", channel="clase")
    svc._on_event(Event("segment", {"segment": final}))
    assert svc.live.partials == {}  # el definitivo reemplaza al provisional
    # Un provisional calculado antes de cerrarse la frase llega tarde: no debe reaparecer.
    svc._on_event(Event("partial", {"channel": "clase", "start": 1.0, "end": 2.8, "text": "hola"}))
    assert svc.live.partials == {}
    # Uno de la frase siguiente sí se muestra.
    svc._on_event(Event("partial", {"channel": "clase", "start": 3.2, "end": 4.0, "text": "bien"}))
    assert svc.live.partials == {"clase": "bien"}


def test_speaker_filter_becomes_a_dropdown_with_many_speakers() -> None:
    from transcriptor.ui.pages_main import uses_dropdown_filter

    few = ["Todos", "Participante", "Profesor", "Yo"]
    many = ["Todos", *[f"Participante {n}" for n in range(1, 12)], "Profesor"]
    assert not uses_dropdown_filter(few)
    assert uses_dropdown_filter(many)
