"""Curso y profesor a partir del texto de un evento de calendario (sin IA)."""

from __future__ import annotations

import re

_TITLES = r"(?:profesor(?:a)?|prof\.?|docente|catedr[aá]tic[oa]|instructor(?:a)?|ing\.?|dr\.?|dra\.?|lic\.?|mtro\.?|mtra\.?|mg\.?|msc\.?)"
# «Profesor: Juan Pérez», «Ing. Juan Pérez», «Docente - Juan Pérez»
_TEACHER = re.compile(
    rf"\b(?P<title>{_TITLES})\s*[:\-–]?\s*(?P<name>[A-ZÁÉÍÓÚÑ][\wáéíóúüñ'’]+(?:\s+(?:de\s+|del\s+|la\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúüñ'’]+){{0,3}})",
    re.IGNORECASE,
)
_LABELLED = re.compile(r"^(?:profesor(?:a)?|docente|catedr[aá]tic[oa]|instructor(?:a)?)$", re.I)
_SEPARATORS = re.compile(r"\s+[-–—|·]\s+")


def teacher_from_text(text: str) -> str:
    """Nombre del profesor si el texto lo etiqueta («Profesor: …», «Ing. …»); si no, ''."""
    for match in _TEACHER.finditer(text or ""):
        name = match.group("name").strip()
        title = match.group("title").lower().rstrip(".")
        # Con título de cortesía conservamos «Ing. Pérez»; con etiqueta solo el nombre.
        if _LABELLED.match(title) or title in {"prof", "profesor", "profesora"}:
            return name
        return f"{match.group('title').strip().capitalize()} {name}"
    return ""


def course_from_title(title: str) -> str:
    """Nombre del curso desde el título del evento, sin sufijos como «- Aula 3» o «(Clase)»."""
    clean = re.sub(r"\s*\((?:clase|aula|sala|virtual|presencial)[^)]*\)\s*$", "", title, flags=re.I)
    parts = _SEPARATORS.split(clean.strip())
    return parts[0].strip() if parts else ""
