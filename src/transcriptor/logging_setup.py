"""Registro rotativo con filtro que oculta secretos (API keys, tokens, enlaces ICS)."""

from __future__ import annotations

import logging
import re
import threading
from logging.handlers import RotatingFileHandler

from transcriptor.paths import log_dir

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),  # Anthropic
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google API key
    re.compile(r"ya29\.[0-9A-Za-z_\-\.]+"),  # Google OAuth access token
    re.compile(r"1//[0-9A-Za-z_\-]{20,}"),  # Google refresh token
    re.compile(r"hf_[A-Za-z0-9]{20,}"),  # Hugging Face
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]+"),
    # Enlaces de calendario con token (Canvas: /feeds/calendars/user_XXXX.ics)
    re.compile(r"(?i)(webcal|https?)://\S+?(\.ics|/feeds/|/calendar/export)\S*"),
]
_known: set[str] = set()
_lock = threading.Lock()

REDACTED = "[OCULTO]"


def register_secret(value: str) -> None:
    """Agrega un valor concreto que nunca debe aparecer en los registros."""
    if value and len(value) >= 6:
        with _lock:
            _known.add(value)


def redact(text: str) -> str:
    with _lock:
        known = sorted(_known, key=len, reverse=True)
    for value in known:
        text = text.replace(value, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = redact(message)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if clean != message or record.args:
            record.msg = clean
            record.args = None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = RotatingFileHandler(
        log_dir() / "oyente.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(fmt)
    handler.addFilter(SecretFilter())
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(SecretFilter())
    root.addHandler(handler)
    root.addHandler(console)
