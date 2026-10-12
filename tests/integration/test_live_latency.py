"""Latencia de la transcripción en vivo: el texto debe verse casi al instante.

Se emite audio en tiempo real (habla continua, como un profesor) y se mide cuánto tarda en
llegar el texto a la interfaz, que consume los eventos ``segment`` (definitivo) y ``partial``
(provisional, en gris). El ASR simulado reproduce lo medido con ``base`` en CPU: un coste casi
fijo por llamada (~0.9 s, el codificador de Whisper procesa siempre una ventana de 30 s) más
un poco por segundo de audio. El identificador de hablante es el real (espectral).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
from tests.synth import PROFESOR, silence, speech

from transcriptor.asr.base import Segment, TranscribeOptions
from transcriptor.asr.fake import FakeTranscriber
from transcriptor.audio.sources import FileAudioSource
from transcriptor.audio.types import SAMPLE_RATE, Samples
from transcriptor.audio.vad import EnergyDetector
from transcriptor.events import Event, EventBus
from transcriptor.session.live import LiveRecorder
from transcriptor.session.models import RecordingSession
from transcriptor.speakers.embedder import SpectralEmbedder
from transcriptor.speakers.identifier import SpeakerIdentifier
from transcriptor.speakers.profiles import SpeakerProfileStore

MAX_LATENCY_S = 3.0  # techo exigido
MEDIAN_GOAL_S = 2.0  # meta: casi en vivo
ASR_FIXED_S = 0.9
ASR_PER_AUDIO_S = 0.05


class _TimedTranscriber(FakeTranscriber):
    """ASR simulado con el perfil de coste de Whisper en CPU."""

    def transcribe(self, samples: Samples, offset: float, opts: TranscribeOptions) -> list[Segment]:
        time.sleep(ASR_FIXED_S + samples.size / SAMPLE_RATE * ASR_PER_AUDIO_S)
        return [self._segment(offset, offset + samples.size / SAMPLE_RATE)]


DIAG: dict[str, float] = {}


def _run(tmp_path: Path, audio: np.ndarray) -> dict[str, list[tuple[float, float, float]]]:
    """Emite ``audio`` en tiempo real; devuelve, por tipo de evento, (inicio, fin, llegada)."""
    bus = EventBus()
    seen: dict[str, list[tuple[float, float, float]]] = {"segment": [], "partial": []}
    lock = threading.Lock()

    def on_event(event: Event) -> None:
        now = time.monotonic()
        with lock:
            if event.type == "segment":
                seg: Segment = event.data["segment"]
                seen["segment"].append((seg.start, seg.end, now))
            elif event.type == "partial":
                seen["partial"].append((event.data["start"], event.data["end"], now))

    bus.subscribe(on_event)
    source = FileAudioSource(samples=audio, channel="clase", realtime=True)
    rec = LiveRecorder(
        RecordingSession(course_id="c", course_name="Curso", teacher=""),
        [source],
        _TimedTranscriber(),
        SpeakerIdentifier(SpectralEmbedder(), SpeakerProfileStore(tmp_path / "perfiles"), "c"),
        EnergyDetector,
        TranscribeOptions(),
        tmp_path,
        tmp_path / "live.txt",
        bus,
    )
    from transcriptor.audio.recorder import ChannelRecorder

    original_write = ChannelRecorder.write
    spent = {"write": 0.0}

    def timed_write(self: ChannelRecorder, samples: Samples) -> float:
        t = time.monotonic()
        try:
            return original_write(self, samples)
        finally:
            spent["write"] += time.monotonic() - t

    ChannelRecorder.write = timed_write  # type: ignore[method-assign]
    t0 = time.monotonic()
    try:
        rec.start()
        source.finished.wait(audio.size / SAMPLE_RATE + 10)
        DIAG["fed_after_s"] = round(time.monotonic() - t0, 2)
        rec.stop()
    finally:
        ChannelRecorder.write = original_write  # type: ignore[method-assign]
    DIAG["audio_s"] = round(audio.size / SAMPLE_RATE, 2)
    DIAG["flac_write_total_s"] = round(spent["write"], 2)
    # Se convierte la llegada a «segundos desde el inicio» para compararla con el audio.
    return {k: [(s, e, a - t0) for s, e, a in v] for k, v in seen.items()}


def _continuous_speech() -> np.ndarray:
    # Peor caso: habla continua, sin pausas largas donde cortar, y luego silencio.
    return np.concatenate([silence(1.0), speech(PROFESOR, 10.0), silence(1.5)]).astype(np.float32)


def test_final_text_arrives_within_3_seconds(tmp_path: Path) -> None:
    seen = _run(tmp_path, _continuous_speech())
    final = seen["segment"]
    assert len(final) >= 2, "la habla continua debe partirse en varias frases"
    lags = [arrived - end for _, end, arrived in final]
    assert max(lags) <= MAX_LATENCY_S, f"latencias (s): {[round(x, 2) for x in lags]} diag={DIAG}"
    assert float(np.median(lags)) <= MEDIAN_GOAL_S


def test_provisional_text_follows_speech_almost_live(tmp_path: Path) -> None:
    """Mientras se habla, el texto provisional va a ≤ 3 s de lo que acaba de sonar."""
    seen = _run(tmp_path, _continuous_speech())
    partial = seen["partial"]
    assert len(partial) >= 3, "debe haber varias actualizaciones mientras se habla"
    speech_starts_at = 1.0
    first_lag = partial[0][2] - speech_starts_at
    assert first_lag <= MAX_LATENCY_S, f"primer texto a los {first_lag:.2f} s de empezar a hablar"
    # Cada actualización cubre hasta el instante «end»: no puede ir muy por detrás de él.
    lags = [arrived - end for _, end, arrived in partial]
    assert max(lags) <= MAX_LATENCY_S, f"retrasos (s): {[round(x, 2) for x in lags]} diag={DIAG}"
    assert float(np.median(lags)) <= MEDIAN_GOAL_S
