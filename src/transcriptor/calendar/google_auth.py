"""Conexión con Google (OAuth de escritorio con PKCE) y autorización incremental.

No hay "login" para usar la app: esto solo se usa si el usuario pulsa «Conectar con Google».
Cada función pide su permiso únicamente cuando se activa. El token vive en el llavero.

Las credenciales del cliente OAuth se incluyen al compilar (archivo ``google_client.json``
junto al paquete o variable de entorno ``TRANSCRIPTOR_GOOGLE_CLIENT``). No van al repositorio.
"""

from __future__ import annotations

import json
import logging
import os
from importlib import resources
from typing import Any

from transcriptor.config.models import GoogleConfig
from transcriptor.config.secrets import Secret, SecretStore

log = logging.getLogger(__name__)

SCOPE_READ_SCHEDULE = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]
SCOPE_APP_CALENDAR = ["https://www.googleapis.com/auth/calendar.app.created"]
SCOPE_TASKS = ["https://www.googleapis.com/auth/tasks"]


class GoogleNotConfiguredError(RuntimeError):
    """Esta compilación no incluye credenciales OAuth de Google."""


def client_config() -> dict[str, Any]:
    env = os.environ.get("TRANSCRIPTOR_GOOGLE_CLIENT")
    if env:
        return json.loads(env)
    try:
        text = (
            resources.files("transcriptor")
            .joinpath("google_client.json")
            .read_text(encoding="utf-8")
        )
        return json.loads(text)
    except (FileNotFoundError, OSError):
        raise GoogleNotConfiguredError(
            "La conexión con Google no está disponible en esta versión (faltan credenciales OAuth)."
        ) from None


def scopes_for(cfg: GoogleConfig) -> list[str]:
    scopes: list[str] = []
    if cfg.read_schedule:
        scopes += SCOPE_READ_SCHEDULE
    if cfg.reminders:
        scopes += SCOPE_APP_CALENDAR
    if cfg.tasks:
        scopes += SCOPE_TASKS
    return scopes


class GoogleAuth:
    def __init__(self, secrets: SecretStore) -> None:
        self.secrets = secrets
        self._creds: Any = None

    @property
    def available(self) -> bool:
        try:
            client_config()
            return True
        except GoogleNotConfiguredError:
            return False

    def credentials(self, needed: list[str] | None = None) -> Any:
        """Credenciales válidas (refresca si hace falta) o None si no hay conexión o falta un permiso."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self._creds is None:
            raw = self.secrets.get(Secret.GOOGLE_TOKEN)
            if not raw:
                return None
            self._creds = Credentials.from_authorized_user_info(json.loads(raw))
        creds = self._creds
        if needed and not set(needed) <= set(creds.scopes or []):
            return None
        if not creds.valid and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save(creds)
            except Exception:
                log.warning("No se pudo renovar el acceso a Google; hay que volver a conectar")
                return None
        return creds if creds.valid else None

    def granted(self) -> set[str]:
        creds = self.credentials()
        return set(creds.scopes or []) if creds else set()

    def connect(self, scopes: list[str]) -> Any:
        """Abre el navegador para autorizar (flujo *loopback*). Pide también los permisos ya dados."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        wanted = sorted(set(scopes) | self.granted())
        flow = InstalledAppFlow.from_client_config(client_config(), wanted)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            prompt="consent",
            include_granted_scopes="true",
            success_message="Listo. Ya puedes cerrar esta pestaña y volver a la app.",
        )
        self._save(creds)
        return creds

    def _save(self, creds: Any) -> None:
        self._creds = creds
        self.secrets.set(Secret.GOOGLE_TOKEN, creds.to_json())

    def disconnect(self) -> None:
        """Revoca el token en Google y lo borra del llavero."""
        creds = self.credentials()
        if creds is not None:
            try:
                import httpx

                httpx.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": creds.token},
                    timeout=10,
                )
            except Exception:
                log.warning("No se pudo revocar el token en Google (se borra igual)")
        self.secrets.delete(Secret.GOOGLE_TOKEN)
        self._creds = None
