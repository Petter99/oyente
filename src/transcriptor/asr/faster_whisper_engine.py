"""Motor principal: faster-whisper (CTranslate2). CPU int8 en la mayoría de equipos Windows."""

from __future__ import annotations

import logging
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
from transcriptor.paths import models_dir

log = logging.getLogger(__name__)


def _is_cuda_runtime_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("cublas", "cudnn", "cuda", "not found or cannot be loaded"))


class FasterWhisperTranscriber:
    def __init__(
        self, model: str, device: str = "cpu", compute_type: str = "int8", live: bool = False
    ) -> None:
        self.name = f"faster-whisper/{model}"
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.live = live
        self._model: Any = None
        self._lock = threading.Lock()

    def ensure_downloaded(self) -> None:
        """Descarga el modelo si falta (sin cargarlo en memoria)."""
        from faster_whisper.utils import download_model

        download_model(self.model_name, cache_dir=str(models_dir() / "whisper"))

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Cargando %s (%s, %s)", self.model_name, self.device, self.compute_type)
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(models_dir() / "whisper"),
            )
        return self._model

    def _run(self, audio: Any, opts: TranscribeOptions, vad: bool) -> tuple[list[Segment], Any]:
        try:
            return self._run_once(audio, opts, vad)
        except RuntimeError as exc:
            if self.device != "cuda" or not _is_cuda_runtime_error(exc):
                raise
            # CTranslate2 carga las librerías de CUDA al primer uso, no al crear el modelo:
            # si faltan (cuBLAS/cuDNN), se sigue en CPU en lugar de dejar la app sin transcribir.
            log.warning("CUDA no disponible (%s); se continúa en CPU con int8", exc)
            with self._lock:
                self.device, self.compute_type = "cpu", "int8"
                self._model = None
            return self._run_once(audio, opts, vad)

    def _run_once(
        self, audio: Any, opts: TranscribeOptions, vad: bool
    ) -> tuple[list[Segment], Any]:
        model = self._load()
        with self._lock:
            segments, info = model.transcribe(
                audio,
                language=opts.language,
                beam_size=1 if self.live else opts.beam_size,
                temperature=0.0,
                condition_on_previous_text=False,
                initial_prompt=glossary_prompt(opts.glossary),
                hotwords=" ".join(opts.glossary) or None,
                word_timestamps=opts.word_timestamps,
                vad_filter=vad,
            )
            out = [self._convert(s) for s in segments]
        return [s for s in out if not is_hallucination(s)], info

    @staticmethod
    def _convert(s: Any) -> Segment:
        words = [Word(w.start, w.end, w.word, w.probability) for w in (s.words or [])]
        return Segment(
            start=s.start,
            end=s.end,
            text=s.text.strip(),
            words=words,
            avg_logprob=s.avg_logprob,
            no_speech_prob=s.no_speech_prob,
        )

    def transcribe(self, samples: Samples, offset: float, opts: TranscribeOptions) -> list[Segment]:
        segments, _ = self._run(samples, opts, vad=False)
        for seg in segments:
            seg.start += offset
            seg.end += offset
            for w in seg.words:
                w.start += offset
                w.end += offset
        return segments

    def transcribe_file(self, path: Path, opts: TranscribeOptions) -> list[Segment]:
        audio = decode_file(path)
        segments, _ = self._run(audio, opts, vad=True)
        for s in segments:
            s.final = True
        return segments
