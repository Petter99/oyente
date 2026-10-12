"""Motor para Mac con Apple Silicon: mlx-whisper (GPU/Neural Engine)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from transcriptor.asr.base import (
    Segment,
    TranscribeOptions,
    Word,
    glossary_prompt,
    is_hallucination,
)
from transcriptor.audio.decode import decode_file
from transcriptor.audio.types import Samples

REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}


class MlxWhisperTranscriber:
    def __init__(self, model: str) -> None:
        self.name = f"mlx-whisper/{model}"
        self.repo = REPOS.get(model, model)
        self._lock = threading.Lock()

    def _run(self, audio: Any, opts: TranscribeOptions) -> list[Segment]:
        import mlx_whisper

        with self._lock:
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self.repo,
                language=opts.language,
                temperature=0.0,
                condition_on_previous_text=False,
                initial_prompt=glossary_prompt(opts.glossary),
                word_timestamps=opts.word_timestamps,
            )
        out = []
        for s in result.get("segments", []):
            words = [
                Word(w["start"], w["end"], w["word"], w.get("probability", 1.0))
                for w in s.get("words", [])
            ]
            seg = Segment(
                start=s["start"],
                end=s["end"],
                text=s["text"].strip(),
                words=words,
                avg_logprob=s.get("avg_logprob", 0.0),
                no_speech_prob=s.get("no_speech_prob", 0.0),
            )
            if not is_hallucination(seg):
                out.append(seg)
        return out

    def transcribe(self, samples: Samples, offset: float, opts: TranscribeOptions) -> list[Segment]:
        segments = self._run(samples, opts)
        for seg in segments:
            seg.start += offset
            seg.end += offset
            for w in seg.words:
                w.start += offset
                w.end += offset
        return segments

    def transcribe_file(self, path: Path, opts: TranscribeOptions) -> list[Segment]:
        segments = self._run(decode_file(path), opts)
        for s in segments:
            s.final = True
        return segments
