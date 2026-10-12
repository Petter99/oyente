"""Secretos en el llavero del sistema (Llavero en Mac, Administrador de credenciales en Windows)."""

from __future__ import annotations

import logging
from enum import StrEnum

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from transcriptor.branding import APP_ID
from transcriptor.logging_setup import register_secret

log = logging.getLogger(__name__)


class Secret(StrEnum):
    ANTHROPIC_API_KEY = "anthropic_api_key"
    GEMINI_API_KEY = "gemini_api_key"
    HF_TOKEN = "huggingface_token"
    GOOGLE_TOKEN = "google_oauth_token"
    ICS_URL = "university_ics_url"


class SecretStore:
    def __init__(self, service: str = APP_ID) -> None:
        self.service = service

    def get(self, key: Secret) -> str | None:
        try:
            value = keyring.get_password(self.service, key.value)
        except KeyringError:
            log.warning("No se pudo leer el llavero (%s)", key.value)
            return None
        if value:
            register_secret(value)
        return value

    def set(self, key: Secret, value: str) -> None:
        register_secret(value)
        keyring.set_password(self.service, key.value, value)

    def delete(self, key: Secret) -> None:
        try:
            keyring.delete_password(self.service, key.value)
        except PasswordDeleteError:
            pass

    def has(self, key: Secret) -> bool:
        return bool(self.get(key))
