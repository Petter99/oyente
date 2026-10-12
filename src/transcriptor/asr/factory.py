"""Crea los transcriptores en vivo y final según el hardware detectado."""

from __future__ import annotations

from dataclasses import dataclass

from transcriptor.asr.base import Transcriber
from transcriptor.config.models import TranscriptionConfig
from transcriptor.hardware import AsrPlan, detect, plan_asr


@dataclass
class TranscriberPair:
    live: Transcriber
    final: Transcriber
    plan: AsrPlan


def create_transcribers(cfg: TranscriptionConfig) -> TranscriberPair:
    plan = plan_asr(detect(), cfg.model)
    if plan.engine == "mlx":
        from transcriptor.asr.mlx_engine import MlxWhisperTranscriber

        return TranscriberPair(
            MlxWhisperTranscriber(plan.live_model), MlxWhisperTranscriber(plan.final_model), plan
        )
    from transcriptor.asr.faster_whisper_engine import FasterWhisperTranscriber

    live = FasterWhisperTranscriber(plan.live_model, plan.device, plan.compute_type, live=True)
    final = FasterWhisperTranscriber(plan.final_model, plan.device, plan.compute_type)
    return TranscriberPair(live, final, plan)
