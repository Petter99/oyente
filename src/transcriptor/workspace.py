"""Carpeta de trabajo: dónde viven las notas (.md) y las transcripciones (.txt), y cómo moverlas.

Las notas van dentro de una subcarpeta propia (p. ej. ``<bóveda>/Notas de clase``): es lo único a lo que la
app accede, así que el resto de la bóveda de Obsidian ni se lee ni se indexa. Las transcripciones
van aparte, fuera de la bóveda.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from transcriptor.notes.writer import VaultLayout

log = logging.getLogger(__name__)

DEFAULT_SUBFOLDER = "Notas de clase"


def default_transcripts_dir(work: Path, vault: Path | None = None) -> Path:
    """Junto a la bóveda (o a la carpeta de trabajo), nunca dentro de ella."""
    return (vault or work).parent / "Transcripciones"


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _merge_move(src: Path, dst: Path) -> tuple[int, int]:
    """Mueve ``src`` dentro de ``dst`` sin pisar nada. Devuelve (movidos, omitidos)."""
    if not src.exists():
        return 0, 0
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return 1, 0
    if not (src.is_dir() and dst.is_dir()):
        return 0, 1  # ya existe un archivo con ese nombre: no se sobrescribe
    moved = skipped = 0
    for child in list(src.iterdir()):
        m, s = _merge_move(child, dst / child.name)
        moved += m
        skipped += s
    try:
        src.rmdir()  # solo si quedó vacía
    except OSError:
        pass
    return moved, skipped


def move_workspace(old: VaultLayout, new: VaultLayout) -> tuple[int, int]:
    """Lleva las carpetas de la app (clases, temas, profesores, transcripciones) a su nueva ubicación.

    Solo toca esas carpetas: si la ubicación anterior era una bóveda completa, las notas propias
    del usuario se quedan donde están. Devuelve (movidos, omitidos por conflicto).
    """
    pairs: list[tuple[Path, Path]] = []
    if old.root.resolve() != new.root.resolve():
        for a, b in (
            (old.classes, new.classes),
            (old.topics, new.topics),
            (old.teachers, new.teachers),
        ):
            pairs.append((old.root / a, new.root / b))
    if (
        old.transcripts_root is not None
        and new.transcripts_root is not None
        and old.transcripts_root.resolve() != new.transcripts_root.resolve()
    ):
        pairs.append((old.transcripts_root, new.transcripts_root))
    moved = skipped = 0
    for src, dst in pairs:
        if _within(dst, src):
            log.warning("No se mueve %s dentro de sí misma (%s)", src, dst)
            skipped += 1
            continue
        m, s = _merge_move(src, dst)
        moved += m
        skipped += s
    return moved, skipped
