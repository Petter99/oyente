"""Temas de una clase sin LLM: KeyBERT si está instalado; si no, TF-IDF de n-gramas."""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Callable

from rapidfuzz import fuzz

from transcriptor.nlp.text import STOPWORDS, fold, words

log = logging.getLogger(__name__)

# Palabras frecuentes en clases que no son temas.
CLASS_NOISE = frozenset({
    "clase", "clases", "profesor", "profe", "tarea", "tareas", "pregunta", "preguntas", "semana",
    "hoy", "mañana", "jueves", "lunes", "martes", "miércoles", "viernes", "sábado", "domingo",
    "minuto", "minutos", "hora", "horas", "examen", "parcial", "punto", "puntos", "chicos", "gracias",
    "favor", "momento", "listo", "verdad", "importante", "próximo", "próxima", "siguiente", "primero",
    "segundo", "tercero", "vez", "veces", "aquí", "nota", "notas", "hoja", "ejercicio", "ejercicios",
    "entregar", "dudas", "duda", "tema", "temas", "ejemplo", "favor", "cuatro", "tres", "dos", "cinco",
})


def _candidates(text: str, max_n: int = 3,
                forms: dict[str, Counter[str]] | None = None) -> Counter[str]:
    toks = words(text)
    counts: Counter[str] = Counter()
    for n in range(1, max_n + 1):
        for i in range(len(toks) - n + 1):
            gram = toks[i : i + n]
            first, last = fold(gram[0]), fold(gram[-1])
            if first in STOPWORDS or last in STOPWORDS:
                continue
            if any(fold(g) in CLASS_NOISE or g.isdigit() or len(g) < 3 for g in (gram[0], gram[-1])):
                continue
            if n == 1 and len(gram[0]) < 5:
                continue
            key = " ".join(gram).lower()
            counts[key] += 1
            if forms is not None:
                forms.setdefault(key, Counter())[" ".join(gram)] += 1
    return counts


def tfidf_keywords(text: str, top: int = 6, doc_freq: Callable[[str], int] | None = None,
                   n_docs: int = 1) -> list[str]:
    forms: dict[str, Counter[str]] = {}
    counts = _candidates(text, forms=forms)
    if not counts:
        return []
    scored = []
    for cand, tf in counts.items():
        if tf < 2:
            continue
        n_words = cand.count(" ") + 1
        df = doc_freq(cand) if doc_freq else 0
        idf = math.log((n_docs + 1) / (df + 1)) + 1.0
        scored.append((tf * idf * (1.0 + 0.6 * (n_words - 1)), cand))
    scored.sort(reverse=True)
    chosen: list[str] = []
    for _, cand in scored:
        # Diversidad: no repetir variantes del mismo tema.
        if any(fuzz.token_set_ratio(fold(cand), fold(c)) >= 80 for c in chosen):
            continue
        chosen.append(cand)
        if len(chosen) >= top:
            break
    out = []
    for c in chosen:
        form = forms[c].most_common(1)[0][0]
        out.append(form[0].upper() + form[1:])
    return out


def extract_topics(text: str, top: int = 6) -> list[str]:
    try:
        from keybert import KeyBERT

        from transcriptor.nlp.embed import SentenceTransformerEmbedder, default_text_embedder

        emb = default_text_embedder()
        if isinstance(emb, SentenceTransformerEmbedder):
            kw = KeyBERT(model=emb._model)
            pairs = kw.extract_keywords(text, keyphrase_ngram_range=(1, 3), stop_words=list(STOPWORDS),
                                        use_mmr=True, diversity=0.6, top_n=top)
            return [p[0][0].upper() + p[0][1:] for p in pairs]
    except Exception:
        log.debug("KeyBERT no disponible; se usa TF-IDF")
    return tfidf_keywords(text, top=top)
