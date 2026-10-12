"""Transcriptor simulado para pruebas: devuelve textos predefinidos sin cargar modelos."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from transcriptor.asr.base import Segment, TranscribeOptions, Word
from transcriptor.audio.decode import decode_file
from transcriptor.audio.types import SAMPLE_RATE, Samples


class FakeTranscriber:
    def __init__(self, script: list[str] | Callable[[float, float], str] | None = None,
                 delay: float = 0.0) -> None:
        self.name = "fake"
        self.script = script or []
        self.calls = 0
        self.delay = delay

    def _text(self, start: float, end: float) -> str:
        if callable(self.script):
            return self.script(start, end)
        if not self.script:
            return f"frase {self.calls}"
        return self.script[(self.calls - 1) % len(self.script)]

    def _segment(self, start: float, end: float) -> Segment:
        self.calls += 1
        text = self._text(start, end)
        words = []
        tokens = text.split()
        step = (end - start) / max(len(tokens), 1)
        for i, tok in enumerate(tokens):
            words.append(Word(start + i * step, start + (i + 1) * step, " " + tok,
                              0.3 if tok.startswith("~") else 0.95))
        return Segment(start=start, end=end, text=text.replace("~", ""), words=words)

    def transcribe(self, samples: Samples, offset: float, opts: TranscribeOptions) -> list[Segment]:
        if self.delay:
            import time

            time.sleep(self.delay)
        return [self._segment(offset, offset + samples.size / SAMPLE_RATE)]

    def transcribe_file(self, path: Path, opts: TranscribeOptions) -> list[Segment]:
        from transcriptor.audio.vad import EnergyDetector

        audio = decode_file(path)
        out = []
        for s, e in EnergyDetector().speech_spans(audio):
            seg = self._segment(s / SAMPLE_RATE, e / SAMPLE_RATE)
            seg.final = True
            out.append(seg)
        return out
