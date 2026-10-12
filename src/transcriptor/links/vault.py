"""Lectura de la bóveda de Obsidian e indexado incremental (solo lectura de notas del usuario)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

from transcriptor.notes.markdown import (
    get_block,
    parse_frontmatter,
    split_frontmatter,
    strip_managed,
)
from transcriptor.notes.writer import TRANSCRIPT_DIR
from transcriptor.search.index import SearchIndex

log = logging.getLogger(__name__)

OWN_KINDS = {"clase", "tema", "profesor", "curso", "indice"}
SKIP_DIRS = {".obsidian", ".trash", ".git", TRANSCRIPT_DIR}
TRANSCRIPT_LINE = re.compile(
    r"^\*\*\[(\d\d):(\d\d):(\d\d)\](?: ([^:*]+):)?\*\* (.*?)(?: \^[\w-]+)?$"
)
LINK_ALIAS = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def iter_markdown(root: Path, excluded: list[str]) -> Iterator[Path]:
    excluded_norm = [e.strip("/").casefold() for e in excluded if e.strip()]
    for path in root.rglob("*.md"):
        rel = path.relative_to(root)
        parts = rel.parts[:-1]
        if any(p in SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        rel_str = rel.as_posix().casefold()
        if any(rel_str == e or rel_str.startswith(e + "/") for e in excluded_norm):
            continue
        if path.name.startswith(".~oy-"):
            continue
        yield path


def link_text(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    m = LINK_ALIAS.search(value)
    if not m:
        return value
    return m.group(2) or m.group(1).split("/")[-1]


def _paragraph_chunks(
    body: str, size: int = 700
) -> list[tuple[str, float | None, str | None, str]]:
    chunks: list[tuple[str, float | None, str | None, str]] = []
    buf = ""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) > size and buf:
            chunks.append(("nota", None, None, buf))
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(("nota", None, None, buf))
    return chunks[:200]


def class_chunks(text: str) -> tuple[str, list[tuple[str, float | None, str | None, str]]]:
    """Resumen y fragmentos (resumen, puntos, transcripción en grupos de ~4 líneas)."""
    chunks: list[tuple[str, float | None, str | None, str]] = []
    summary = (get_block(text, "resumen") or "").replace("> [!summary] Resumen", "")
    summary = re.sub(r"^>\s?", "", summary, flags=re.M).strip()
    if summary.startswith("[!note]"):
        summary = ""
    if summary:
        chunks.append(("resumen", None, None, summary))
    for line in (get_block(text, "puntos") or "").splitlines():
        line = line.strip("- ").strip()
        if line:
            m = re.search(r"\^t-(\d\d)-(\d\d)-(\d\d)", line)
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None
            chunks.append(("punto", t, None, re.sub(r"\(\[\[.*?\]\]\)", "", line).strip()))
    group: list[str] = []
    g_start: float | None = None
    g_speaker: str | None = None
    for line in (get_block(text, "transcripcion") or "").splitlines():
        m = TRANSCRIPT_LINE.match(line.strip())
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        speaker = (m.group(4) or "").strip() or None
        content = m.group(5).replace("==", "")
        if group and (len(group) >= 4 or speaker != g_speaker):
            chunks.append(("transcripcion", g_start, g_speaker, " ".join(group)))
            group = []
        if not group:
            g_start, g_speaker = t, speaker
        group.append(content)
    if group:
        chunks.append(("transcripcion", g_start, g_speaker, " ".join(group)))
    return summary, chunks


def index_note(index: SearchIndex, root: Path, path: Path, force: bool = False) -> None:
    rel = path.relative_to(root).as_posix()
    mtime = path.stat().st_mtime
    if not force and index.note_mtime(rel) == mtime:
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        log.warning("No se pudo leer %s", rel)
        return
    fm = parse_frontmatter(text)
    owned = bool(fm.get("transcriptor_id"))
    kind = str(fm.get("tipo", "")) if owned and fm.get("tipo") in OWN_KINDS else "usuario"
    aliases = fm.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    title = path.stem
    if kind == "clase":
        summary, chunks = class_chunks(text)
        topics = [link_text(t) or "" for t in (fm.get("temas") or [])]
        summary_full = (summary + " " + " ".join(topics)).strip()
        index.upsert_note(
            path=rel,
            title=title,
            kind=kind,
            owned=True,
            mtime=mtime,
            course=link_text(fm.get("curso")),
            day=str(fm.get("fecha") or "") or None,
            session_id=str(fm.get("transcriptor_id")),
            aliases=aliases,
            summary=summary_full,
            chunks=chunks,
        )
    else:
        _, body = split_frontmatter(text)
        if owned:
            body = strip_managed(body)
        index.upsert_note(
            path=rel,
            title=title,
            kind=kind,
            owned=owned,
            mtime=mtime,
            aliases=[str(a) for a in aliases],
            summary=body[:400],
            chunks=_paragraph_chunks(body),
        )


def sync_vault(index: SearchIndex, root: Path, excluded: list[str]) -> int:
    """Indexado incremental de toda la bóveda. Devuelve cuántas notas se revisaron."""
    seen: set[str] = set()
    n = 0
    for path in iter_markdown(root, excluded):
        seen.add(path.relative_to(root).as_posix())
        index_note(index, root, path)
        n += 1
    for gone in index.all_paths() - seen:
        index.remove_note(gone)
    return n
