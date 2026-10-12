"""Grabación y transcripción en vivo.

Hilos:
- uno por fuente: lee audio → FLAC por canal + segmentador (VAD) → cola de frases;
- un trabajador de transcripción: frase → hablante → Whisper → .txt en vivo + eventos;
- un hilo de texto provisional: re-transcribe la frase en curso cada ~0.5 s (evento «partial»);
- un reloj: rellena con silencio los canales pausados o perdidos para mantener la alineación.

El .txt en vivo se escribe con flush en cada frase: si la app se cierra, no se pierde lo dicho.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import numpy as np

from transcriptor.asr.base import Segment, TranscribeOptions, Transcriber, format_ts
from transcriptor.audio.recorder import ChannelRecorder
from transcriptor.audio.sources.base import AudioSource
from transcriptor.audio.types import SAMPLE_RATE, Channel, Utterance
from transcriptor.audio.vad import SpeechDetector, StreamingSegmenter
from transcriptor.events import EventBus
from transcriptor.session.models import Marker, RecordingSession
from transcriptor.speakers.identifier import SpeakerIdentifier

log = logging.getLogger(__name__)

_STOP = object()
PARTIAL_EVERY_S = 0.5  # cada cuánto se refresca el texto provisional
MIN_PARTIAL_S = 0.6  # no se transcribe menos audio que esto
MIN_NEW_AUDIO_S = 0.4  # ni se repite sin audio nuevo


@dataclass
class _ChannelState:
    source: AudioSource
    recorder: ChannelRecorder
    segmenter: StreamingSegmenter
    thread: threading.Thread | None = None
    embeddings: list[tuple[float, float, np.ndarray]] = field(default_factory=list)


def format_line(seg: Segment) -> str:
    speaker = f" {seg.speaker}:" if seg.speaker else ""
    return f"[{format_ts(seg.start)}]{speaker} {seg.text}\n"


class LiveRecorder:
    def __init__(
        self,
        session: RecordingSession,
        sources: list[AudioSource],
        transcriber: Transcriber,
        identifier: SpeakerIdentifier | None,
        detector_factory: Callable[[], SpeechDetector],
        opts: TranscribeOptions,
        work_dir: Path,
        live_txt: Path,
        bus: EventBus,
    ) -> None:
        self.session = session
        self.transcriber = transcriber
        self.identifier = identifier
        self.opts = opts
        self.work_dir = work_dir
        self.live_txt = live_txt
        self.bus = bus
        self.segments: list[Segment] = []
        self._utts: queue.Queue[object] = queue.Queue()
        self._paused = threading.Event()
        self._stopping = threading.Event()
        self._t0 = 0.0
        self._paused_at: float | None = None
        self._channels: dict[Channel, _ChannelState] = {}
        self._txt: TextIO | None = None
        self._worker: threading.Thread | None = None
        self._clock: threading.Thread | None = None
        self._partial: threading.Thread | None = None
        self._asr_lock = (
            threading.Lock()
        )  # un solo Whisper a la vez; el texto final tiene prioridad
        self._lock = threading.Lock()
        self._speaker_pool = ThreadPoolExecutor(1, thread_name_prefix="hablante")
        for src in sources:
            channel = src.info.channel
            self._channels[channel] = _ChannelState(
                source=src,
                recorder=ChannelRecorder(work_dir / f"{channel}.flac", channel),
                segmenter=StreamingSegmenter(channel, detector_factory()),
            )
            src.on_lost(self._on_lost)

    # ------------------------------------------------------------------ control
    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._t0 else 0.0

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def audio_files(self) -> list[Path]:
        return [c.recorder.path for c in self._channels.values()]

    def levels(self) -> dict[str, float]:
        return {ch: st.source.level_db for ch, st in self._channels.items()}

    def start(self) -> None:
        self.live_txt.parent.mkdir(parents=True, exist_ok=True)
        self._txt = self.live_txt.open("a", encoding="utf-8", newline="\n")
        self._t0 = time.monotonic()
        started: list[AudioSource] = []
        try:
            for state in self._channels.values():
                state.source.start()
                started.append(state.source)
        except Exception:
            for src in started:
                src.stop()
            raise
        for channel, state in self._channels.items():
            state.thread = threading.Thread(
                target=self._read_loop, args=(channel,), name=f"audio-{channel}", daemon=True
            )
            state.thread.start()
        self._worker = threading.Thread(target=self._asr_loop, name="asr-live", daemon=True)
        self._worker.start()
        self._clock = threading.Thread(target=self._clock_loop, name="clock", daemon=True)
        self._clock.start()
        self._partial = threading.Thread(target=self._partial_loop, name="parcial", daemon=True)
        self._partial.start()
        self.bus.emit("recording_started", session_id=self.session.id)

    def pause(self) -> None:
        self._paused.set()
        self.bus.emit("recording_paused", t=self.elapsed)

    def resume(self) -> None:
        self._paused.clear()
        self.bus.emit("recording_resumed", t=self.elapsed)

    def mark(self, label: str = "Momento marcado") -> Marker:
        marker = Marker(t=self.elapsed, label=label)
        self.session.markers.append(marker)
        self.bus.emit("marker", t=marker.t, label=label)
        return marker

    def replace_source(self, channel: Channel, new_source: AudioSource) -> None:
        """Cambia la fuente de un canal sin cortar la sesión (p. ej. se cerró la pestaña)."""
        state = self._channels[channel]
        old = state.source
        old.stop()
        state.source = new_source
        new_source.on_lost(self._on_lost)
        new_source.start()
        if state.thread is None or not state.thread.is_alive():
            state.thread = threading.Thread(
                target=self._read_loop, args=(channel,), name=f"audio-{channel}", daemon=True
            )
            state.thread.start()
        self.session.source_label = new_source.info.label
        self.bus.emit("source_changed", channel=channel, label=new_source.info.label)

    def stop(self) -> list[Segment]:
        self._stopping.set()
        for state in self._channels.values():
            state.source.stop()
        for state in self._channels.values():
            if state.thread:
                state.thread.join(timeout=5)
        # Frases pendientes en los segmentadores.
        for state in self._channels.values():
            for utt in state.segmenter.flush():
                self._utts.put(utt)
        self._utts.put(_STOP)
        if self._worker:
            self._worker.join()
        if self._clock:
            self._clock.join(timeout=2)
        if self._partial:
            self._partial.join(timeout=5)
        end = self.elapsed
        for state in self._channels.values():
            state.recorder.pad_to(end)
            state.recorder.close()
        if self._txt:
            self._txt.close()
        self._speaker_pool.shutdown(wait=False)
        self.bus.emit("recording_stopped", session_id=self.session.id, duration=end)
        return self.segments

    def _on_lost(self, source: AudioSource, reason: str) -> None:
        # La sesión sigue: el reloj rellena con silencio y la interfaz ofrece cambiar de fuente.
        self.bus.emit(
            "source_lost", channel=source.info.channel, label=source.info.label, reason=reason
        )

    # ------------------------------------------------------------------ hilos
    def _read_loop(self, channel: Channel) -> None:
        state = self._channels[channel]
        src = state.source
        while state.source is src:
            block = src.read(timeout=0.05 if self._stopping.is_set() else 0.2)
            if block is None:
                # Al detener, se termina de vaciar la cola antes de salir.
                if self._stopping.is_set() or not src.running:
                    break
                continue
            if self._paused.is_set():
                # En pausa se graba silencio para mantener alineados los canales.
                state.recorder.write(np.zeros_like(block))
                continue
            state.recorder.write(block)
            for utt in state.segmenter.feed(block):
                self._utts.put(utt)

    def _clock_loop(self) -> None:
        while not self._stopping.wait(1.0):
            now = self.elapsed
            for state in self._channels.values():
                if state.source.lost or not state.source.running:
                    state.recorder.pad_to(now)
            self.bus.emit("tick", t=now, levels=self.levels())

    def _partial_loop(self) -> None:
        """Texto provisional: lo que va de la frase en curso, antes de que se cierre."""
        last: dict[Channel, int] = {}  # muestras ya transcritas por canal
        while not self._stopping.wait(PARTIAL_EVERY_S):
            if self._paused.is_set() or not self._utts.empty():
                continue  # el texto definitivo pendiente va primero
            for channel, state in self._channels.items():
                try:
                    start, samples = state.segmenter.peek()
                    if samples.size < MIN_PARTIAL_S * SAMPLE_RATE:
                        last.pop(channel, None)
                        continue
                    done = last.get(channel, 0)
                    if samples.size < done:
                        done = 0  # la frase anterior se cerró: empieza otra
                    if samples.size - done < MIN_NEW_AUDIO_S * SAMPLE_RATE:
                        continue
                    if not self._asr_lock.acquire(blocking=False):
                        continue  # Whisper ocupado con una frase definitiva
                    try:
                        segs = self.transcriber.transcribe(samples, start, self.opts)
                    finally:
                        self._asr_lock.release()
                    last[channel] = samples.size
                    text = " ".join(s.text for s in segs).strip()
                    if text and not self._stopping.is_set():
                        self.bus.emit(
                            "partial",
                            channel=channel,
                            start=start,
                            end=start + samples.size / SAMPLE_RATE,
                            text=text,
                        )
                except Exception:
                    log.exception("Error en el texto provisional")

    def _asr_loop(self) -> None:
        while True:
            item = self._utts.get()
            if item is _STOP:
                break
            assert isinstance(item, Utterance)
            try:
                self._process(item)
            except Exception as exc:
                log.exception("Error transcribiendo una frase")
                self.bus.emit("error", message=f"Error de transcripción: {exc}")
            self.bus.emit("queue", pending=self._utts.qsize())

    def _process(self, utt: Utterance) -> None:
        # El hablante se identifica en paralelo con Whisper: no suma a la latencia del texto.
        who = (
            self._speaker_pool.submit(self.identifier.label_live, utt.channel, utt.samples)
            if self.identifier is not None
            else None
        )
        with self._asr_lock:
            segments = self.transcriber.transcribe(utt.samples, utt.start, self.opts)
        speaker = "Yo" if utt.channel == "mic" else ""
        if who is not None:
            try:
                speaker, emb = who.result()
                if emb is not None:
                    self._channels[utt.channel].embeddings.append((utt.start, utt.end, emb))
            except Exception:
                log.exception("No se pudo identificar al hablante; se muestra el texto igual")
        for seg in segments:
            seg.channel = utt.channel
            seg.speaker = speaker
            with self._lock:
                self.segments.append(seg)
                if self._txt:
                    self._txt.write(format_line(seg))
                    self._txt.flush()
            self.bus.emit("segment", segment=seg)
