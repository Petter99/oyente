"""Puente de captura por pestaña (T11.1–T11.3)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from transcriptor.audio.sources.browser_tab import BrowserTabSource
from transcriptor.capture_web.hub import EXTENSION_IDS, TabCaptureHub
from transcriptor.capture_web.routes import register

EXT_ORIGIN = f"chrome-extension://{next(iter(EXTENSION_IDS))}"


@pytest.fixture
def setup() -> tuple[TestClient, TabCaptureHub]:
    hub = TabCaptureHub(port=8765)
    app = FastAPI()
    register(app, hub)
    return TestClient(app), hub


def test_capture_page_requires_token(setup: tuple[TestClient, TabCaptureHub]) -> None:
    client, hub = setup
    assert client.get("/captura?token=malo").status_code == 403
    ok = client.get(f"/captura?token={hub.token}")
    assert ok.status_code == 200 and "getDisplayMedia" in ok.text and "{{NOMBRE}}" not in ok.text
    assert (
        client.get("/captura/worklet.js")
        .headers["content-type"]
        .startswith("application/javascript")
    )


def test_t11_2_audio_flows_into_source(setup: tuple[TestClient, TabCaptureHub]) -> None:
    client, hub = setup
    src = BrowserTabSource()
    hub.attach(src)
    src.start()
    with client.websocket_connect(
        f"/ws/audio?token={hub.token}", headers={"origin": "http://127.0.0.1:8765"}
    ) as ws:
        ws.send_text(json.dumps({"type": "start", "label": "Meet - Clase"}))
        ws.send_bytes(np.full(1600, 0.25, dtype="<f4").tobytes())
        ws.send_bytes(np.full(1600, 0.25, dtype="<f4").tobytes())
    block = src.read(timeout=1)
    assert block is not None and block.size == 1600 and np.allclose(block, 0.25)
    assert src.info.label == "Pestaña: Meet - Clase"
    assert src.lost  # al cerrar la conexión, la fuente se marca como perdida (la sesión sigue)


@pytest.mark.parametrize(
    ("origin", "token"),
    [
        ("https://sitio-malicioso.com", "ok"),
        ("http://127.0.0.1:8765", "token-incorrecto"),
        ("chrome-extension://otraextensionqueno", None),
    ],
)
def test_t11_1_rejects_foreign_origins_and_bad_tokens(
    setup: tuple[TestClient, TabCaptureHub], origin: str, token: str | None
) -> None:
    client, hub = setup
    tok = hub.token if token == "ok" else token
    url = f"/ws/audio?token={tok}" if tok else "/ws/audio"
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(url, headers={"origin": origin}) as ws,
    ):
        ws.receive_text()


def test_t11_3_extension_detected(setup: tuple[TestClient, TabCaptureHub]) -> None:
    client, hub = setup
    events: list = []
    hub.expected_extension_version = "1.0.0"
    hub.on_event(lambda kind, data: events.append((kind, data)))
    with client.websocket_connect("/ws/extension", headers={"origin": EXT_ORIGIN}) as ws:
        ws.send_text(json.dumps({"type": "hello", "version": "0.9.0", "browser": "chrome"}))
        assert ws.receive_json()["type"] == "ok"
        assert hub.extension.connected and hub.extension.version == "0.9.0"
    assert ("extension", {"connected": True, "version": "0.9.0", "outdated": True}) in events
    assert not hub.extension.connected  # al cerrar el navegador se detecta la desconexión


def test_extension_audio_without_token(setup: tuple[TestClient, TabCaptureHub]) -> None:
    client, hub = setup
    src = BrowserTabSource()
    hub.attach(src)
    src.start()
    with client.websocket_connect("/ws/audio", headers={"origin": EXT_ORIGIN}) as ws:
        ws.send_bytes(np.zeros(1600, dtype="<f4").tobytes())
    assert src.read(timeout=1) is not None


def test_non_local_clients_rejected() -> None:
    hub = TabCaptureHub(port=8765)
    assert not hub.allowed("192.168.1.20", "http://127.0.0.1:8765", hub.token)
    assert hub.allowed("127.0.0.1", "http://127.0.0.1:8765", hub.token)
