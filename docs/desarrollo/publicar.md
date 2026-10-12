# Publicar en GitHub, compilar y firmar

## 1. Subir el repositorio (una vez)

El repositorio local ya está listo (historial revisado con `gitleaks`, sin secretos ni audios).
Los commits usan el correo `mcanil1999@gmail.com`; si prefieres no exponerlo, antes de subir:
GitHub → Settings → Emails → *Keep my email address private*, copia la dirección
`…@users.noreply.github.com` y ejecuta `git config user.email "<esa dirección>"` (y reescribe el
autor de los commits existentes si quieres).

En la terminal del proyecto (en Claude Code, con `!` delante):

```bash
gh auth login                    # GitHub.com → HTTPS → Login with a web browser
                                 # (contraseña y verificación en dos pasos solo en la web de GitHub)
gitleaks detect --config .gitleaks.toml   # comprobación final: «no leaks found»
gh repo create oyente --public --source . --push
```

Si no recuerdas la contraseña: github.com → *Sign in* → **Forgot password?** con `mcanil1999@gmail.com`.

Después, en GitHub → Settings:
- **Branches:** proteger `main` (PR obligatorio, CI en verde).
- **Actions → Variables:** `BUILD_IN_CLOUD=true` solo si quieres compilar el instalador en la nube
  (gasta minutos del plan; normalmente se compila en el PC Windows).

## 2. Compilar el instalador en el PC Windows

```powershell
gh repo clone oyente          # la primera vez (después: git pull)
cd oyente
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
```

El script instala lo que falte (uv, Inno Setup), ejecuta las pruebas y la auditoría de licencias,
compila con PyInstaller, hace la prueba de humo del `.exe` y genera
`dist\Oyente-Setup-<versión>.exe`. Luego sigue `docs/pruebas/manual-windows.md`.

## 3. Firma de código (recomendado antes de distribuir)

- **Windows:** certificado de firma de código (o Azure Trusted Signing). Con el certificado
  instalado en Windows: `$env:SIGN_CERT_THUMBPRINT="<huella>"` antes de ejecutar el script.
  Sin firma, Windows muestra un aviso de SmartScreen.
- **macOS:** cuenta de Apple Developer (99 USD/año). Exporta `MAC_SIGN_IDENTITY` y
  `MAC_NOTARY_PROFILE` y ejecuta `bash scripts/build-mac.sh` (firma, notariza y grapa el `.dmg`).

## 4. Credenciales de Google (para «Conectar con Google»)

1. https://console.cloud.google.com → crear proyecto (a nombre del titular).
2. *APIs y servicios → Biblioteca*: habilitar **Google Calendar API** y **Google Tasks API**.
3. *Pantalla de consentimiento OAuth*: tipo **Externo**, nombre de la app, correo de soporte,
   logo, política de privacidad (`docs/legal/privacidad.md` publicada en una URL) y los permisos:
   `calendar.events.readonly`, `calendar.calendarlist.readonly`, `calendar.app.created`, `tasks`.
4. *Credenciales → Crear ID de cliente OAuth → App de escritorio*. Descarga el JSON.
5. Copia ese JSON como `src/transcriptor/google_client.json` (está en `.gitignore`: **no se sube**)
   antes de compilar, o define la variable `TRANSCRIPTOR_GOOGLE_CLIENT` con su contenido.
6. Mientras la app esté en modo **Prueba** (hasta 100 usuarios de prueba) los permisos caducan
   cada 7 días. Para distribuir: **Publicar** y solicitar la **verificación** (permisos sensibles,
   sin auditoría de pago).

## 5. Versión y lanzamiento

1. Sube la versión en `pyproject.toml` y `src/transcriptor/__init__.py`, y anota `CHANGELOG.md`.
2. `git tag v1.0.0 && git push --tags` (ejecuta las pruebas de macOS en GitHub).
3. Compila en Windows y en Mac, y crea el Release público:
   `gh release create v1.0.0 dist/*-Setup-*.exe dist/*.dmg --notes-file CHANGELOG.md`.

## 6. Cambiar el nombre de la app

Edita `src/transcriptor/branding.toml`, reemplaza `assets/branding/` (o ejecuta
`uv run python scripts/make_icons.py`) y recompila. Los datos de los usuarios no se pierden
(ver `docs/plan/plan-fases.md` §6.5). Opcional: `gh repo rename <nuevo-nombre>`.
