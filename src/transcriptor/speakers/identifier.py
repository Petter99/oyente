"""Identificación del profesor (siempre activa).

- Canal del micrófono → «Yo».
- Canal de la clase → huella de voz comparada con el perfil del profesor del curso.
- Pasada final: agrupamiento de huellas; el grupo que coincide con el perfil (o el que
  más habla si no hay perfil) es el profesor. Los demás: «Participante 1, 2…».
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from transcriptor.asr.base import Segment
from transcriptor.audio.types import SAMPLE_RATE, Samples
from transcriptor.speakers.embedder import SpeakerEmbedder, cosine
from transcriptor.speakers.profiles import SpeakerProfileStore

TEACHER = "Profesor"
ME = "Yo"
PARTICIPANT = "Participante"


@dataclass
class Cluster:
    centroid: np.ndarray
    seconds: float = 0.0
    members: list[int] = field(default_factory=list)

    def add(self, emb: np.ndarray, seconds: float, idx: int) -> None:
        total = self.seconds + seconds
        self.centroid = (self.centroid * self.seconds + emb * seconds) / max(total, 1e-9)
        self.centroid /= max(float(np.linalg.norm(self.centroid)), 1e-9)
        self.seconds = total
        self.members.append(idx)


@dataclass
class FinalLabels:
    labels: list[str]
    teacher_cluster: int | None
    needs_confirmation: bool
    # Por grupo: (índice de segmento de ejemplo, segundos hablados) para «¿Es este el profesor?»
    clusters: list[tuple[int, float]]
    teacher_embedding: np.ndarray | None
    # Voz de cada segmento (índice de grupo; None = micrófono o sin huella) y huella de cada grupo.
    # Varias voces pueden mostrarse con la misma etiqueta: esto es lo que las distingue.
    assignment: list[int | None] = field(default_factory=list)
    centroids: list[np.ndarray] = field(default_factory=list)


class SpeakerIdentifier:
    def __init__(
        self,
        embedder: SpeakerEmbedder,
        profiles: SpeakerProfileStore,
        course_id: str,
        threshold: float | None = None,
        distinguish_participants: bool = True,
        cluster_threshold: float | None = None,
    ) -> None:
        self.embedder = embedder
        self.profiles = profiles
        self.course_id = course_id
        self.threshold = threshold if threshold is not None else embedder.default_threshold
        self.distinguish = distinguish_participants
        self.cluster_threshold = (
            cluster_threshold if cluster_threshold is not None else self.threshold
        )
        self.profile = profiles.get(course_id, embedder.name)
        self._live_clusters: list[Cluster] = []

    # ------------------------------------------------------------------ en vivo
    def label_live(self, channel: str, samples: Samples) -> tuple[str, np.ndarray | None]:
        if channel == "mic":
            return ME, None
        emb = self.embedder.embed(samples)
        if emb is None:
            return self._guess_short(), None
        seconds = samples.size / SAMPLE_RATE
        if self.profile is not None:
            return (TEACHER if cosine(emb, self.profile) >= self.threshold else PARTICIPANT), emb
        # Sin perfil: grupos en línea; el que más habla hasta ahora es el profesor.
        best, score = self._nearest(self._live_clusters, emb)
        if best is None or score < self.cluster_threshold:
            self._live_clusters.append(Cluster(emb.copy()))
            best = len(self._live_clusters) - 1
        self._live_clusters[best].add(emb, seconds, -1)
        top = max(range(len(self._live_clusters)), key=lambda i: self._live_clusters[i].seconds)
        return (TEACHER if best == top else PARTICIPANT), emb

    def _guess_short(self) -> str:
        # Frases muy cortas: se asume el hablante dominante (casi siempre el profesor).
        return TEACHER

    @staticmethod
    def _nearest(clusters: list[Cluster], emb: np.ndarray) -> tuple[int | None, float]:
        if not clusters:
            return None, -1.0
        scores = [cosine(c.centroid, emb) for c in clusters]
        i = int(np.argmax(scores))
        return i, scores[i]

    # ------------------------------------------------------------------ pasada final
    def label_final(
        self, segments: list[Segment], embeddings: list[np.ndarray | None]
    ) -> FinalLabels:
        clusters: list[Cluster] = []
        assignment: list[int | None] = []
        for idx, (seg, emb) in enumerate(zip(segments, embeddings, strict=True)):
            if seg.channel == "mic" or emb is None:
                assignment.append(None)
                continue
            best, score = self._nearest(clusters, emb)
            if best is None or score < self.cluster_threshold:
                clusters.append(Cluster(emb.copy()))
                best = len(clusters) - 1
            clusters[best].add(emb, seg.end - seg.start, idx)
            assignment.append(best)

        clusters, assignment = self._merge_close(clusters, assignment)

        teacher: int | None = None
        needs_confirmation = False
        if clusters:
            if self.profile is not None:
                scores = [cosine(c.centroid, self.profile) for c in clusters]
                best = int(np.argmax(scores))
                if scores[best] >= self.threshold:
                    teacher = best
            if teacher is None:
                teacher = max(range(len(clusters)), key=lambda i: clusters[i].seconds)
                needs_confirmation = not self.profiles.confirmed(self.course_id, self.embedder.name)

        order = sorted(
            (i for i in range(len(clusters)) if i != teacher),
            key=lambda i: min(clusters[i].members),
        )
        participant_name = {
            c: f"{PARTICIPANT} {n}" if self.distinguish else PARTICIPANT
            for n, c in enumerate(order, 1)
        }
        labels = []
        for seg, a in zip(segments, assignment, strict=True):
            if seg.channel == "mic":
                labels.append(ME)
            elif a is None:
                labels.append(TEACHER if teacher is not None else PARTICIPANT)
            else:
                labels.append(TEACHER if a == teacher else participant_name[a])
        summary = [(c.members[0], c.seconds) for c in clusters]
        return FinalLabels(
            labels,
            teacher,
            needs_confirmation,
            summary,
            clusters[teacher].centroid if teacher is not None else None,
            assignment,
            [c.centroid for c in clusters],
        )

    def _merge_close(
        self, clusters: list[Cluster], assignment: list[int | None]
    ) -> tuple[list[Cluster], list[int | None]]:
        merged = True
        while merged and len(clusters) > 1:
            merged = False
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    if cosine(clusters[i].centroid, clusters[j].centroid) >= self.cluster_threshold:
                        ci, cj = clusters[i], clusters[j]
                        for m in cj.members:
                            ci.members.append(m)
                        total = ci.seconds + cj.seconds
                        ci.centroid = (ci.centroid * ci.seconds + cj.centroid * cj.seconds) / max(
                            total, 1e-9
                        )
                        ci.centroid /= max(float(np.linalg.norm(ci.centroid)), 1e-9)
                        ci.seconds = total
                        del clusters[j]
                        assignment = [
                            i if a == j else (a - 1 if a is not None and a > j else a)
                            for a in assignment
                        ]
                        merged = True
                        break
                if merged:
                    break
        return clusters, assignment

    # ------------------------------------------------------------------ aprendizaje
    def learn(self, result: FinalLabels) -> None:
        if result.teacher_embedding is not None and not result.needs_confirmation:
            self.profiles.update(self.course_id, self.embedder.name, result.teacher_embedding)

    def confirm_teacher(self, embedding: np.ndarray) -> None:
        """El usuario confirmó (o corrigió) cuál es la voz del profesor."""
        self.profiles.replace(self.course_id, self.embedder.name, embedding, confirmed=True)
        self.profile = self.profiles.get(self.course_id, self.embedder.name)
