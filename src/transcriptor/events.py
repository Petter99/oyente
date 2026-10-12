"""Bus de eventos sencillo entre el núcleo y la interfaz (hilos → UI)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


Listener = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def emit(self, type_: str, **data: Any) -> None:
        event = Event(type_, data)
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                log.exception("Error en un oyente del evento %s", type_)
