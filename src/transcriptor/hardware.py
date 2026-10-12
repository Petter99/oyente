"""Detección de hardware para elegir motor y modelo de transcripción sin preguntar al usuario."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from typing import Literal

import psutil

Engine = Literal["mlx", "faster-whisper"]


@dataclass(frozen=True)
class Hardware:
    system: str  # "Darwin", "Windows", "Linux"
    machine: str  # "arm64", "AMD64", "x86_64"
    cpu_count: int
    ram_gb: float
    nvidia_gpu: bool
    nvidia_vram_gb: float
    # nvidia-smi solo prueba que hay GPU y driver; faster-whisper además necesita cuBLAS de CUDA 12.
    cuda_libs: bool = True

    @property
    def apple_silicon(self) -> bool:
        return self.system == "Darwin" and self.machine == "arm64"


@dataclass(frozen=True)
class AsrPlan:
    engine: Engine
    live_model: str
    final_model: str
    device: str  # "cpu", "cuda", "mlx"
    compute_type: str  # "int8", "float16", ...
    note: str


def _nvidia() -> tuple[bool, float]:
    if not shutil.which("nvidia-smi"):
        return False, 0.0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        vram = max(float(x) for x in out.split() if x.strip()) / 1024
        return True, vram
    except Exception:
        return False, 0.0


def _cuda_libs_available() -> bool:
    """¿Se puede cargar cuBLAS de CUDA 12? Sin ella faster-whisper falla al transcribir en GPU."""
    names = {"Windows": ["cublas64_12.dll"], "Linux": ["libcublas.so.12"]}.get(
        platform.system(), []
    )
    for name in names:
        try:
            ctypes.CDLL(name, winmode=0)  # winmode=0: busca también en PATH, como CTranslate2
            return True
        except OSError:
            continue
    return False


@cache
def detect() -> Hardware:
    has_gpu, vram = _nvidia()
    return Hardware(
        system=platform.system(),
        machine=platform.machine(),
        cpu_count=os.cpu_count() or 2,
        ram_gb=psutil.virtual_memory().total / 1024**3,
        nvidia_gpu=has_gpu,
        nvidia_vram_gb=vram,
        cuda_libs=_cuda_libs_available() if has_gpu else False,
    )


def mlx_available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
    except Exception:
        return False
    return True


def plan_asr(hw: Hardware, override: str = "auto", mlx_installed: bool | None = None) -> AsrPlan:
    """Elige motor y modelos. ``override`` permite fijar un modelo concreto desde Configuración."""
    if mlx_installed is None:
        mlx_installed = mlx_available()

    if hw.apple_silicon and mlx_installed:
        plan = AsrPlan(
            "mlx",
            "large-v3-turbo",
            "large-v3",
            "mlx",
            "float16",
            "Mac con Apple Silicon: motor MLX",
        )
    elif hw.nvidia_gpu and hw.cuda_libs and hw.nvidia_vram_gb >= 5.5:
        plan = AsrPlan(
            "faster-whisper",
            "large-v3-turbo",
            "large-v3",
            "cuda",
            "float16",
            "GPU NVIDIA: faster-whisper en CUDA",
        )
    elif hw.ram_gb >= 7.5 and hw.cpu_count >= 4:
        # Camino principal de la mayoría de portátiles Windows: CPU con int8. En CPU, turbo
        # transcribe ~1× tiempo real (la cola crece y el texto llega 30 s tarde) y cada llamada
        # a «small» cuesta ~2.4 s fijos: en vivo se usa «base» (~0.9 s) y la pasada final corrige
        # con el modelo grande.
        plan = AsrPlan(
            "faster-whisper",
            "base",
            "large-v3",
            "cpu",
            "int8",
            "CPU: faster-whisper int8",
        )
    else:
        plan = AsrPlan(
            "faster-whisper",
            "small",
            "medium",
            "cpu",
            "int8",
            "Equipo modesto: modelos small/medium",
        )

    if override != "auto":
        plan = AsrPlan(
            plan.engine,
            override,
            override,
            plan.device,
            plan.compute_type,
            f"Modelo fijado por el usuario: {override}",
        )
    return plan
