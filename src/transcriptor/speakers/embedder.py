"""Huellas de voz (embeddings de hablante).

- ``EcapaEmbedder``: SpeechBrain ECAPA-TDNN (extra ``[speakers]``), la opción precisa.
- ``SpectralEmbedder``: estadísticas de MFCC con numpy; sin dependencias, menos preciso.
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any, Protocol

import numpy as np

from transcriptor.audio.types import SAMPLE_RATE, Samples
from transcriptor.paths import models_dir

log = logging.getLogger(__name__)

Embedding = np.ndarray


class SpeakerEmbedder(Protocol):
    name: str
    default_threshold: float

    def embed(self, samples: Samples) -> Embedding | None:
        """Vector normalizado (norma 1) o None si el audio es demasiado corto."""
        ...


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


@cache
def _mel_filterbank(n_fft: int, n_mels: int, rate: int) -> np.ndarray:
    def hz_to_mel(f: np.ndarray | float) -> np.ndarray:
        return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)

    def mel_to_hz(m: np.ndarray) -> np.ndarray:
        return 700.0 * (10 ** (m / 2595.0) - 1.0)

    mels = np.linspace(hz_to_mel(60.0), hz_to_mel(rate / 2 - 200), n_mels + 2)
    bins = np.floor((n_fft + 1) * mel_to_hz(mels) / rate).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        left, center, right = bins[m - 1], bins[m], bins[m + 1]
        for k in range(left, center):
            fb[m - 1, k] = (k - left) / max(center - left, 1)
        for k in range(center, right):
            fb[m - 1, k] = (right - k) / max(right - center, 1)
    return fb


class SpectralEmbedder:
    name = "spectral"
    min_seconds = 0.8
    # Umbral de similitud coseno para «misma persona» (medido con voces sintéticas).
    default_threshold = 0.96

    def __init__(self, n_mfcc: int = 20, n_mels: int = 40) -> None:
        self.n_mfcc = n_mfcc
        self.n_mels = n_mels
        self.n_fft = 512
        self.hop = SAMPLE_RATE // 100
        self.win = SAMPLE_RATE * 25 // 1000

    def _mfcc(self, x: np.ndarray) -> np.ndarray:
        x = np.append(x[0], x[1:] - 0.97 * x[:-1])
        n = 1 + (x.size - self.win) // self.hop
        idx = np.arange(self.win)[None, :] + self.hop * np.arange(n)[:, None]
        frames = x[idx] * np.hamming(self.win)
        spec = np.abs(np.fft.rfft(frames, self.n_fft)) ** 2
        energy = spec.sum(axis=1)
        keep = energy > np.percentile(energy, 30)  # solo tramas con voz
        mel = np.log(spec[keep] @ _mel_filterbank(self.n_fft, self.n_mels, SAMPLE_RATE).T + 1e-10)
        k = np.arange(self.n_mels)
        dct = np.cos(np.pi / self.n_mels * (k[None, :] + 0.5) * np.arange(self.n_mfcc)[:, None])
        return mel @ dct.T

    def embed(self, samples: Samples) -> Embedding | None:
        if samples.size < self.min_seconds * SAMPLE_RATE:
            return None
        mfcc = self._mfcc(samples.astype(np.float64))
        if mfcc.shape[0] < 10:
            return None
        mfcc = mfcc[:, 1:]  # sin C0 (volumen); las medias conservan el timbre
        feat = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)])
        return _normalize(feat.astype(np.float32))


def kaldi_fbank(samples: Samples, n_mels: int = 80) -> np.ndarray:
    """Fbank de 80 bandas compatible con Kaldi/torchaudio (entrada de los modelos WeSpeaker)."""
    x = samples.astype(np.float64) * 32768.0
    win, hop, n_fft = 400, 160, 512
    n = 1 + (x.size - win) // hop
    if n <= 0:
        return np.zeros((0, n_mels), dtype=np.float32)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * (1 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(win) / (win - 1))) ** 0.85
    spec = np.abs(np.fft.rfft(frames * window, n_fft)) ** 2
    spec = spec[:, : n_fft // 2]
    mel = _kaldi_mel_banks(n_mels, n_fft, SAMPLE_RATE)
    feats = np.log(np.maximum(spec @ mel.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


@cache
def _kaldi_mel_banks(n_mels: int, n_fft: int, rate: int, low: float = 20.0) -> np.ndarray:
    def mel(f: np.ndarray | float) -> np.ndarray:
        return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)

    high = rate / 2
    n_bins = n_fft // 2
    fft_mel = mel(np.arange(n_bins) * rate / n_fft)
    lo, hi = mel(low), mel(high)
    delta = (hi - lo) / (n_mels + 1)
    banks = np.zeros((n_mels, n_bins))
    for m in range(n_mels):
        left, center, right = lo + m * delta, lo + (m + 1) * delta, lo + (m + 2) * delta
        up = (fft_mel - left) / (center - left)
        down = (right - fft_mel) / (right - center)
        banks[m] = np.maximum(0.0, np.minimum(up, down))
    return banks


class WespeakerEmbedder:
    """ResNet34 de WeSpeaker (VoxCeleb, CC-BY-4.0) en ONNX: preciso y sin PyTorch."""

    name = "wespeaker"
    min_seconds = 0.8
    # Medido con voces distintas: misma voz ≥ 0,78; voces distintas ≤ 0,45 (scripts/calibrate_speakers.py).
    default_threshold = 0.55
    REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
    FILE = "voxceleb_resnet34_LM.onnx"

    def __init__(self, model_path: str | None = None) -> None:
        import onnxruntime as ort

        if model_path is None:
            from huggingface_hub import hf_hub_download

            model_path = hf_hub_download(self.REPO, self.FILE, cache_dir=str(models_dir() / "voz"))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self._session = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])

    def embed(self, samples: Samples) -> Embedding | None:
        if samples.size < self.min_seconds * SAMPLE_RATE:
            return None
        feats = kaldi_fbank(samples)
        if feats.shape[0] < 50:
            return None
        out = self._session.run(None, {"feats": feats[None, :, :]})[0][0]
        return _normalize(out.astype(np.float32))


class EcapaEmbedder:
    name = "ecapa"
    min_seconds = 0.8
    default_threshold = 0.45

    def __init__(self) -> None:
        from speechbrain.inference.speaker import EncoderClassifier

        self._model: Any = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(models_dir() / "ecapa"),
            run_opts={"device": "cpu"},
        )

    def embed(self, samples: Samples) -> Embedding | None:
        import torch

        if samples.size < self.min_seconds * SAMPLE_RATE:
            return None
        with torch.no_grad():
            emb = self._model.encode_batch(torch.from_numpy(samples).unsqueeze(0))
        return _normalize(emb.squeeze().cpu().numpy().astype(np.float32))


def default_embedder() -> SpeakerEmbedder:
    """WeSpeaker (ONNX) → ECAPA (si está instalado PyTorch) → extractor espectral local."""
    for factory in (WespeakerEmbedder, EcapaEmbedder):
        try:
            return factory()
        except Exception as exc:
            log.info("Extractor de voz %s no disponible: %s", factory.__name__, exc)
    return SpectralEmbedder()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(_normalize(a), _normalize(b)))
