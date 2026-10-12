"""Detección de voz y segmentación en frases para la transcripción en vivo.

Usa Silero VAD (incluido en faster-whisper, ONNX, CPU). Si no está disponible, usa un
detector por energía (suficiente para pruebas y equipos sin el modelo).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from transcriptor.audio.types import SAMPLE_RATE, Channel, Samples, Utterance

log = logging.getLogger(__name__)


class SpeechDetector(Protocol):
    def speech_spans(self, samples: Samples) -> list[tuple[int, int]]:
        """Intervalos [inicio, fin) en muestras donde hay voz."""
        ...


class SileroDetector:
    def __init__(self, threshold: float = 0.5, min_silence_ms: int = 500) -> None:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        self._fn = get_speech_timestamps
        self._opts = VadOptions(threshold=threshold, min_silence_duration_ms=min_silence_ms,
                                speech_pad_ms=200, min_speech_duration_ms=200)

    def speech_spans(self, samples: Samples) -> list[tuple[int, int]]:
        if samples.size < SAMPLE_RATE // 4:
            return []
        stamps = self._fn(samples, self._opts)
        return [(int(s["start"]), int(s["end"])) for s in stamps]


class EnergyDetector:
    """Detector simple por energía con umbral adaptativo al ruido de fondo."""

    def __init__(self, frame_ms: int = 30, margin_db: float = 12.0, floor_db: float = -50.0) -> None:
        self.frame = SAMPLE_RATE * frame_ms // 1000
        self.margin_db = margin_db
        self.floor_db = floor_db

    def speech_spans(self, samples: Samples) -> list[tuple[int, int]]:
        n = samples.size // self.frame
        if n == 0:
            return []
        frames = samples[: n * self.frame].reshape(n, self.frame)
        energy = 10 * np.log10(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
        noise = np.percentile(energy, 10)
        threshold = max(noise + self.margin_db, self.floor_db)
        voiced = energy > threshold
        spans: list[tuple[int, int]] = []
        start = None
        silence = 0
        hang = max(1, 300 // (self.frame * 1000 // SAMPLE_RATE))  # 300 ms de tolerancia
        for i, v in enumerate(voiced):
            if v:
                if start is None:
                    start = i
                silence = 0
            elif start is not None:
                silence += 1
                if silence > hang:
                    spans.append((start * self.frame, (i - silence + 1) * self.frame))
                    start, silence = None, 0
        if start is not None:
            spans.append((start * self.frame, (n - silence) * self.frame))
        return [(s, e) for s, e in spans if e - s >= SAMPLE_RATE // 5]


def default_detector() -> SpeechDetector:
    try:
        return SileroDetector()
    except Exception:
        log.warning("Silero VAD no disponible; se usa detección por energía")
        return EnergyDetector()


@dataclass
class SegmenterConfig:
    min_silence_s: float = 0.6
    max_utterance_s: float = 25.0
    pad_s: float = 0.15
    step_s: float = 0.5


class StreamingSegmenter:
    """Acumula audio de un canal y emite frases completas (Utterance) al detectar silencio."""

    def __init__(self, channel: Channel, detector: SpeechDetector,
                 config: SegmenterConfig | None = None) -> None:
        self.channel = channel
        self.detector = detector
        self.cfg = config or SegmenterConfig()
        self._buf = np.zeros(0, dtype=np.float32)
        self._offset = 0  # muestra absoluta del inicio de _buf
        self._since_check = 0

    def feed(self, samples: Samples) -> Iterator[Utterance]:
        self._buf = np.concatenate([self._buf, samples.astype(np.float32)])
        self._since_check += samples.size
        if self._since_check < self.cfg.step_s * SAMPLE_RATE:
            return
        self._since_check = 0
        yield from self._process(final=False)

    def flush(self) -> Iterator[Utterance]:
        yield from self._process(final=True)
        self._buf = np.zeros(0, dtype=np.float32)

    def _cut(self, start: int, end: int) -> Utterance:
        pad = int(self.cfg.pad_s * SAMPLE_RATE)
        s = max(0, start - pad)
        e = min(self._buf.size, end + pad)
        utt = Utterance(
            channel=self.channel,
            start=(self._offset + s) / SAMPLE_RATE,
            end=(self._offset + e) / SAMPLE_RATE,
            samples=self._buf[s:e].copy(),
        )
        self._drop(e)
        return utt

    def _drop(self, n: int) -> None:
        self._buf = self._buf[n:]
        self._offset += n

    def _process(self, final: bool) -> Iterator[Utterance]:
        while self._buf.size:
            spans = self.detector.speech_spans(self._buf)
            length = self._buf.size
            if not spans:
                # Sin voz: conservar solo el último medio segundo para no cortar el inicio.
                keep = int(0.5 * SAMPLE_RATE)
                if final:
                    self._drop(length)
                elif length > keep:
                    self._drop(length - keep)
                return
            first_start = spans[0][0]
            max_len = int(self.cfg.max_utterance_s * SAMPLE_RATE)
            min_sil = int(self.cfg.min_silence_s * SAMPLE_RATE)
            # Buscar el primer hueco de silencio suficiente después del inicio de la voz.
            cut_end = None
            for (_, e1), (s2, _) in zip(spans, spans[1:], strict=False):
                if s2 - e1 >= min_sil:
                    cut_end = e1
                    break
            if cut_end is None and length - spans[-1][1] >= min_sil:
                cut_end = spans[-1][1]
            too_long = (cut_end if cut_end is not None else length) - first_start > max_len
            if too_long:
                # Frase demasiado larga: cortar en el hueco más grande antes del máximo.
                limit = first_start + max_len
                gaps = [(s2 - e1, e1) for (_, e1), (s2, _) in zip(spans, spans[1:], strict=False)
                        if e1 <= limit]
                cut_end = max(gaps)[1] if gaps else limit
            if cut_end is None and final:
                cut_end = spans[-1][1]
            if cut_end is None:
                return
            yield self._cut(first_start, cut_end)
