"""Qué pasa con el audio al terminar la clase: formato (FLAC/Opus) y retención (siempre/N días/nunca)."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from transcriptor.audio.decode import encode_opus
from transcriptor.config.models import AudioFormat, RetentionConfig

log = logging.getLogger(__name__)

RETENTION_FILE = "retencion.json"


@dataclass
class RetentionResult:
    kept: list[Path]
    deleted: list[Path]
    expires_at: datetime | None


def finalize_audio(
    audio_files: list[Path],
    fmt: AudioFormat,
    retention: RetentionConfig,
    final_pass_ok: bool,
    now: datetime | None = None,
    dest_dir: Path | None = None,
) -> RetentionResult:
    """Aplica formato y retención tras la pasada final.

    Nunca borra el audio si la pasada final falló: se necesita para reintentar.
    """
    now = now or datetime.now()
    existing = [p for p in audio_files if p.exists()]
    if not existing:
        return RetentionResult([], [], None)
    folder = existing[0].parent

    if retention.mode == "never" and final_pass_ok:
        for p in existing:
            p.unlink(missing_ok=True)
        _write_meta(folder, None, deleted=True)
        return RetentionResult([], existing, None)

    kept: list[Path] = []
    for p in existing:
        target = p
        if fmt == "opus" and p.suffix.lower() == ".flac":
            try:
                target = encode_opus(p, p.with_suffix(".opus"))
                if final_pass_ok:
                    p.unlink(missing_ok=True)
                else:
                    target = p  # conservar FLAC para reintentar con máxima fidelidad
            except Exception:
                log.exception("No se pudo convertir a Opus; se conserva FLAC")
                target = p
        if dest_dir is not None and target.parent != dest_dir:
            dest_dir.mkdir(parents=True, exist_ok=True)
            moved = dest_dir / target.name
            shutil.move(str(target), moved)
            target = moved
        kept.append(target)

    expires = now + timedelta(days=retention.days) if retention.mode == "days" else None
    _write_meta(kept[0].parent, expires)
    return RetentionResult(kept, [], expires)


def _write_meta(folder: Path, expires: datetime | None, deleted: bool = False) -> None:
    meta = {"expires_at": expires.isoformat() if expires else None, "deleted": deleted}
    (folder / RETENTION_FILE).write_text(json.dumps(meta), encoding="utf-8")


def sweep_expired(root: Path, now: datetime | None = None) -> list[Path]:
    """Borra audios con retención vencida. Se ejecuta al iniciar la app."""
    now = now or datetime.now()
    removed: list[Path] = []
    for meta_file in root.rglob(RETENTION_FILE):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        expires = meta.get("expires_at")
        if expires and datetime.fromisoformat(expires) <= now:
            for audio in meta_file.parent.glob("*"):
                if audio.suffix.lower() in {".flac", ".opus", ".wav"}:
                    audio.unlink(missing_ok=True)
                    removed.append(audio)
            _write_meta(meta_file.parent, None, deleted=True)
    return removed


def keep_forever(folder: Path) -> None:
    _write_meta(folder, None)


def delete_now(folder: Path) -> list[Path]:
    removed = []
    for audio in folder.glob("*"):
        if audio.suffix.lower() in {".flac", ".opus", ".wav"}:
            audio.unlink(missing_ok=True)
            removed.append(audio)
    _write_meta(folder, None, deleted=True)
    return removed
