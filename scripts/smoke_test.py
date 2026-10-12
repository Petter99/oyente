"""Prueba de humo del ejecutable empaquetado (T14.6).

Arranca la app en modo servidor (--headless) con datos aislados, comprueba que responde y que
las páginas principales cargan, y la cierra.

Uso:  python scripts/smoke_test.py <ruta-al-ejecutable>
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.request


def get(url: str) -> int:
    with urllib.request.urlopen(url, timeout=5) as r:
        return int(r.status)


def main() -> int:
    exe = sys.argv[1]
    home = tempfile.mkdtemp(prefix="transcriptor-smoke-")
    env = {**os.environ, "TRANSCRIPTOR_HOME": home}
    proc = subprocess.Popen([exe, "--headless"], env=env)
    try:
        for _ in range(90):
            try:
                if get("http://127.0.0.1:8765/api/vivo") == 200:
                    break
            except Exception:
                time.sleep(1)
        else:
            print("La app no respondió en 90 s")
            return 1
        for path in ("/bienvenida", "/captura/worklet.js", "/branding/icon.png"):
            status = get(f"http://127.0.0.1:8765{path}")
            print(f"{status} {path}")
            if status != 200:
                return 1
        print("Prueba de humo: OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
