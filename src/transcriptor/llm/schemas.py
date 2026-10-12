"""Formatos JSON que se piden al LLM (simples, sin campos opcionales: válidos en todos los proveedores)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LLMKeyPoint(BaseModel):
    texto: str = Field(description="Punto importante, una frase")
    minuto: str = Field(
        description="Marca [hh:mm:ss] de la transcripción donde se dijo, o cadena vacía"
    )


class LLMTask(BaseModel):
    descripcion: str = Field(description="Qué hay que hacer o estudiar, en una frase")
    fecha_texto: str = Field(
        description="Expresión de fecha tal como se dijo (p. ej. 'el próximo jueves'), o vacío"
    )
    tipo: Literal["tarea", "examen", "lectura", "otro"]
    minuto: str = Field(description="Marca [hh:mm:ss] donde se mencionó, o vacío")


class LLMClassSummary(BaseModel):
    titulo: str = Field(description="Título corto del tema principal de la clase (máx. 8 palabras)")
    resumen: str = Field(description="Resumen de 3 a 6 frases")
    puntos_clave: list[LLMKeyPoint]
    temas: list[str] = Field(description="3 a 8 conceptos o temas, como nombres de nota (sin '#')")
    tareas: list[LLMTask]
    curso_sugerido: str = Field(
        description="Nombre de la materia si se deduce de la transcripción o del evento de calendario; si no, vacío"
    )
    profesor_sugerido: str = Field(
        description="Nombre del profesor si se dice o aparece en el evento de calendario; si no, vacío"
    )


class ChunkInsights(BaseModel):
    """Resultado parcial de las sugerencias en vivo (solo del texto nuevo)."""

    tareas: list[LLMTask]
    puntos_clave: list[LLMKeyPoint]
    preguntas_sugeridas: list[str] = Field(description="0 a 2 preguntas útiles para hacer en clase")
    temas: list[str]


class EventProposal(BaseModel):
    titulo: str
    fecha_texto: str
    descripcion: str
    tipo: Literal["tarea", "examen", "lectura", "otro"]
