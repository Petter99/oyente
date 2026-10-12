# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: genera la app de escritorio (Windows: carpeta con .exe; macOS: .app).
# Uso: uv run --group build pyinstaller packaging/app.spec --noconfirm
# El nombre visible sale de src/transcriptor/branding.toml.

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
brand = tomllib.loads((ROOT / "src/transcriptor/branding.toml").read_text(encoding="utf-8"))
NAME = brand["ejecutable"].replace("{nombre}", brand["nombre"])
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

datas = [
    (str(ROOT / "assets" / "branding"), "assets/branding"),
    (str(ROOT / "dist" / "extension"), "browser-extension"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]
for pkg in ("nicegui", "faster_whisper", "transcriptor", "certifi", "icalendar", "googleapiclient"):
    datas += collect_data_files(pkg)
for pkg in ("nicegui", "anthropic", "google-genai", "google-api-python-client", "faster-whisper", "transcriptor"):
    datas += copy_metadata(pkg)

hidden = collect_submodules("transcriptor") + [
    "pystray._win32" if IS_WIN else "pystray._darwin",
    "sounddevice",
    "keyring.backends.Windows" if IS_WIN else "keyring.backends.macOS",
]
if IS_WIN:
    hidden += ["webview.platforms.edgechromium", "webview.platforms.winforms", "comtypes.stream",
               "pycaw.pycaw", "soundcard.mediafoundation"]
if IS_MAC:
    hidden += ["webview.platforms.cocoa", "ScreenCaptureKit", "CoreMedia", "dispatch", "Quartz"]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hidden,
    excludes=["torch", "torchaudio", "speechbrain", "sentence_transformers", "keybert", "pytest",
              "hypothesis", "tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(ROOT / "assets" / "branding" / ("icon.ico" if IS_WIN else "icon.icns"))
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=icon,
    version=str(ROOT / "packaging" / "windows" / "version_info.txt") if IS_WIN else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=icon,
        bundle_identifier="com.mcanil.transcriptor",  # identificador interno estable (no cambia con la marca)
        info_plist={
            "CFBundleDisplayName": brand["nombre"],
            "CFBundleName": brand["nombre"],
            "LSMinimumSystemVersion": "13.0",
            "NSMicrophoneUsageDescription": "Para transcribir tu voz en las clases.",
            "NSScreenCaptureUsageDescription": "Para capturar el audio de la aplicación de la clase.",
            "NSHighResolutionCapable": True,
        },
    )
