# Corrida diaria de MAREA: las fuentes que no se recuperan despues (M5.5).
#
#   - Deribit: el libro de opciones no tiene historia. Un dia sin correr es
#     un dia perdido para siempre.
#   - DefiLlama: no publica vintages; la precision point-in-time de las
#     stablecoins depende de que cada corrida registre lo que habia ese dia.
#   - Funding de Binance: se recupera, pero es barato y deja el monitor al dia.
#
# Se programa a las 12:00 LOCALES y no a medianoche: el equipo suele estar
# apagado de noche (la tarea del panel, a las 21:05, corre casi siempre de
# recuperacion a la manana siguiente). Con StartWhenAvailable, si se pierde
# la hora corre apenas el equipo vuelve. La hora exacta de cada foto queda
# en captured_at (UTC), asi que una corrida tardia no se confunde con otra.
#
# EL LOG VA EN UTC A PROPOSITO, por la misma razon que panel_daily.ps1.
#
# TODO ESTE ARCHIVO ES ASCII, Y SE GUARDA CON BOM. PowerShell 5.1 lee un .ps1
# sin BOM como ANSI y rompe con acentos (ver panel_daily.ps1).

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$backend = "C:\Users\niche\proyectos\bob\backend"
$uv = "C:\Users\niche\AppData\Local\Programs\Python\Python314\Scripts\uv.exe"
$logDir = Join-Path $backend "logs"
$log = Join-Path $logDir "marea_diario.log"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }

function Write-Log($linea) {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value "$ts UTC  $linea" -Encoding utf8
}

Set-Location $backend
Write-Log "=== arranque ==="

$t0 = Get-Date
$salida = & $uv run python -m marea.data.ingest diario 2>&1
$codigo = $LASTEXITCODE
$seg = [math]::Round(((Get-Date) - $t0).TotalSeconds, 0)

# Una linea por foto de opciones (BTC / ETH) y el resumen de las tres fuentes.
$fotos = $salida | Select-String -Pattern "^(BTC|ETH)\s"
foreach ($f in $fotos) {
    $t = $f.Line.Trim()
    Write-Log "deribit: $t"
}
$resumen = $salida | Select-String -Pattern "resumen diario" | Select-Object -Last 1
if ($resumen) {
    $texto = $resumen.Line.Trim()
    Write-Log "$texto  ($seg s)"
} else {
    Write-Log "sin linea de resumen  ($seg s)"
}
if ($codigo -ne 0) {
    Write-Log "FALLO con codigo $codigo"
    $fallas = $salida | Select-String -Pattern "error|Error|Traceback" | Select-Object -First 5
    foreach ($f in $fallas) {
        $t = $f.Line.Trim()
        Write-Log "  $t"
    }
}

Write-Log "=== fin ==="
exit $codigo
