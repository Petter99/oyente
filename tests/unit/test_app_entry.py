"""Arranque del ejecutable: sin consola (Windows) no hay sys.stdout/sys.stderr."""

from __future__ import annotations

import os
import sys

import pytest
from tqdm import tqdm

from transcriptor.app import ensure_std_streams


def test_windowed_exe_without_std_streams_can_show_download_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.delenv("HF_HUB_DISABLE_PROGRESS_BARS", raising=False)

    ensure_std_streams()

    assert sys.stdout is not None and sys.stderr is not None
    for _ in tqdm(range(3)):  # lo que hace huggingface_hub al descargar el modelo
        pass
    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"


def test_existing_streams_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = sys.stdout, sys.stderr
    ensure_std_streams()
    assert sys.stdout is out and sys.stderr is err
