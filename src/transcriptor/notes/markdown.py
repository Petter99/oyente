"""Edición quirúrgica de notas de Obsidian.

Reglas de seguridad (plan §7.3):
- Solo se tocan las propiedades del frontmatter que pertenecen a la app (línea a línea, sin
  reescribir el YAML del usuario) y el contenido entre marcadores
  ``%% transcriptor:<nombre>:inicio %%`` / ``%% transcriptor:<nombre>:fin %%``.
- Todo lo demás queda idéntico, byte a byte.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from transcriptor.branding import VAULT_MARKER

FM_RE = re.compile(r"\A---\r?\n(.*?\r?\n)?---[ \t]*(\r?\n|\Z)", re.S)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(bloque YAML sin delimitadores o None, cuerpo)."""
    m = FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1) or "", text[m.end():]


def parse_frontmatter(text: str) -> dict[str, Any]:
    raw, _ = split_frontmatter(text)
    if raw is None:
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _yaml_value(value: Any) -> str:
    dumped = yaml.safe_dump({"k": value}, allow_unicode=True, default_flow_style=False,
                            sort_keys=False, width=10_000)
    return dumped[len("k:"):].rstrip("\n")


def render_frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        lines.append(f"{key}:{_yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def set_properties(text: str, values: dict[str, Any]) -> str:
    """Actualiza (o agrega) propiedades del frontmatter sin tocar las demás líneas."""
    m = FM_RE.match(text)
    if not m:
        return render_frontmatter(values) + text
    raw = m.group(1) or ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines(keepends=True)
    remaining = dict(values)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        key_match = re.match(r"^([^\s#][^:]*):", line)
        key = key_match.group(1).strip() if key_match else None
        if key is not None and key in remaining:
            # Saltar las líneas de continuación (listas o bloques indentados) de esta clave.
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t", "-")) and not re.match(r"^[^\s#-][^:]*:", lines[j])):
                j += 1
            out.append(f"{key}:{_yaml_value(remaining.pop(key))}{newline}")
            i = j
            continue
        out.append(line)
        i += 1
    if out and not out[-1].endswith(("\n", "\r\n")):
        out[-1] += newline
    for key, value in remaining.items():
        out.append(f"{key}:{_yaml_value(value)}{newline}")
    return f"---{newline}{''.join(out)}---{m.group(2) or newline}" + text[m.end():]


def block_markers(name: str) -> tuple[str, str]:
    return f"%% {VAULT_MARKER}:{name}:inicio %%", f"%% {VAULT_MARKER}:{name}:fin %%"


def wrap_block(name: str, content: str) -> str:
    start, end = block_markers(name)
    body = content.strip("\n")
    return f"{start}\n{body}\n{end}" if body else f"{start}\n{end}"


def get_block(text: str, name: str) -> str | None:
    start, end = block_markers(name)
    m = re.search(re.escape(start) + r"\r?\n(.*?)" + re.escape(end), text, re.S)
    if not m:
        return None
    return m.group(1).rstrip("\r\n")


def has_block(text: str, name: str) -> bool:
    start, end = block_markers(name)
    return start in text and end in text


def replace_block(text: str, name: str, content: str, heading: str | None = None) -> str:
    """Reemplaza el contenido del bloque; si no existe, lo agrega al final (con encabezado)."""
    start, end = block_markers(name)
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    new_block = wrap_block(name, content)
    if pattern.search(text):
        return pattern.sub(lambda _: new_block, text, count=1)
    suffix = "" if text.endswith("\n") or not text else "\n"
    head = f"\n{heading}\n" if heading else "\n"
    return f"{text}{suffix}{head}{new_block}\n"


def managed_names(text: str) -> list[str]:
    return re.findall(rf"%% {re.escape(VAULT_MARKER)}:([\w-]+):inicio %%", text)


def strip_managed(text: str) -> str:
    """Texto sin bloques gestionados (para comprobar que lo demás no cambió)."""
    out = text
    for name in managed_names(text):
        start, end = block_markers(name)
        out = re.sub(re.escape(start) + r".*?" + re.escape(end), "", out, flags=re.S)
    return out


def wikilink(target: str, alias: str | None = None) -> str:
    target = target.removesuffix(".md")
    return f"[[{target}|{alias}]]" if alias and alias != target else f"[[{target}]]"
