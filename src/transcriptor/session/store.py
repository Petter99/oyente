"""Persistencia local de sesiones: data/sesiones/<id>/session.json + segmentos."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from transcriptor.asr.base import Segment
from transcriptor.fsutil import atomic_write_text
from transcriptor.paths import sessions_dir
from transcriptor.session.models import RecordingSession

log = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or sessions_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, session_id: str) -> Path:
        path = self.root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, session: RecordingSession) -> None:
        atomic_write_text(self.dir(session.id) / "session.json", session.model_dump_json(indent=2))

    def get(self, session_id: str) -> RecordingSession | None:
        path = self.root / session_id / "session.json"
        if not path.exists():
            return None
        return RecordingSession.model_validate_json(path.read_text(encoding="utf-8"))

    def all(self) -> list[RecordingSession]:
        sessions = []
        for path in self.root.glob("*/session.json"):
            try:
                sessions.append(
                    RecordingSession.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except Exception:
                log.exception("Sesión ilegible: %s", path)
        return sorted(sessions, key=lambda s: s.started_at, reverse=True)

    def delete(self, session_id: str) -> None:
        import shutil

        shutil.rmtree(self.root / session_id, ignore_errors=True)

    # -- segmentos ---------------------------------------------------------
    def save_segments(self, session_id: str, segments: list[Segment], kind: str = "final") -> Path:
        path = self.dir(session_id) / f"segmentos.{kind}.json"
        atomic_write_text(path, json.dumps([s.to_dict() for s in segments], ensure_ascii=False))
        return path

    def load_segments(self, session_id: str, kind: str = "final") -> list[Segment]:
        path = self.root / session_id / f"segmentos.{kind}.json"
        if not path.exists():
            return []
        return [Segment.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]

    def interrupted(self) -> list[RecordingSession]:
        """Sesiones que quedaron grabando (cierre abrupto) y deben recuperarse."""
        return [s for s in self.all() if s.status in ("recording", "finalizing")]
