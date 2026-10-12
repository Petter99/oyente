"""Captura de audio en Windows.

- ``WindowsLoopbackSource``: todo el sistema (WASAPI loopback, librería soundcard).
- ``WindowsProcessLoopbackSource``: solo una aplicación (WASAPI *process loopback*,
  Windows 10 build 20348+ / Windows 11), vía ActivateAudioInterfaceAsync.

Este módulo solo se importa en Windows. Se prueba manualmente (docs/pruebas/manual-windows.md).
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import POINTER, byref, c_int16, c_uint32, c_void_p, wintypes
from typing import Any

import comtypes
import numpy as np
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

from transcriptor.audio.sources.base import AudioSource, SourceInfo, SourceUnavailableError
from transcriptor.audio.sources.system import RunningApp

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- sesiones
def list_audio_sessions() -> list[RunningApp]:
    """Apps con sesión de audio (pycaw). ``playing`` indica si suenan ahora mismo."""
    from pycaw.pycaw import AudioUtilities

    apps: dict[str, RunningApp] = {}
    for session in AudioUtilities.GetAllSessions():
        proc = session.Process
        if proc is None:
            continue
        name = proc.name().removesuffix(".exe")
        root = _root_pid(proc.pid)
        playing = session.State == 1
        current = apps.get(name.lower())
        if current is None or (playing and not current.playing):
            apps[name.lower()] = RunningApp(str(root), name, playing)
    return list(apps.values())


def _root_pid(pid: int) -> int:
    """Sube por los procesos padre con el mismo ejecutable (Chrome usa muchos procesos)."""
    import psutil

    try:
        proc = psutil.Process(pid)
        name = proc.name()
        while (parent := proc.parent()) is not None and parent.name() == name:
            proc = parent
        return proc.pid
    except psutil.Error:
        return pid


# --------------------------------------------------------------------------- todo el sistema
class WindowsLoopbackSource(AudioSource):
    RATE = 48_000

    def __init__(self) -> None:
        super().__init__(SourceInfo("system", "Todo el sistema", "clase"))
        self._thread: threading.Thread | None = None

    def _open(self) -> None:
        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            self._mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as exc:
            raise SourceUnavailableError("No se pudo capturar el audio del sistema.") from exc
        self._thread = threading.Thread(target=self._run, name="wasapi-loopback", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            with self._mic.recorder(samplerate=self.RATE, channels=1, blocksize=1024) as rec:
                while self.running:
                    data = rec.record(numframes=self.RATE // 10)
                    self._emit(data, self.RATE)
        except Exception as exc:
            self._mark_lost(f"Loopback detenido: {exc}")
        finally:
            comtypes.CoUninitialize()

    def _close(self) -> None:
        if self._thread:
            self._thread.join(timeout=2)


# --------------------------------------------------------------------------- una aplicación
VT_BLOB = 65
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
WAVE_FORMAT_PCM = 1


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int),
                ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD),
                ("r3", wintypes.WORD), ("blob", BLOB)]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer",
                  (["out"], POINTER(POINTER(ctypes.c_byte)), "ppData"),
                  (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                  (["out"], POINTER(wintypes.DWORD), "pdwFlags"),
                  (["in"], c_void_p, "pu64DevicePosition"),
                  (["in"], c_void_p, "pu64QPCPosition")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFrames")),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize",
                  (["in"], ctypes.c_int, "ShareMode"),
                  (["in"], wintypes.DWORD, "StreamFlags"),
                  (["in"], ctypes.c_longlong, "hnsBufferDuration"),
                  (["in"], ctypes.c_longlong, "hnsPeriodicity"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["in"], c_void_p, "AudioSessionGuid")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "pNumBufferFrames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong), "phnsLatency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "pNumPaddingFrames")),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int, "ShareMode"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(ctypes.c_longlong), "a"),
                  (["out"], POINTER(ctypes.c_longlong), "b")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "eventHandle")),
        COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "riid"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppv")),
    ]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult",
                  (["out"], POINTER(HRESULT), "activateResult"),
                  (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted",
                  (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation")),
    ]


class IAgileObject(IUnknown):
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_: list[Any] = []


class _CompletionHandler(comtypes.COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self) -> None:
        super().__init__()
        self.done = threading.Event()
        self.client: Any = None
        self.error: str | None = None

    def ActivateCompleted(self, operation: Any) -> int:  # noqa: N802 (nombre COM)
        try:
            hr, punk = operation.GetActivateResult()
            if hr != 0:
                self.error = f"HRESULT 0x{hr & 0xFFFFFFFF:08X}"
            else:
                self.client = punk.QueryInterface(IAudioClient)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.done.set()
        return 0


class WindowsProcessLoopbackSource(AudioSource):
    RATE = 48_000
    CHANNELS = 2

    def __init__(self, app: RunningApp) -> None:
        super().__init__(SourceInfo("app", app.name, "clase"))
        self.app = app
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._open_error: str | None = None

    def _open(self) -> None:
        self._thread = threading.Thread(target=self._run, name="process-loopback", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        if self._open_error or not self._ready.is_set():
            self._running.clear()
            raise SourceUnavailableError(
                f"No se pudo capturar solo «{self.app.name}» ({self._open_error or 'tiempo agotado'}). "
                "Requiere Windows 10 (20348+) o Windows 11. Usa «Todo el sistema»."
            )

    def _activate(self) -> Any:
        params = AUDIOCLIENT_ACTIVATION_PARAMS()
        params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        params.ProcessLoopbackParams.TargetProcessId = int(self.app.id)
        params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        prop = PROPVARIANT()
        prop.vt = VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(params)
        prop.blob.pBlobData = ctypes.cast(byref(params), c_void_p)

        handler = _CompletionHandler()
        operation = POINTER(IActivateAudioInterfaceAsyncOperation)()
        activate = ctypes.windll.Mmdevapi.ActivateAudioInterfaceAsync  # type: ignore[attr-defined]
        activate.restype = HRESULT
        activate(
            ctypes.c_wchar_p(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK),
            byref(IAudioClient._iid_),
            byref(prop),
            handler.QueryInterface(IActivateAudioInterfaceCompletionHandler),
            byref(operation),
        )
        if not handler.done.wait(timeout=5) or handler.error or handler.client is None:
            raise OSError(handler.error or "activación sin respuesta")
        return handler.client

    def _run(self) -> None:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        event = None
        client = None
        try:
            client = self._activate()
            fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, self.CHANNELS, self.RATE,
                               self.RATE * self.CHANNELS * 2, self.CHANNELS * 2, 16, 0)
            client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
                2_000_000, 0, byref(fmt), None,
            )
            event = kernel32.CreateEventW(None, False, False, None)
            client.SetEventHandle(event)
            capture = client.GetService(byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
            client.Start()
            self._ready.set()
            while self.running:
                if kernel32.WaitForSingleObject(event, 200) != 0:
                    continue
                while capture.GetNextPacketSize() > 0:
                    data, n, flags = capture.GetBuffer(None, None)
                    if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                        pcm = np.zeros((n, self.CHANNELS), dtype=np.float32)
                    else:
                        raw = ctypes.cast(data, POINTER(c_int16 * (n * self.CHANNELS))).contents
                        pcm = np.frombuffer(raw, dtype=np.int16).reshape(-1, self.CHANNELS) / 32768.0
                    capture.ReleaseBuffer(n)
                    self._emit(pcm.astype(np.float32), self.RATE)
        except Exception as exc:
            if not self._ready.is_set():
                self._open_error = str(exc)
                self._ready.set()
            else:
                self._mark_lost(f"La app dejó de estar disponible: {exc}")
        finally:
            if client is not None:
                try:
                    client.Stop()
                except Exception:
                    pass
            if event:
                kernel32.CloseHandle(event)
            comtypes.CoUninitialize()

    def _close(self) -> None:
        if self._thread:
            self._thread.join(timeout=2)
