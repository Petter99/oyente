# Compila el instalador de Windows en el PC Windows.
#
#   1. git pull
#   2. powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
#
# Resultado: dist\<Nombre>-Setup-<versión>.exe
# Requisitos (el script instala los que falten con winget): uv, Inno Setup 6.
# Firma opcional: define $env:SIGN_CERT_THUMBPRINT (certificado en el almacén de Windows) o usa
# Azure Trusted Signing con $env:AZURE_SIGNING_* (ver docs/desarrollo/publicar.md).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Need($cmd, $wingetId) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Instalando $wingetId..."
        winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")
    }
}

Need "uv" "astral-sh.uv"
$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) { $Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $Iscc)) {
    winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements
    $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}

Write-Host "== Dependencias"
uv sync --group build --frozen

Write-Host "== Pruebas rápidas"
uv run pytest -q
if ($LASTEXITCODE -ne 0) { throw "Las pruebas fallaron; no se compila." }

Write-Host "== Licencias"
uv run python scripts/check_licenses.py
if ($LASTEXITCODE -ne 0) { throw "Auditoría de licencias fallida." }

$Name = uv run python -c "from transcriptor.branding import branding; print(branding().ejecutable)"
$Version = uv run python -c "import transcriptor; print(transcriptor.__version__)"
Write-Host "== Compilando $Name $Version"

uv run python scripts/build_extension.py dist/extension
uv run pyinstaller packaging/app.spec --noconfirm --clean --distpath dist/app --workpath build/pyinstaller
$AppDir = Join-Path $Root "dist\app\$Name"
$Exe = Join-Path $AppDir "$Name.exe"

function Sign($file) {
    if ($env:SIGN_CERT_THUMBPRINT) {
        $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" | Select-Object -Last 1
        & $signtool.FullName sign /sha1 $env:SIGN_CERT_THUMBPRINT /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $file
    } else {
        Write-Host "(sin firma: Windows mostrará un aviso de SmartScreen al instalar)"
    }
}
Sign $Exe

Write-Host "== Prueba de humo del ejecutable"
uv run python scripts/smoke_test.py $Exe
if ($LASTEXITCODE -ne 0) { throw "La prueba de humo falló." }

Write-Host "== Instalador"
& $Iscc "/DAppName=$Name" "/DAppVersion=$Version" "/DAppExe=$Name.exe" "/DSourceDir=$AppDir" "/DOutputDir=$Root\dist" packaging\windows\installer.iss
$Setup = Join-Path $Root "dist\$Name-Setup-$Version.exe"
Sign $Setup
Write-Host "Listo: $Setup"
