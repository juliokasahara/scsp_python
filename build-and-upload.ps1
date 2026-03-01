# ==============================================================
# build-and-upload.ps1
# Gera o DualPlayer.exe com PyInstaller e faz upload para o MinIO
#
# Uso:
#   .\build-and-upload.ps1
#   .\build-and-upload.ps1 -SkipBuild      # só faz o upload
#   .\build-and-upload.ps1 -SkipUpload     # só gera o exe
#
# Pré-requisitos:
#   - pyinstaller instalado  (pip install pyinstaller)
#   - mc (MinIO Client) instalado e configurado
#       mc alias set minio http://localhost:9000 minioadmin minioadmin
# ==============================================================

param(
    [switch]$SkipBuild,
    [switch]$SkipUpload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Configuração ────────────────────────────────────────────────────────── #
$MINIO_ALIAS  = "minio"               # alias configurado no mc
$BUCKET       = "videos"              # mesmo bucket dos vídeos
$REMOTE_PATH  = "releases"            # prefixo dentro do bucket
$EXE_NAME     = "DualPlayer.exe"
$EXE_PATH     = "dist\$EXE_NAME"

# Nomes no MinIO (devem bater com BiomecanicaController.ALLOWED_KEYS)
$UPLOAD_AS_X64 = "biomecanica-windows-x64.exe"
$UPLOAD_AS_X86 = "biomecanica-windows-x86.exe"   # mesmo exe por enquanto
# Para gerar um x86 real, adicione outro spec e ajuste aqui.

# ── Build ────────────────────────────────────────────────────────────────── #
if (-not $SkipBuild) {
    Write-Host "`n[1/2] Gerando build com PyInstaller..." -ForegroundColor Cyan

    if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }

    pyinstaller dual_player.spec --noconfirm

    if (-not (Test-Path $EXE_PATH)) {
        Write-Error "Build falhou: $EXE_PATH não encontrado."
        exit 1
    }

    $size = [math]::Round((Get-Item $EXE_PATH).Length / 1MB, 1)
    Write-Host "  OK  $EXE_PATH ($size MB)" -ForegroundColor Green
} else {
    Write-Host "[1/2] Build ignorado (-SkipBuild)." -ForegroundColor Yellow
    if (-not (Test-Path $EXE_PATH)) {
        Write-Error "$EXE_PATH não encontrado. Rode sem -SkipBuild primeiro."
        exit 1
    }
}

# ── Upload ───────────────────────────────────────────────────────────────── #
if (-not $SkipUpload) {
    Write-Host "`n[2/2] Fazendo upload para MinIO..." -ForegroundColor Cyan

    # Verifica se mc está disponível
    if (-not (Get-Command mc -ErrorAction SilentlyContinue)) {
        Write-Error "mc (MinIO Client) não encontrado. Instale em https://min.io/docs/minio/linux/reference/minio-mc.html"
        exit 1
    }

    $remote64 = "${MINIO_ALIAS}/${BUCKET}/${REMOTE_PATH}/${UPLOAD_AS_X64}"
    $remote86 = "${MINIO_ALIAS}/${BUCKET}/${REMOTE_PATH}/${UPLOAD_AS_X86}"

    Write-Host "  → $remote64"
    mc cp $EXE_PATH $remote64

    Write-Host "  → $remote86"
    mc cp $EXE_PATH $remote86

    Write-Host "`nUpload concluído." -ForegroundColor Green
    Write-Host "Links disponíveis via:  GET /api/biomecanica/download-url?key=biomecanica-windows-x64.exe"
} else {
    Write-Host "[2/2] Upload ignorado (-SkipUpload)." -ForegroundColor Yellow
}

Write-Host "`nPronto!`n" -ForegroundColor Green
