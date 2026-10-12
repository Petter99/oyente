"""Captura de audio en macOS 13+ con ScreenCaptureKit (todo el sistema o una sola app).

Requiere el permiso «Grabación de pantalla y audio del sistema». Se prueba manualmente.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

from transcriptor.audio.sources.base import AudioSource, SourceInfo, SourceUnavailableError
from transcriptor.audio.sources.system import RunningApp

log = logging.getLogger(__name__)

SC_STREAM_OUTPUT_TYPE_AUDIO = 1
_RATE = 48_000


def has_permission() -> bool:
    try:
        import Quartz

        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def request_permission() -> bool:
    try:
        import Quartz

        return bool(Quartz.CGRequestScreenCaptureAccess())
    except Exception:
        return False


def _shareable_content(timeout: float = 5.0) -> Any:
    import ScreenCaptureKit as SCK

    done = threading.Event()
    result: dict[str, Any] = {}

    def handler(content: Any, error: Any) -> None:
        result["content"], result["error"] = content, error
        done.set()

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(handler)
    if not done.wait(timeout) or result.get("error") or result.get("content") is None:
        raise SourceUnavailableError(
            "macOS no permitió capturar el audio. Activa el permiso en Ajustes del Sistema → "
            "Privacidad y seguridad → Grabación de pantalla y audio del sistema."
        )
    return result["content"]


def list_shareable_apps() -> list[RunningApp]:
    content = _shareable_content()
    apps = []
    for app in content.applications():
        name = str(app.applicationName() or "")
        bundle = str(app.bundleIdentifier() or "")
        if name and bundle and (not bundle.startswith("com.apple.") or "Safari" in name):
            apps.append(RunningApp(bundle, name))
    return apps


def _make_output_class() -> Any:
    import objc
    from Foundation import NSObject

    SCStreamOutput = objc.protocolNamed("SCStreamOutput")
    SCStreamDelegate = objc.protocolNamed("SCStreamDelegate")

    class _AudioOutput(NSObject, protocols=[SCStreamOutput, SCStreamDelegate]):  # type: ignore[misc,call-arg]
        def initWithSource_(self, source: Any) -> Any:  # noqa: N802
            self = objc.super(_AudioOutput, self).init()
            if self is None:
                return None
            self.source = source
            return self

        def stream_didOutputSampleBuffer_ofType_(self, stream: Any, buffer: Any, kind: int) -> None:  # noqa: N802
            if kind != SC_STREAM_OUTPUT_TYPE_AUDIO:
                return
            try:
                import CoreMedia

                block = CoreMedia.CMSampleBufferGetDataBuffer(buffer)
                if block is None:
                    return
                length = CoreMedia.CMBlockBufferGetDataLength(block)
                status, data = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
                if status == 0 and data:
                    samples = np.frombuffer(bytes(data), dtype=np.float32)
                    self.source._emit(samples, _RATE)
            except Exception:
                log.exception("Error convirtiendo audio de ScreenCaptureKit")

        def stream_didStopWithError_(self, stream: Any, error: Any) -> None:  # noqa: N802
            self.source._mark_lost(str(error))

    return _AudioOutput


_OUTPUT_CLASS: Any = None


class ScreenCaptureSource(AudioSource):
    def __init__(self, app: RunningApp | None) -> None:
        label = app.name if app else "Todo el sistema"
        super().__init__(SourceInfo("app" if app else "system", label, "clase"))
        self.app = app
        self._stream: Any = None
        self._output: Any = None

    def _open(self) -> None:
        global _OUTPUT_CLASS
        import dispatch
        import ScreenCaptureKit as SCK

        if not has_permission():
            request_permission()
        content = _shareable_content()
        displays = content.displays()
        if not displays:
            raise SourceUnavailableError("No se encontró ninguna pantalla para capturar audio.")
        display = displays[0]
        if self.app is not None:
            target = [a for a in content.applications() if str(a.bundleIdentifier()) == self.app.id]
            if not target:
                raise SourceUnavailableError(f"«{self.app.name}» no está abierta.")
            flt = SCK.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(
                display, target, [])
        else:
            flt = SCK.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
                display, [], [])

        config = SCK.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setSampleRate_(_RATE)
        config.setChannelCount_(1)
        config.setExcludesCurrentProcessAudio_(True)
        config.setWidth_(2)
        config.setHeight_(2)

        if _OUTPUT_CLASS is None:
            _OUTPUT_CLASS = _make_output_class()
        self._output = _OUTPUT_CLASS.alloc().initWithSource_(self)
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, config, self._output)
        queue = dispatch.dispatch_queue_create(b"transcriptor.audio", None)
        ok, err = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, SC_STREAM_OUTPUT_TYPE_AUDIO, queue, None)
        if not ok:
            raise SourceUnavailableError(f"No se pudo iniciar la captura: {err}")

        started = threading.Event()
        errors: list[Any] = []

        def on_start(error: Any) -> None:
            if error:
                errors.append(error)
            started.set()

        self._stream.startCaptureWithCompletionHandler_(on_start)
        if not started.wait(5) or errors:
            raise SourceUnavailableError(f"No se pudo iniciar la captura de audio: {errors}")

    def _close(self) -> None:
        if self._stream is not None:
            done = threading.Event()
            self._stream.stopCaptureWithCompletionHandler_(lambda error: done.set())
            done.wait(3)
            self._stream = None
