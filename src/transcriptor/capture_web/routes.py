"""Rutas locales: página de captura de pestaña y WebSockets de audio/extensión (FastAPI)."""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from transcriptor.branding import branding
from transcriptor.capture_web.hub import TabCaptureHub

log = logging.getLogger(__name__)


def _static(name: str) -> str:
    return (
        resources.files("transcriptor.capture_web")
        .joinpath("static", name)
        .read_text(encoding="utf-8")
    )


def register(app: FastAPI | Any, hub: TabCaptureHub) -> None:
    @app.get("/captura", response_class=HTMLResponse)
    def capture_page(token: str = "") -> Response:
        if token != hub.token:
            return PlainTextResponse(
                "Enlace de captura vencido. Vuelve a la app y pulsa «Elegir pestaña».",
                status_code=403,
            )
        html = _static("captura.html").replace("{{NOMBRE}}", branding().nombre)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/captura/worklet.js")
    def worklet() -> Response:
        return Response(_static("worklet.js"), media_type="application/javascript")

    @app.websocket("/ws/audio")
    async def audio_ws(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        token = ws.query_params.get("token")
        host = ws.client.host if ws.client else None
        if not hub.allowed(host, origin, token):
            await ws.close(code=4403)
            return
        via = "extension" if (origin or "").startswith("chrome-extension://") else "page"
        await ws.accept()
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if not hub.handle_audio(msg["bytes"]):
                        await ws.send_json({"type": "idle"})
                elif msg.get("text") is not None:
                    hub.handle_text(msg["text"], via)
        except WebSocketDisconnect:
            pass
        finally:
            hub.handle_closed(via)

    @app.websocket("/ws/extension")
    async def extension_ws(ws: WebSocket) -> None:
        """Canal de control de la extensión: solo para detectarla (no lleva audio)."""
        origin = ws.headers.get("origin")
        host = ws.client.host if ws.client else None
        if not (origin or "").startswith("chrome-extension://") or not hub.allowed(
            host, origin, None
        ):
            await ws.close(code=4403)
            return
        await ws.accept()
        try:
            while True:
                text = await ws.receive_text()
                hub.handle_text(text, "extension")
                await ws.send_json({"type": "ok", "app": branding().nombre})
        except WebSocketDisconnect:
            pass
        finally:
            hub.handle_closed("extension-control")
