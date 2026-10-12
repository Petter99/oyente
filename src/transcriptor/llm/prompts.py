"""Prompts fijos (se cachean) y formato compacto de la transcripción para gastar pocos tokens."""

from __future__ import annotations

from transcriptor.asr.base import Segment, format_ts

SUMMARY_SYSTEM = """Eres un asistente académico. Recibes la transcripción de una clase universitaria en español.
Tu trabajo es EXTRAER información; nunca reescribas ni corrijas la transcripción.

Reglas:
- Usa solo lo que aparece en la transcripción. No inventes tareas, fechas ni temas.
- Las tareas son encargos explícitos del profesor (ejercicios, lecturas, entregas, exámenes).
- "fecha_texto": copia la expresión de fecha tal como se dijo; si no se dijo, deja "".
- "minuto": usa la marca [hh:mm:ss] de la línea donde aparece, o "".
- "temas": conceptos de la materia como posibles nombres de nota (p. ej. "Integración por partes").
- Escribe en español, con frases claras y breves.
- Hablantes: P = Profesor, Y = el estudiante que graba, E1/E2 = otros participantes."""

MERGE_SYSTEM = """Eres un asistente académico. Recibes resultados parciales extraídos durante una clase
universitaria (tareas, puntos y temas por bloque) y un extracto de la transcripción.
Únelos en un resumen final sin duplicados. No inventes información que no esté en los datos.
"fecha_texto" se copia tal cual; "minuto" usa marcas [hh:mm:ss] existentes."""

LIVE_SYSTEM = """Eres un asistente que acompaña una clase universitaria en vivo. Recibes SOLO el fragmento
nuevo de la transcripción y un estado breve de lo ya detectado. Devuelve únicamente lo NUEVO:
tareas encargadas por el profesor (con fecha_texto tal como se dijo), puntos importantes
("esto viene en el examen", definiciones clave), hasta 2 preguntas útiles si algo quedó confuso,
y temas. Si no hay nada nuevo, devuelve listas vacías. No inventes nada."""

CHAT_SYSTEM = """Eres el asistente de estudio de un estudiante universitario. Respondes preguntas sobre sus
clases usando SOLO los fragmentos proporcionados (transcripciones y resúmenes).
- Cita la clase y el minuto así: [Curso · AAAA-MM-DD · mm:ss].
- Si la respuesta no está en los fragmentos, dilo claramente y sugiere qué buscar.
- Responde en español, breve y claro. Puedes usar listas."""

SPEAKER_CODE = {"Profesor": "P", "Yo": "Y"}


def speaker_code(speaker: str) -> str:
    if speaker in SPEAKER_CODE:
        return SPEAKER_CODE[speaker]
    if speaker.startswith("Participante"):
        num = speaker.split()[-1]
        return f"E{num}" if num.isdigit() else "E"
    return speaker[:2] or "?"


def compact_transcript(segments: list[Segment]) -> str:
    """Una línea por intervención: «[hh:mm:ss] P: texto». Une frases seguidas del mismo hablante."""
    lines: list[str] = []
    current: tuple[str, float, list[str]] | None = None
    for seg in segments:
        code = speaker_code(seg.speaker)
        if current and current[0] == code and seg.start - current[1] < 90:
            current[2].append(seg.text.strip())
            continue
        if current:
            lines.append(f"[{format_ts(current[1])}] {current[0]}: {' '.join(current[2])}")
        current = (code, seg.start, [seg.text.strip()])
    if current:
        lines.append(f"[{format_ts(current[1])}] {current[0]}: {' '.join(current[2])}")
    return "\n".join(lines)


def parse_ts(value: str) -> float | None:
    v = value.strip().strip("[]")
    parts = v.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return None
