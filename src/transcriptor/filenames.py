"""Nombres de archivo válidos en Windows, macOS y Linux (y en las nubes de sincronización)."""

from __future__ import annotations

import re
import unicodedata

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Obsidian además trata # ^ [ ] | como especiales en los enlaces.
_OBSIDIAN = re.compile(r"[#^\[\]]")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, max_len: int = 120, fallback: str = "sin título") -> str:
    """Devuelve un nombre de archivo seguro conservando tildes y ñ.

    >>> safe_filename("Clase: repaso?")
    'Clase - repaso'
    """
    text = unicodedata.normalize("NFC", name)
    text = text.replace(":", " -")
    text = _FORBIDDEN.sub(" ", text)
    text = _OBSIDIAN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Windows no admite punto ni espacio al final.
    text = text.rstrip(". ")
    if len(text) > max_len:
        text = text[:max_len].rstrip(". ")
    if not text:
        text = fallback
    stem = text.split(".")[0].upper()
    if stem in _RESERVED:
        text = f"_{text}"
    return text


def slug(text: str) -> str:
    """Etiqueta para tags de Obsidian: ``Cálculo II`` → ``calculo-ii``."""
    norm = unicodedata.normalize("NFKD", text)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm or "x"
