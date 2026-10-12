#!/usr/bin/env bash
# Compila la app de macOS (.app) y el .dmg.
#   bash scripts/build-mac.sh
# Firma y notarización opcionales con:
#   MAC_SIGN_IDENTITY="Developer ID Application: Nombre (TEAMID)"
#   MAC_NOTARY_PROFILE=<perfil creado con: xcrun notarytool store-credentials>
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --group build --frozen
uv run pytest -q
uv run python scripts/check_licenses.py

NAME=$(uv run python -c "from transcriptor.branding import branding; print(branding().ejecutable)")
VERSION=$(uv run python -c "import transcriptor; print(transcriptor.__version__)")
echo "== Compilando $NAME $VERSION"

uv run python scripts/build_extension.py dist/extension
uv run pyinstaller packaging/app.spec --noconfirm --clean --distpath dist/app --workpath build/pyinstaller
APP="dist/app/$NAME.app"

if [[ -n "${MAC_SIGN_IDENTITY:-}" ]]; then
  codesign --deep --force --options runtime --timestamp \
    --entitlements packaging/macos/entitlements.plist --sign "$MAC_SIGN_IDENTITY" "$APP"
else
  echo "(sin firma: macOS pedirá abrirla con clic derecho → Abrir la primera vez)"
fi

echo "== Prueba de humo"
uv run python scripts/smoke_test.py "$APP/Contents/MacOS/$NAME"

DMG="dist/$NAME-$VERSION.dmg"
rm -f "$DMG"
hdiutil create -volname "$NAME" -srcfolder "$APP" -ov -format UDZO "$DMG"
if [[ -n "${MAC_SIGN_IDENTITY:-}" && -n "${MAC_NOTARY_PROFILE:-}" ]]; then
  codesign --sign "$MAC_SIGN_IDENTITY" --timestamp "$DMG"
  xcrun notarytool submit "$DMG" --keychain-profile "$MAC_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi
echo "Listo: $DMG"
