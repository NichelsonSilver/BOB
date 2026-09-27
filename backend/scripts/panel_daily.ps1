# Corrida diaria de la validacion forward del panel (Fase 10-bis).
#
# Se programa con Task Scheduler para las 21:05 LOCALES, que con Chile en
# UTC-3 son las 00:05 UTC: cinco minutos despues de que cierre la vela diaria.
# La gracia para que la emision cuente como forward es de 2h desde ese cierre
# (FORWARD_GRACE_MS), asi que hay margen de sobra para los ~12 min de ingesta.
#
# Cuando termine el horario de verano de Chile el offset pasa a UTC-4 y las
# 21:05 locales seran 01:05 UTC. Sigue estando despues del cierre y dentro de
# la gracia, con 55 min de margen. No hay que reprogramar nada.
#
# EL LOG VA EN UTC A PROPOSITO. La Fase 5 perdio un analisis entero por
# atribuir barras con un log en hora local atravesando el cambio de horario
# del 06-09: un offset fijo reasignaba tramos completos entre "proceso abajo"
# y "analista mudo". Con UTC no hay nada que convertir.
#
# TODO ESTE ARCHIVO ES ASCII, Y SE GUARDA CON BOM. PowerShell 5.1 lee un .ps1
# sin BOM como ANSI: con acentos o guiones largos adentro, el parser rompe las
# comillas y la tarea falla sin escribir una sola linea de log. Verificado.

$ErrorActionPreference = "Continue"

# La salida de Python es UTF-8, pero PowerShell 5.1 la decodifica con el
# codepage de consola (850/437 en este equipo) y los acentos entran al log
# como mojibake: "s??mbolos" en vez de "simbolos". El log es el registro de
# auditoria de 180 dias, asi que se fuerza UTF-8 en la captura.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$backend = "C:\Users\niche\proyectos\bob\backend"
$uv = "C:\Users\niche\AppData\Local\Programs\Python\Python314\Scripts\uv.exe"
$logDir = Join-Path $backend "logs"
$log = Join-Path $logDir "panel_daily.log"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }

function Write-Log($linea) {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $log -Value "$ts UTC  $linea" -Encoding utf8
}

Set-Location $backend
Write-Log "=== arranque ==="

# --- 1. Ingesta de klines ------------------------------------------------- #
$t0 = Get-Date
$salida = & $uv run python -m bob.data.download_panel --ingest --concurrency 4 2>&1
$codigo = $LASTEXITCODE
$seg = [math]::Round(((Get-Date) - $t0).TotalSeconds, 0)

$resumen = $salida | Select-String -Pattern "velas escritas" | Select-Object -Last 1
if ($resumen) {
    $texto = $resumen.Line.Trim()
    Write-Log "ingesta: $texto  ($seg s)"
} else {
    Write-Log "ingesta: sin linea de resumen  ($seg s)"
}

if ($codigo -ne 0) {
    Write-Log "ingesta FALLO con codigo $codigo - se emite igual sobre lo que haya en DB"
}
if ($salida | Select-String -Pattern "CORRIDA INCOMPLETA") {
    Write-Log "ingesta INCOMPLETA: algunos simbolos fallaron"
}

# --- 2. Emision del pronostico -------------------------------------------- #
# Se emite aunque la ingesta haya fallado: si no hay vela nueva, --emit es
# idempotente y escribe 0 filas. Lo que NO hay que hacer es saltearla, porque
# una ingesta parcial igual puede haber traido la vela del dia.
$t1 = Get-Date
$salida2 = & $uv run python -m bob.paper.panel_forward --emit 2>&1
$codigo2 = $LASTEXITCODE
$seg2 = [math]::Round(((Get-Date) - $t1).TotalSeconds, 0)

$emision = $salida2 | Select-String -Pattern "filas nuevas" | Select-Object -Last 1
if ($emision) {
    $texto2 = $emision.Line.Trim()
    Write-Log "emision: $texto2  ($seg2 s)"
    # El estado que importa: si salio BACKFILL, esa fecha NO cuenta como
    # evidencia forward y el IC confirmatorio no la va a usar.
    if ($texto2 -match "BACKFILL") {
        Write-Log "  ^^ NO cuenta como forward: emitido fuera de la ventana de gracia"
    } else {
        Write-Log "  ^^ cuenta como forward"
    }
} else {
    Write-Log "emision: sin linea de resumen  ($seg2 s)"
    $fallas = $salida2 | Select-String -Pattern "Error|Traceback|raise" | Select-Object -First 3
    foreach ($f in $fallas) {
        $t = $f.Line.Trim()
        Write-Log "  $t"
    }
}

if ($codigo2 -ne 0) { Write-Log "emision FALLO con codigo $codigo2" }

Write-Log "=== fin ==="
exit $codigo2
