"""Rutas de la app independientes del sistema operativo."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

from transcriptor.branding import APP_ID

_dirs = PlatformDirs(appname=APP_ID, appauthor=False, roaming=False)


def _override() -> Path | None:
    # Permite aislar datos en pruebas o instalaciones portables.
    env = os.environ.get("TRANSCRIPTOR_HOME")
    return Path(env) if env else None


def config_dir() -> Path:
    base = _override()
    path = base / "config" if base else Path(_dirs.user_config_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    base = _override()
    path = base / "data" if base else Path(_dirs.user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    base = _override()
    path = base / "logs" if base else Path(_dirs.user_log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sessions_dir() -> Path:
    """Audio y archivos de trabajo locales (fuera de la carpeta compartida)."""
    path = data_dir() / "sesiones"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_dir() -> Path:
    """Perfiles de voz: nunca se sincronizan a la carpeta compartida."""
    path = data_dir() / "perfiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_db_path() -> Path:
    return data_dir() / "indice.sqlite"


def long_path(path: Path) -> str:
    """En Windows, antepone ``\\\\?\\`` para rutas de más de 260 caracteres."""
    text = str(path.resolve())
    if os.name == "nt" and len(text) >= 240 and not text.startswith("\\\\?\\"):
        if text.startswith("\\\\"):
            return "\\\\?\\UNC\\" + text[2:]
        return "\\\\?\\" + text
    return text
