"""Lectura, escritura y migración de la configuración (config.yaml)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from transcriptor.config.models import CONFIG_VERSION, AppConfig
from transcriptor.fsutil import atomic_write_text
from transcriptor.paths import config_dir

log = logging.getLogger(__name__)

Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    # v1 guardaba "save_audio: bool" en audio; v2 usa retention.mode.
    audio = data.setdefault("audio", {})
    if "save_audio" in audio:
        audio["retention"] = {"mode": "always" if audio.pop("save_audio") else "never", "days": 30}
    data["version"] = 2
    return data


MIGRATIONS: dict[int, Migration] = {1: _v1_to_v2}


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    version = int(data.get("version", 1))
    while version < CONFIG_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ValueError(f"No hay migración desde la versión {version}")
        data = step(data)
        version = int(data["version"])
    return data


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "config.yaml")

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            return AppConfig.model_validate(migrate(raw))
        except Exception:
            # Una config dañada no debe impedir transcribir: se respalda y se usan valores por defecto.
            backup = self.path.with_suffix(".yaml.danado")
            self.path.replace(backup)
            log.exception("Configuración dañada; respaldada en %s", backup)
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        data = config.model_dump(mode="json")
        atomic_write_text(self.path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
