"""Escritura atómica segura para carpetas sincronizadas (OneDrive, Drive, iCloud, Dropbox)."""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path


class ConcurrentModificationError(RuntimeError):
    """El archivo cambió en disco desde que se leyó (p. ej. el usuario lo editó)."""


def atomic_write_text(path: Path, text: str, expected_mtime: float | None = None) -> float:
    """Escribe ``text`` en ``path`` de forma atómica y devuelve el nuevo mtime.

    Si ``expected_mtime`` se indica y el archivo cambió desde entonces, no escribe y
    lanza ConcurrentModificationError para no pisar cambios del usuario.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected_mtime is not None and path.exists():
        current = path.stat().st_mtime
        if abs(current - expected_mtime) > 1e-6:
            raise ConcurrentModificationError(str(path))
    fd, tmp = tempfile.mkstemp(prefix=".~oy-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(Path(tmp), path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path.stat().st_mtime


def _replace_with_retry(src: Path, dst: Path, attempts: int = 8) -> None:
    # En Windows, antivirus o clientes de sincronización pueden bloquear el archivo un instante.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (2**i))


def read_text(path: Path) -> tuple[str, float]:
    """Lee un archivo de texto UTF-8 y devuelve (contenido, mtime)."""
    return path.read_text(encoding="utf-8"), path.stat().st_mtime
