"""Interfaz de transcripción (ASR). El LLM nunca escribe ni corrige este texto."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from transcriptor.audio.types import Channel, Samples


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    channel: Channel = "clase"
    speaker: str = ""  # "Profesor", "Yo", "Participante 1"...
    words: list[Word] = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    final: bool = False

    @property
    def block_id(self) -> str:
        """Identificador de bloque de Obsidian (^t-hh-mm-ss)."""
        return "t-" + format_ts(self.start).replace(":", "-")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Segment:
        words = [Word(**w) for w in data.get("words", [])]
        return Segment(**{**data, "words": words})


def format_ts(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


@dataclass(frozen=True)
class TranscribeOptions:
    language: str = "es"
    beam_size: int = 5
    glossary: tuple[str, ...] = ()
    word_timestamps: bool = True


class Transcriber(Protocol):
    name: str

    def transcribe(self, samples: Samples, offset: float, opts: TranscribeOptions) -> list[Segment]:
        """Transcribe una frase en vivo. ``offset`` = segundo de inicio en la sesión."""
        ...

    def transcribe_file(self, path: Path, opts: TranscribeOptions) -> list[Segment]:
        """Pasada final de alta fidelidad sobre un archivo completo."""
        ...


def glossary_prompt(glossary: tuple[str, ...]) -> str | None:
    """Texto inicial con el glosario del curso para mejorar nombres propios y términos."""
    if not glossary:
        return None
    return "Glosario: " + ", ".join(glossary) + "."


# Frases que Whisper inventa en silencios (alucinaciones típicas en español).
HALLUCINATIONS = (
    "subtítulos realizados por la comunidad de amara.org",
    "subtítulos por la comunidad de amara.org",
    "gracias por ver el video",
    "gracias por ver",
    "suscríbete",
    "¡suscríbete!",
    "www.mooji.org",
)


def is_hallucination(seg: Segment) -> bool:
    text = seg.text.strip().lower()
    if not text:
        return True
    if seg.no_speech_prob > 0.8 and seg.avg_logprob < -1.0:
        return True
    return any(text == h or text.strip(" .!¡") == h.strip(" .!¡") for h in HALLUCINATIONS)
