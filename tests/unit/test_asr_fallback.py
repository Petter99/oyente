"""Si CUDA falla al transcribir (faltan cuBLAS/cuDNN), el motor sigue en CPU."""

from __future__ import annotations

from typing import Any

import pytest

from transcriptor.asr.base import TranscribeOptions
from transcriptor.asr.faster_whisper_engine import FasterWhisperTranscriber


def _engine(device: str, error: Exception | None) -> tuple[FasterWhisperTranscriber, list[str]]:
    engine = FasterWhisperTranscriber("tiny", device, "float16" if device == "cuda" else "int8")
    seen: list[str] = []

    def fake_run_once(audio: Any, opts: TranscribeOptions, vad: bool) -> tuple[list, Any]:
        seen.append(engine.device)
        if engine.device == "cuda" and error is not None:
            raise error
        return [], None

    engine._run_once = fake_run_once  # type: ignore[method-assign]
    return engine, seen


def test_cuda_missing_library_falls_back_to_cpu() -> None:
    err = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    engine, seen = _engine("cuda", err)
    engine._run(None, TranscribeOptions(), vad=False)
    assert seen == ["cuda", "cpu"]
    assert (engine.device, engine.compute_type) == ("cpu", "int8")


def test_unrelated_runtime_error_is_not_swallowed() -> None:
    engine, seen = _engine("cuda", RuntimeError("audio corrupto"))
    with pytest.raises(RuntimeError, match="audio corrupto"):
        engine._run(None, TranscribeOptions(), vad=False)
    assert seen == ["cuda"]


def test_cpu_errors_are_not_retried() -> None:
    engine, _ = _engine("cpu", None)
    engine._run_once = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cublas"))  # type: ignore[assignment,method-assign]
    with pytest.raises(RuntimeError):
        engine._run(None, TranscribeOptions(), vad=False)
