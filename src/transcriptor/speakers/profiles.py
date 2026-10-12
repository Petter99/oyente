"""Perfil de voz del profesor por curso (vector numérico local, nunca en la carpeta compartida)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from transcriptor.paths import profiles_dir


class SpeakerProfileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or profiles_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, course_id: str, embedder: str) -> tuple[Path, Path]:
        base = self.root / f"{course_id}.{embedder}"
        return base.with_suffix(".npy"), base.with_suffix(".json")

    def get(self, course_id: str, embedder: str) -> np.ndarray | None:
        vec, _ = self._paths(course_id, embedder)
        return np.load(vec) if vec.exists() else None

    def count(self, course_id: str, embedder: str) -> int:
        _, meta = self._paths(course_id, embedder)
        if not meta.exists():
            return 0
        return int(json.loads(meta.read_text(encoding="utf-8")).get("n", 0))

    def confirmed(self, course_id: str, embedder: str) -> bool:
        _, meta = self._paths(course_id, embedder)
        if not meta.exists():
            return False
        return bool(json.loads(meta.read_text(encoding="utf-8")).get("confirmed", False))

    def update(
        self,
        course_id: str,
        embedder: str,
        embedding: np.ndarray,
        weight: int = 1,
        confirmed: bool | None = None,
    ) -> np.ndarray:
        """Media móvil ponderada: el perfil mejora con cada clase."""
        vec_path, meta_path = self._paths(course_id, embedder)
        current = self.get(course_id, embedder)
        n = self.count(course_id, embedder)
        if current is None:
            new = embedding
        else:
            n_eff = min(n, 50)  # se adapta a cambios (micrófono nuevo, etc.)
            new = (current * n_eff + embedding * weight) / (n_eff + weight)
        new = new / max(float(np.linalg.norm(new)), 1e-9)
        np.save(vec_path, new.astype(np.float32))
        prev_confirmed = self.confirmed(course_id, embedder)
        meta = {"n": n + weight, "confirmed": prev_confirmed if confirmed is None else confirmed}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return new

    def replace(
        self, course_id: str, embedder: str, embedding: np.ndarray, confirmed: bool = True
    ) -> None:
        self.delete(course_id)
        self.update(course_id, embedder, embedding, confirmed=confirmed)

    def delete(self, course_id: str) -> None:
        for p in self.root.glob(f"{course_id}.*"):
            p.unlink(missing_ok=True)

    def delete_all(self) -> None:
        for p in self.root.glob("*"):
            if p.suffix in {".npy", ".json"}:
                p.unlink(missing_ok=True)
