# Cambios

## Sin publicar
- Asistente: el paso 6 (Obsidian) ya no muestra «Omitir», que llevaba a una página en blanco; «Terminar» funciona sin carpetas excluidas y aunque falle el análisis de la bóveda.

## 0.1.0 — 2026-10-12
Primera versión completa (alfa interna).

- Transcripción local en vivo + pasada final de alta fidelidad (faster-whisper / MLX).
- Fuentes: una aplicación, una pestaña del navegador (página local o extensión), todo el sistema, micrófono.
- Identificación del profesor con huella de voz (WeSpeaker ONNX) y confirmación en la primera clase.
- Notas de Obsidian con relaciones automáticas; nunca se modifica texto del usuario.
- Modo sin API key completo; IA opcional con Claude, Gemini u Ollama, respaldo entre proveedores,
  límite de gasto, sugerencias en vivo y procesamiento por lotes.
- Chat con tareas a 0 tokens y búsqueda híbrida local.
- Google Calendar + Tasks, calendario de Canvas/Moodle/Blackboard y cruce de fechas.
- Audio: Opus o FLAC con estimación de espacio; retención siempre / N días / nunca.
- Interfaz de escritorio (Mac y Windows), asistente de primera vez, bandeja / barra de menús.
- Instalador de Windows (Inno Setup) y app de macOS (PyInstaller).
