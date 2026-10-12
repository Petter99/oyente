"""Edición segura de notas (T6.2, T6.3) con pruebas basadas en propiedades."""

from __future__ import annotations

import string

import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from transcriptor.notes.markdown import (
    get_block,
    parse_frontmatter,
    render_frontmatter,
    replace_block,
    set_properties,
    split_frontmatter,
    strip_managed,
    wrap_block,
)

OWN_KEYS = ["anterior", "siguiente", "relacionadas", "temas", "tareas_pendientes"]

user_text = st.text(
    alphabet=string.ascii_letters + string.digits + " \n#-*>[]()áéíóúñÁÉÍÓÚÑ.,:¿?¡!`",
    max_size=400,
).filter(lambda t: "%%" not in t and not t.startswith("---"))

user_key = st.from_regex(r"[a-z][a-z_]{0,10}", fullmatch=True).filter(lambda k: k not in OWN_KEYS)
user_value = st.one_of(st.integers(-1000, 1000), st.booleans(),
                       st.text(alphabet=string.ascii_letters + " áñ", max_size=20),
                       st.lists(st.text(alphabet=string.ascii_letters, min_size=1, max_size=8), max_size=3))


@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(
    body=user_text,
    props=st.dictionaries(user_key, user_value, max_size=5),
    updates=st.dictionaries(st.sampled_from(OWN_KEYS), user_value, min_size=1, max_size=5),
    blocks=st.lists(st.tuples(st.sampled_from(["resumen", "tareas", "relacionado"]), user_text), max_size=4),
)
def test_t6_3_user_text_is_never_modified(body: str, props: dict, updates: dict, blocks: list) -> None:
    """Fuera de los bloques gestionados y de las propiedades propias, el archivo queda idéntico."""
    original = render_frontmatter(props) + body if props else body
    edited = set_properties(original, updates)
    for name, content in blocks:
        edited = replace_block(edited, name, content)

    # 1) Las propiedades del usuario se conservan con su valor y su texto exacto.
    fm = parse_frontmatter(edited)
    for key, value in props.items():
        assert fm[key] == value
    for key, value in updates.items():
        assert fm[key] == value
    raw_before, _ = split_frontmatter(original)
    raw_after, body_after = split_frontmatter(edited)
    if raw_before:
        for line in raw_before.splitlines():
            key = line.split(":", 1)[0]
            if key in props:
                assert line in raw_after.splitlines()  # type: ignore[union-attr]

    # 2) El cuerpo del usuario aparece intacto (los bloques solo se agregan al final).
    body_clean = strip_managed(body_after)
    assert body_clean.startswith(body if not original.startswith("---") or props else body)


def test_replace_existing_block_keeps_surroundings() -> None:
    text = "# Clase\n\nMis notas antes.\n\n" + wrap_block("resumen", "viejo") + "\n\nMis notas después.\n"
    new = replace_block(text, "resumen", "nuevo resumen")
    assert get_block(new, "resumen") == "nuevo resumen"
    assert new.replace(wrap_block("resumen", "nuevo resumen"), "X") == \
        text.replace(wrap_block("resumen", "viejo"), "X")


def test_set_properties_preserves_crlf_and_comments() -> None:
    text = "---\r\n# comentario del usuario\r\nmio: 1\r\ntemas:\r\n- a\r\n- b\r\n---\r\ncuerpo\r\n"
    out = set_properties(text, {"temas": ["c"], "siguiente": "[[x]]"})
    assert "# comentario del usuario\r\n" in out and "mio: 1\r\n" in out
    assert out.endswith("---\r\ncuerpo\r\n")
    assert yaml.safe_load(split_frontmatter(out)[0] or "") == {"mio": 1, "temas": ["c"], "siguiente": "[[x]]"}


def test_note_without_frontmatter_gets_one() -> None:
    out = set_properties("solo texto", {"tipo": "tema"})
    assert parse_frontmatter(out) == {"tipo": "tema"} and out.endswith("solo texto")
