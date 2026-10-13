"""Detección de Obsidian: si está instalado y qué bóvedas tiene registradas.

Obsidian guarda sus bóvedas en ``obsidian.json`` (una por carpeta). La app lo lee para ofrecer
una lista en vez de pedir una ruta, y crea una subcarpeta propia dentro de la bóveda elegida.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DOWNLOAD_URL = "https://obsidian.md/download"


@dataclass(frozen=True)
class Vault:
    path: Path
    name: str


def _config_file() -> Path:
    override = os.environ.get("TRANSCRIPTOR_OBSIDIAN_CONFIG")  # pruebas: no leer el Obsidian real
    if override:
        return Path(override)
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "obsidian" / "obsidian.json"


def list_vaults(config_file: Path | None = None) -> list[Vault]:
    """Bóvedas registradas en Obsidian que existen en disco (la última usada primero)."""
    path = config_file or _config_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = data.get("vaults") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return []
    entries = sorted(
        (v for v in raw.values() if isinstance(v, dict) and v.get("path")),
        key=lambda v: v.get("ts") or 0,
        reverse=True,
    )
    vaults: list[Vault] = []
    for entry in entries:
        folder = Path(str(entry["path"]))
        if folder.is_dir():
            vaults.append(Vault(folder, folder.name))
    return vaults


def find_vault(folder: Path) -> Path | None:
    """La bóveda (carpeta con ``.obsidian``) que contiene ``folder``, o None."""
    try:
        folder = folder.resolve()
    except OSError:
        return None
    for candidate in (folder, *folder.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def is_registered(vault: Path, config_file: Path | None = None) -> bool:
    """¿Obsidian conoce esta bóveda? Sin eso, ``obsidian://open?path=`` no encuentra la nota."""
    try:
        target = vault.resolve()
        return any(v.path.resolve() == target for v in list_vaults(config_file))
    except OSError:
        return False


def obsidian_installed() -> bool:
    system = platform.system()
    if system == "Windows":
        if sys.platform == "win32":  # mypy solo entiende sys.platform
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "obsidian"):
                    return True
            except OSError:
                pass
        local = os.environ.get("LOCALAPPDATA")
        return bool(local and (Path(local) / "Programs" / "Obsidian" / "Obsidian.exe").exists())
    if system == "Darwin":
        return (
            Path("/Applications/Obsidian.app").exists()
            or (Path.home() / "Applications" / "Obsidian.app").exists()
        )
    return bool(shutil.which("obsidian")) or _config_file().exists()
