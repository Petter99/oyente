"""Gancho de licencias para una futura versión comercial.

Hoy la app no se vende: ``LicenseService`` siempre devuelve "activada". Si algún día se
vende, se implementa otra clase con la misma interfaz (claves, prueba gratuita) sin tocar
el resto del código (ver docs/plan/plan-fases.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LicenseStatus:
    active: bool
    kind: str = "personal"
    message: str = ""


class LicenseService(Protocol):
    def status(self) -> LicenseStatus: ...

    def feature_enabled(self, feature: str) -> bool: ...


class AlwaysActiveLicense:
    def status(self) -> LicenseStatus:
        return LicenseStatus(active=True)

    def feature_enabled(self, feature: str) -> bool:
        return True
