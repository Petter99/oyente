"""Marca de la app (nombre visible) leída de branding.toml.

El nombre visible puede cambiar; el identificador interno ``APP_ID`` no.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

# Identificador técnico estable: carpetas de datos, llavero, marcadores en notas.
APP_ID = "transcriptor"
VAULT_MARKER = "transcriptor"


@dataclass(frozen=True)
class Branding:
    nombre: str
    nombre_corto: str
    eslogan: str
    sitio_web: str
    soporte: str
    calendario_google: str
    lista_tasks: str
    ejecutable: str

    def fmt(self, text: str) -> str:
        """Sustituye ``{nombre}`` en textos de la interfaz."""
        return text.replace("{nombre}", self.nombre)


def _expand(value: str, nombre: str) -> str:
    return value.replace("{nombre}", nombre)


def load_branding(raw: dict[str, str]) -> Branding:
    nombre = raw["nombre"]
    fields = {k: _expand(str(v), nombre) for k, v in raw.items()}
    return Branding(**{k: fields.get(k, "") for k in Branding.__dataclass_fields__})


@cache
def branding() -> Branding:
    data = resources.files("transcriptor").joinpath("branding.toml").read_text(encoding="utf-8")
    return load_branding(tomllib.loads(data))
