"""Detección de bóvedas de Obsidian y movimiento de la carpeta de trabajo."""

from __future__ import annotations

import json
from pathlib import Path

from transcriptor.links.obsidian_env import find_vault, is_registered, list_vaults
from transcriptor.notes.writer import VaultLayout
from transcriptor.workspace import default_transcripts_dir, move_workspace


def _config(tmp_path: Path, *vaults: Path) -> Path:
    cfg = tmp_path / "obsidian.json"
    cfg.write_text(
        json.dumps({"vaults": {f"id{i}": {"path": str(v), "ts": i} for i, v in enumerate(vaults)}}),
        encoding="utf-8",
    )
    return cfg


def test_list_vaults_skips_missing_and_orders_by_last_use(tmp_path: Path) -> None:
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    cfg = _config(tmp_path, a, b, tmp_path / "borrada")
    assert [v.name for v in list_vaults(cfg)] == ["B", "A"]
    assert is_registered(a, cfg) and not is_registered(tmp_path, cfg)


def test_list_vaults_without_obsidian_is_empty(tmp_path: Path) -> None:
    assert list_vaults(tmp_path / "no-existe.json") == []
    bad = tmp_path / "roto.json"
    bad.write_text("{no es json", encoding="utf-8")
    assert list_vaults(bad) == []


def test_find_vault_walks_up(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    work = vault / "Oyente" / "Clases"
    work.mkdir(parents=True)
    assert find_vault(work) == vault.resolve()
    assert find_vault(tmp_path / "suelta") is None


def test_default_transcripts_dir_is_outside_the_vault(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    work = vault / "Oyente"
    assert default_transcripts_dir(work, vault) == tmp_path / "Transcripciones"
    assert default_transcripts_dir(tmp_path / "Clases") == tmp_path / "Transcripciones"


def test_move_workspace_moves_only_app_folders(tmp_path: Path) -> None:
    old_root = tmp_path / "Vault"
    (old_root / "Clases" / "Cálculo").mkdir(parents=True)
    (old_root / "Clases" / "Cálculo" / "nota.md").write_text("x", encoding="utf-8")
    (old_root / "Temas").mkdir()
    (old_root / "Temas" / "t.md").write_text("t", encoding="utf-8")
    (old_root / "Diario").mkdir()
    (old_root / "Diario" / "hoy.md").write_text("mío", encoding="utf-8")
    new_root = old_root / "Oyente"
    old = VaultLayout(old_root)
    new = VaultLayout(new_root)
    moved, skipped = move_workspace(old, new)
    assert moved == 2 and skipped == 0
    assert (new_root / "Clases" / "Cálculo" / "nota.md").read_text(encoding="utf-8") == "x"
    assert (new_root / "Temas" / "t.md").exists()
    assert (old_root / "Diario" / "hoy.md").exists()  # lo del usuario no se toca


def test_move_workspace_never_overwrites(tmp_path: Path) -> None:
    old_root, new_root = tmp_path / "viejo", tmp_path / "nuevo"
    (old_root / "Clases").mkdir(parents=True)
    (old_root / "Clases" / "a.md").write_text("viejo", encoding="utf-8")
    (new_root / "Clases").mkdir(parents=True)
    (new_root / "Clases" / "a.md").write_text("nuevo", encoding="utf-8")
    moved, skipped = move_workspace(VaultLayout(old_root), VaultLayout(new_root))
    assert skipped == 1
    assert (new_root / "Clases" / "a.md").read_text(encoding="utf-8") == "nuevo"
    assert (old_root / "Clases" / "a.md").read_text(encoding="utf-8") == "viejo"
