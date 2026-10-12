# Guía de uso

## 1. Instalar
- **Windows:** abre `Oyente-Setup-<versión>.exe`. Si Windows muestra «Windows protegió su PC»
  (versiones sin firma), pulsa **Más información → Ejecutar de todas formas**.
- **Mac:** abre el `.dmg` y arrastra la app a Aplicaciones. Si macOS no la abre la primera vez,
  clic derecho → **Abrir**.

## 2. Primera vez (asistente)
1. **Carpeta:** elige dónde guardar tus clases (tu bóveda de Obsidian, o una carpeta en Drive/OneDrive).
2. **Cursos:** agrega tus cursos, el profesor y un glosario (nombres y términos técnicos).
3. **IA (opcional):** Claude, Gemini, local (Ollama) o «Ninguno: solo transcribir».
4. **Google (opcional):** horario, recordatorios y tareas.
5. **Audio:** de dónde se toma (recomendado: *solo una aplicación*), si se guarda y en qué formato.
6. **Obsidian:** carpetas a excluir. Pulsa *Terminar*.

La primera vez se descarga el modelo de voz (unos minutos, solo una vez).

## 3. Grabar una clase
1. Pulsa **Grabar** (o `Ctrl+Alt+R` / `⌘⌥R`).
2. Elige el curso y la fuente:
   - **Solo una aplicación:** elige tu navegador (o Zoom/Teams). Funciona con cualquier navegador.
   - **Una pestaña:** se abre una página en Chrome/Edge; pulsa *Elegir pestaña*, elige la de la clase
     y deja marcada *Compartir audio de la pestaña*.
   - **Todo el sistema** o **Solo micrófono** (clases presenciales).
3. Verás el texto en vivo con *Profesor / Yo / Participante*. Puedes **Marcar momento** o **Pausar**.
4. Pulsa **Detener**: se hace la transcripción final (más precisa) y se crea la nota.

En Mac, la primera vez macOS pide permiso de **Grabación de pantalla y audio del sistema**:
Ajustes del Sistema → Privacidad y seguridad → activa la app y ábrela de nuevo.

## 4. Después de la clase
- **¿Esta es la voz del profesor?** (solo la primera clase de cada curso): escucha y pulsa *Sí* o
  elige la voz correcta. Después lo reconocerá solo.
- **Palabras en amarillo:** el modelo no estaba seguro; revísalas.
- **Tareas:** pulsa *Agregar* para enviarlas a Google Tasks o a tu calendario. Si la tarea ya está en
  Canvas verás «Ya está en Canvas»; si la fecha no coincide, «Las fechas no coinciden».
- **Abrir en Obsidian:** la nota ya está enlazada con el curso, la clase anterior, el profesor, los
  temas y tus propias notas.

## 5. Chat
Pregunta «¿Qué tareas tengo esta semana?» (gratis, sin IA) o «¿Cómo explicó el profe la regla
LIATE?» (usa IA si la configuraste; si no, muestra los fragmentos más relevantes).

## 6. Calendario de la universidad
Configuración → Universidad: guía para Canvas, Moodle y Blackboard. Copia el enlace del calendario
y agrégalo en Google Calendar (*Otros calendarios → + → Desde URL*), o pégalo en la app para que lo
lea localmente.

## 7. Extensión del navegador (opcional)
Solo si capturas por pestaña y quieres hacerlo con un clic: Configuración → Audio →
**Preparar extensión** y sigue los pasos (Chrome/Edge → Extensiones → Modo de desarrollador →
Cargar descomprimida).

## Problemas frecuentes
| Problema | Solución |
|---|---|
| No aparece la app en la lista | Abre la llamada y pulsa *Actualizar lista*, o usa *Todo el sistema* |
| La pestaña se cerró durante la clase | La grabación sigue; elige otra fuente en el aviso |
| «No se pudo abrir el micrófono» | Windows: Configuración → Privacidad → Micrófono. Mac: Ajustes → Privacidad → Micrófono |
| La transcripción va retrasada | Normal en equipos sin GPU; la pasada final corrige todo al terminar |
| Sin IA | Todo funciona; agrega una clave luego y usa *Procesar clases pendientes* |
