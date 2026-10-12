"""Pruebas de la fase F1 (T1.1–T1.6, T1.8) y detección de hardware (T3.8)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest
import yaml

from transcriptor.branding import APP_ID, branding, load_branding
from transcriptor.config import AppConfig, ConfigStore, Course, Secret, SecretStore
from transcriptor.config.store import migrate
from transcriptor.filenames import safe_filename, slug
from transcriptor.fsutil import ConcurrentModificationError, atomic_write_text
from transcriptor.hardware import Hardware, plan_asr
from transcriptor.logging_setup import REDACTED, SecretFilter, redact

SRC = Path(__file__).resolve().parents[2] / "src" / "transcriptor"


def test_t1_1_config_roundtrip(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.yaml")
    cfg = AppConfig()
    cfg.courses.append(Course(name="Cálculo II", teacher="Ing. Pérez", glossary=["LIATE"]))
    cfg.audio.format = "flac"
    store.save(cfg)
    loaded = store.load()
    assert loaded == cfg
    assert "Cálculo II" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_t1_1_missing_config_uses_defaults(tmp_path: Path) -> None:
    cfg = ConfigStore(tmp_path / "no-existe.yaml").load()
    assert cfg.audio.default_source == "app"
    assert cfg.audio.format == "opus"
    assert cfg.audio.retention.mode == "days" and cfg.audio.retention.days == 30
    assert cfg.ai.provider == "none"


def test_t1_1_corrupt_config_is_backed_up(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("audio: [esto no es válido", encoding="utf-8")
    cfg = ConfigStore(path).load()
    assert cfg == AppConfig()
    assert (tmp_path / "config.yaml.danado").exists()


def test_t1_2_migration_v1_to_v2_keeps_user_settings() -> None:
    v1 = {
        "version": 1,
        "audio": {"save_audio": False, "format": "flac"},
        "courses": [{"name": "Física I"}],
    }
    cfg = AppConfig.model_validate(migrate(v1))
    assert cfg.audio.retention.mode == "never"
    assert cfg.audio.format == "flac"
    assert cfg.courses[0].name == "Física I"


def test_t1_3_secrets_never_in_config(tmp_path: Path) -> None:
    secrets = SecretStore()
    secrets.set(Secret.ANTHROPIC_API_KEY, "sk-ant-api03-SECRETO123456789")
    store = ConfigStore(tmp_path / "config.yaml")
    store.save(AppConfig())
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "sk-ant" not in text
    assert secrets.get(Secret.ANTHROPIC_API_KEY) == "sk-ant-api03-SECRETO123456789"
    secrets.delete(Secret.ANTHROPIC_API_KEY)
    assert not secrets.has(Secret.ANTHROPIC_API_KEY)


def test_t1_3_config_model_has_no_secret_fields() -> None:
    dumped = yaml.safe_dump(AppConfig().model_dump(mode="json"))
    assert not re.search(r"(?m)^\s*(\w*api_key|\w*_token|password|ics_url)\s*:", dumped)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("clave sk-ant-api03-abcdefghijklmnop usada", "abcdefghijklmnop"),
        ("google AIzaSyA1234567890abcdefghijklmnop", "1234567890abcdef"),
        ("token ya29.a0AfH6SMBxyz-123", "a0AfH6"),
        ("Authorization: Bearer abc.def.ghi", "abc.def"),
        ("calendario https://canvas.uni.edu/feeds/calendars/user_ABCDEF123.ics", "ABCDEF123"),
        ("webcal://moodle.uni.edu/calendar/export_execute.php?userid=3&authtoken=xyz", "authtoken"),
        ("hf hf_ABCDEFGHIJKLMNOPQRSTUV", "hf_ABC"),
    ],
)
def test_t1_4_log_filter_hides_secrets(text: str, secret: str) -> None:
    out = redact(text)
    assert REDACTED in out
    assert secret not in out


def test_t1_4_registered_secret_is_hidden_in_records() -> None:
    SecretStore().set(Secret.GEMINI_API_KEY, "valor-muy-secreto-42")
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "usando %s", ("valor-muy-secreto-42",), None
    )
    SecretFilter().filter(record)
    assert "valor-muy-secreto-42" not in record.getMessage()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Clase: repaso?", "Clase - repaso"),
        ("Integrales por partes", "Integrales por partes"),
        ("Álgebra y Ñandúes", "Álgebra y Ñandúes"),
        ('a<b>c"d|e*f', "a b c d e f"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("termina en punto. ", "termina en punto"),
        ("[[enlace]] #tag ^bloque", "enlace tag bloque"),
        ("   ", "sin título"),
    ],
)
def test_t1_5_safe_filenames(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected


def test_t1_5_safe_filename_length_and_slug() -> None:
    assert len(safe_filename("x" * 500)) <= 120
    assert slug("Cálculo II") == "calculo-ii"


def test_t1_6_paths_with_accents_and_long_names(tmp_path: Path) -> None:
    deep = tmp_path / "Mis Clases ñ" / ("Ecuaciones Diferenciales Ordinarias II " * 3).strip()
    target = deep / f"{safe_filename('2026-10-12 Transformada de Laplace: aplicaciones' * 2)}.md"
    atomic_write_text(target, "contenido con tildes: áéíóú ñ")
    assert target.read_text(encoding="utf-8") == "contenido con tildes: áéíóú ñ"


def test_atomic_write_detects_concurrent_change(tmp_path: Path) -> None:
    path = tmp_path / "nota.md"
    mtime = atomic_write_text(path, "uno")
    path.write_text("editado por el usuario", encoding="utf-8")
    os.utime(path, (mtime + 5, mtime + 5))
    with pytest.raises(ConcurrentModificationError):
        atomic_write_text(path, "dos", expected_mtime=mtime)
    assert path.read_text(encoding="utf-8") == "editado por el usuario"


def test_t1_8_brand_name_only_in_branding_file() -> None:
    name = branding().nombre
    offenders = []
    for file in SRC.rglob("*"):
        if file.is_dir() or file.suffix in {".pyc", ".png", ".ico", ".icns"}:
            continue
        if file.name == "branding.toml":
            continue
        if name in file.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(file.relative_to(SRC)))
    assert offenders == [], f"'{name}' escrito fuera de branding.toml: {offenders}"


def test_t1_8_renaming_changes_visible_strings() -> None:
    b = load_branding(
        {
            "nombre": "Otra",
            "nombre_corto": "Otra",
            "eslogan": "",
            "sitio_web": "",
            "soporte": "",
            "calendario_google": "Clases – {nombre}",
            "lista_tasks": "Tareas de clase",
            "ejecutable": "{nombre}",
        }
    )
    assert b.calendario_google == "Clases – Otra"
    assert b.ejecutable == "Otra"
    assert b.fmt("Bienvenido a {nombre}") == "Bienvenido a Otra"
    assert APP_ID == "transcriptor"


@pytest.mark.parametrize(
    ("hw", "engine", "device", "live"),
    [
        (Hardware("Darwin", "arm64", 10, 16, False, 0), "mlx", "mlx", "large-v3-turbo"),
        (Hardware("Windows", "AMD64", 8, 16, True, 8), "faster-whisper", "cuda", "large-v3-turbo"),
        (Hardware("Windows", "AMD64", 8, 16, False, 0), "faster-whisper", "cpu", "base"),
        (Hardware("Windows", "AMD64", 2, 4, False, 0), "faster-whisper", "cpu", "small"),
    ],
)
def test_t3_8_hardware_plan(hw: Hardware, engine: str, device: str, live: str) -> None:
    plan = plan_asr(hw, mlx_installed=True)
    assert (plan.engine, plan.device, plan.live_model) == (engine, device, live)


def test_hardware_plan_mac_without_mlx_falls_back() -> None:
    plan = plan_asr(Hardware("Darwin", "arm64", 10, 16, False, 0), mlx_installed=False)
    assert plan.engine == "faster-whisper"


def test_hardware_plan_cuda_without_libs_uses_cpu() -> None:
    # GPU NVIDIA detectada pero sin cuBLAS (cublas64_12.dll): CUDA fallaría al transcribir.
    plan = plan_asr(Hardware("Windows", "AMD64", 8, 16, True, 8, cuda_libs=False))
    assert (plan.device, plan.compute_type) == ("cpu", "int8")
    # En CPU el modelo en vivo debe ir más rápido que el audio (turbo no lo logra).
    assert plan.live_model == "base"
    assert plan.final_model == "large-v3"
