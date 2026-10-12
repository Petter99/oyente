"""Decodificación y codificación de audio con PyAV (incluye Opus y FLAC, sin ffmpeg externo)."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import soundfile as sf

from transcriptor.audio.types import SAMPLE_RATE, Samples, to_mono_16k


def decode_file(path: Path) -> Samples:
    """Lee cualquier formato de audio y devuelve 16 kHz mono float32."""
    if path.suffix.lower() in {".wav", ".flac"}:
        try:
            with sf.SoundFile(str(path)) as fh:
                # Un FLAC sin cerrar (cierre abrupto) no tiene la duración en la cabecera.
                if 0 < fh.frames < 2**40:
                    data = fh.read(dtype="float32", always_2d=False)
                    return to_mono_16k(data, int(fh.samplerate))
        except RuntimeError:
            pass
    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)


def encode_opus(src: Path, dst: Path, bitrate: int = 24_000) -> Path:
    """Convierte un archivo (FLAC) a Opus en contenedor Ogg."""
    samples = decode_file(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with av.open(str(tmp), mode="w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=48_000)
        stream.bit_rate = bitrate
        stream.layout = "mono"
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
        block = SAMPLE_RATE  # 1 s
        pts = 0
        for i in range(0, max(samples.size, 1), block):
            chunk = samples[i : i + block]
            if chunk.size == 0:
                break
            pcm = np.clip(chunk * 32767, -32768, 32767).astype(np.int16).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
            frame.sample_rate = SAMPLE_RATE
            frame.pts = pts
            pts += chunk.size
            for rf in resampler.resample(frame):
                for packet in stream.encode(rf):
                    out.mux(packet)
        for rf in resampler.resample(None):
            for packet in stream.encode(rf):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    tmp.replace(dst)
    return dst


# Tamaños aproximados por hora y canal (16 kHz mono), para mostrar al usuario.
MB_PER_HOUR = {"flac": 60.0, "opus": 11.0}


def estimate_mb(fmt: str, hours: float, channels: int = 2) -> float:
    return MB_PER_HOUR[fmt] * hours * channels
