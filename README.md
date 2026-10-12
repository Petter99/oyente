# Oyente

Transcribe tus clases en vivo, en tu propio equipo, y las guarda como notas de **Obsidian**
relacionadas entre sí. Opcionalmente usa IA (Claude, Gemini o un modelo local) para resúmenes,
tareas y un chat que responde con tus clases. Se integra con **Google Calendar y Tasks** y con
el calendario de **Canvas / Moodle / Blackboard**.

> Repositorio privado. © Todos los derechos reservados (ver `LICENSE`).
> El nombre visible se cambia en `src/transcriptor/branding.toml` (ver `docs/plan/plan-fases.md` §6.5).

## Qué hace

| Sin API key | Con IA |
|---|---|
| Transcripción en vivo + pasada final de alta fidelidad (Whisper, local) | Resumen, puntos importantes y tareas precisas |
| Identifica al **profesor**, a ti («Yo») y a los participantes | Sugerencias durante la clase (bajo consumo) |
| Nota `.md` con relaciones automáticas (curso, anterior/siguiente, temas, profesor, tus notas) | Chat con respuestas citadas al minuto |
| Temas y «posibles tareas» locales, búsqueda en clases anteriores | «Procesar clases pendientes» (por lotes) |

El audio **nunca sale del equipo**. Fuentes de audio: una aplicación (recomendado), una pestaña
del navegador (Chrome/Edge), todo el sistema o solo micrófono.

## Documentación

- Usuarios: [`docs/usuario/guia.md`](docs/usuario/guia.md)
- Planes: [funcional](docs/plan/plan-transcriptor.md) · [diseño](docs/plan/plan-diseno.md) · [fases](docs/plan/plan-fases.md)
- Publicar, firmar y Google OAuth: [`docs/desarrollo/publicar.md`](docs/desarrollo/publicar.md)
- Pruebas manuales: [Windows](docs/pruebas/manual-windows.md) · [macOS](docs/pruebas/manual-mac.md)
- Estado de la implementación: [`docs/desarrollo/estado.md`](docs/desarrollo/estado.md)
- Privacidad y términos: [`docs/legal/`](docs/legal/)

## Desarrollo

Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.12 solo).

```bash
uv sync                          # dependencias
uv run oyente                    # app de escritorio
uv run oyente --browser          # en el navegador (diagnóstico)
uv run pytest                    # pruebas rápidas (sin red ni hardware)
uv run pytest -m slow            # + transcripción real con Whisper (descarga modelos)
uv run ruff check src tests scripts && uv run mypy
uv run python scripts/check_licenses.py   # auditoría de licencias + avisos de terceros
```

Datos de ejemplo para revisar la interfaz sin grabar:

```bash
TRANSCRIPTOR_HOME=build/uihome uv run python scripts/dev_seed.py
TRANSCRIPTOR_HOME=build/uihome uv run oyente --browser
```

Extras opcionales: `uv sync --extra mlx` (Mac Apple Silicon), `--extra speakers` (huella de voz
ECAPA), `--extra embeddings` (BGE-M3 + KeyBERT).

## Compilar

- **Windows** (en el PC Windows): `git pull` y
  `powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1` → `dist\Oyente-Setup-<versión>.exe`
- **macOS**: `bash scripts/build-mac.sh` → `dist/Oyente-<versión>.dmg`

## Estructura

```
src/transcriptor/
  audio/        fuentes (app, pestaña, sistema, mic), VAD, grabación, retención
  asr/          Whisper (faster-whisper / MLX), WER
  speakers/     identificación del profesor (huella de voz)
  session/      grabación en vivo y cierre de clase
  notes/ links/ notas de Obsidian y relaciones automáticas
  llm/          proveedores (Claude, Gemini, Ollama, sin IA), respaldo y presupuesto
  processing/ live/ chat/ search/   resumen, sugerencias, chat e índice local
  calendar/     Google Calendar + Tasks, calendarios ICS de la universidad
  capture_web/  captura por pestaña (página local + extensión)
  ui/           interfaz (NiceGUI + ventana nativa), bandeja/barra de menús
browser-extension/  extensión opcional de Chrome/Edge
packaging/          PyInstaller, Inno Setup, macOS
```
