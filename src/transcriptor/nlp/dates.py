"""Convierte expresiones de fecha en español a fechas concretas, relativas al día de la clase.

Determinista y verificable: el LLM solo propone el texto («el próximo jueves»); la fecha la
calcula este código.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from transcriptor.nlp.text import fold

WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
          "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
NUMBERS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
           "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15}

_WD = "|".join(WEEKDAYS)
_MO = "|".join(MONTHS)

PATTERNS = [
    re.compile(rf"\b(?:el\s+)?(?:proximo|siguiente)\s+({_WD})\b"),
    re.compile(rf"\b({_WD})\s+(?:de\s+la\s+)?(?:proxima|siguiente|otra)\s+semana\b"),
    re.compile(rf"\b(?:este|el|para el|hasta el)\s+({_WD})\b"),
    re.compile(rf"\b({_WD})\s+(\d{{1,2}})\b"),
    re.compile(rf"\b(\d{{1,2}})\s+de\s+({_MO})(?:\s+(?:de|del)\s+(\d{{4}}))?\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"),
    re.compile(r"\bpasado\s+manana\b"),
    re.compile(r"\bmanana\b"),
    re.compile(r"\bhoy\b"),
    re.compile(r"\b(?:en|dentro\s+de)\s+(\w+)\s+(dias|semanas)\b"),
    re.compile(r"\b(?:la\s+)?(?:proxima|siguiente)\s+semana\b"),
    re.compile(rf"\b({_WD})\b"),
]


def _next_weekday(base: date, weekday: int, strictly_after: bool = True) -> date:
    days = (weekday - base.weekday()) % 7
    if days == 0 and strictly_after:
        days = 7
    return base + timedelta(days=days)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def resolve(text: str, base: date | datetime) -> date | None:
    """Fecha a partir de una expresión en español. ``None`` si no se reconoce."""
    if isinstance(base, datetime):
        base = base.date()
    t = fold(text)
    for i, pattern in enumerate(PATTERNS):
        m = pattern.search(t)
        if not m:
            continue
        if i == 0:  # el próximo jueves
            return _next_weekday(base, WEEKDAYS[m.group(1)])
        if i == 1:  # jueves de la próxima semana
            monday_next = base + timedelta(days=7 - base.weekday())
            return monday_next + timedelta(days=WEEKDAYS[m.group(1)])
        if i == 2:  # este/el jueves
            return _next_weekday(base, WEEKDAYS[m.group(1)])
        if i == 3:  # jueves 19
            day = int(m.group(2))
            cand = _next_weekday(base, WEEKDAYS[m.group(1)], strictly_after=False)
            for _ in range(8):
                if cand.day == day:
                    return cand
                cand += timedelta(days=7)
            continue
        if i == 4:  # 19 de octubre (de 2026)
            year = int(m.group(3)) if m.group(3) else base.year
            d = _safe_date(year, MONTHS[m.group(2)], int(m.group(1)))
            if d and not m.group(3) and d < base - timedelta(days=30):
                d = _safe_date(year + 1, d.month, d.day)
            if d:
                return d
            continue
        if i == 5:  # 19/10(/2026)
            year = int(m.group(3)) if m.group(3) else base.year
            if year < 100:
                year += 2000
            d = _safe_date(year, int(m.group(2)), int(m.group(1)))
            if d and not m.group(3) and d < base - timedelta(days=30):
                d = _safe_date(year + 1, d.month, d.day)
            if d:
                return d
            continue
        if i == 6:
            return base + timedelta(days=2)
        if i == 7:
            return base + timedelta(days=1)
        if i == 8:
            return base
        if i == 9:  # en dos semanas / dentro de 3 días
            n = NUMBERS.get(m.group(1)) or (int(m.group(1)) if m.group(1).isdigit() else None)
            if n is None:
                continue
            return base + timedelta(days=n * (7 if m.group(2) == "semanas" else 1))
        if i == 10:  # la próxima semana → lunes siguiente
            return base + timedelta(days=7 - base.weekday())
        if i == 11:  # «jueves» suelto
            return _next_weekday(base, WEEKDAYS[m.group(1)])
    return None


def find_date_phrase(sentence: str) -> str | None:
    """Devuelve el fragmento de fecha encontrado en una frase (en su forma normalizada)."""
    t = fold(sentence)
    for pattern in PATTERNS:
        m = pattern.search(t)
        if m:
            return m.group(0)
    return None
