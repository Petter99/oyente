# Estado de la implementación (v0.1.0 · 2026-10-12)

Resumen honesto de qué está hecho, cómo se verificó y qué falta verificar en hardware real.

## Verificado automáticamente

| Área | Cómo | Resultado |
|---|---|---|
| Configuración, llavero, logs sin secretos, nombres de archivo de Windows, marca cambiable | `tests/unit/test_base.py` | ✅ |
| VAD, grabación FLAC a prueba de cierres abruptos, Opus/FLAC y tamaños, retención | `tests/unit/test_audio.py` (voces sintéticas) | ✅ |
| Transcripción real (faster-whisper `small`) | `pytest -m slow` · voces en español del sistema | ✅ WER 7,5 % (archivo) |
| **Extremo a extremo real**: grabar → Whisper → profesor → nota | `tests/integration/test_e2e_real.py` | ✅ **WER 4,1 %**, hablantes correctos |
| Identificación del profesor (WeSpeaker ONNX) | `tests/integration/test_speakers_real.py` | ✅ 100 % en el caso de prueba |
| Notas de Obsidian, relaciones, «nunca modificar texto del usuario» (pruebas por propiedades) | `test_pipeline.py`, `test_markdown.py` | ✅ |
| Modo sin API key (sin red) | prueba guardiana con la red bloqueada | ✅ |
| Proveedores de LLM (Claude, Gemini, Ollama): misma suite de contrato | `tests/contract/` (clientes simulados) | ✅ |
| Respaldo, límite de gasto, sugerencias en vivo, lotes (Batch API) | `test_llm_logic.py`, `test_batch.py` | ✅ |
| Chat (tareas a 0 tokens, búsqueda híbrida, modo búsqueda) | `test_chat.py` | ✅ |
| Google Calendar/Tasks (API simulada), ICS de Canvas/Moodle/Blackboard, cruce de fechas | `test_calendar.py` | ✅ |
| Captura por pestaña (WebSocket local, seguridad, extensión) | `test_capture_web.py` | ✅ |
| Interfaz (todas las pantallas, tarjeta de Canvas, chat, hoja de grabación, estimación de espacio) | `tests/ui/` (simulación de usuario de NiceGUI) + capturas con `scripts/screenshots.py` | ✅ |
| App empaquetada (macOS) | PyInstaller + `scripts/smoke_test.py` | ✅ |
| Lint, tipos, licencias, secretos | ruff, mypy, `check_licenses.py`, gitleaks | ✅ |

Totales: 167 pruebas rápidas + 6 lentas (modelos reales).

## Pendiente de verificar en hardware real (no se puede automatizar aquí)

| Qué | Dónde | Lista |
|---|---|---|
| Captura **solo una aplicación** en Windows (WASAPI *process loopback*) y todo el sistema | PC Windows | `docs/pruebas/manual-windows.md` (T2.7, T2.8) |
| Instalador de Windows (Inno Setup), SmartScreen, rendimiento sin GPU | PC Windows | T14.1, T14.2, T3.7 |
| Captura por aplicación en macOS (ScreenCaptureKit) con permiso real | Mac | `docs/pruebas/manual-mac.md` |
| Pestaña del navegador en Chrome/Edge real y la extensión | Ambos | T11.4–T11.7 |
| Google OAuth real | Requiere crear el cliente OAuth (`docs/desarrollo/publicar.md` §4) | T12.10 |
| Claves reales de Claude/Gemini (pruebas «en vivo») | Con tus claves | T8.2, T8.7 |
| Nombres de modelos de Gemini por defecto (`gemini-flash-latest`…) | Configuración → IA | confirmar con tu cuenta |

## Decisiones tomadas durante la implementación

- **Huella de voz:** WeSpeaker ResNet34 en ONNX (CC-BY-4.0) por defecto: preciso y sin PyTorch,
  así el instalador no crece ~700 MB. ECAPA (SpeechBrain) queda como extra opcional.
- **Embeddings de texto:** sin el extra `[embeddings]` se usa un embedder ligero local (n-gramas);
  BGE-M3 + KeyBERT se activan instalando el extra (mejor calidad de relaciones y búsqueda).
- **Ventana:** barra de título nativa del sistema en Mac y Windows (más robusta que una propia).
- **Atajo `Ctrl/⌘+Alt+R`:** funciona con la ventana activa; con la app en segundo plano se usa el
  ícono de la bandeja / barra de menús.
- **Diarización con pyannote:** se guarda el token, pero la integración queda para una versión
  posterior (la identificación del profesor ya funciona sin ella).
