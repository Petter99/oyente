"""Tasa de error por palabra (WER) para medir la fidelidad de la transcripción."""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str, strip_accents: bool = False) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    if strip_accents:
        text = "".join(
            c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
        )
    text = re.sub(r"[^\w\s]", " ", text)
    return text.split()


def wer(reference: str, hypothesis: str, strip_accents: bool = False) -> float:
    ref = normalize(reference, strip_accents)
    hyp = normalize(hypothesis, strip_accents)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)
