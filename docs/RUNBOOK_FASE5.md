# RUNBOOK — Corrida de validación forward (Fase 5)

> Procedimiento para acumular los pronósticos que validan el target de
> volatilidad en vivo. Escrito para que una sesión nueva de Claude lo lea y
> continúe sin reconstruir contexto.
>
> **Verificado end-to-end el 2026-08-25** levantando el backend real: cada
> comando de acá se ejecutó y su salida se comprobó.

---

## Qué se está midiendo

No es "72 horas de reloj". El objetivo son **~280 pronósticos resueltos** de
ETHUSDT 15m, que es lo que hacen falta para comparar la cobertura forward
contra la del backtest. A una barra cada 15 minutos y con horizonte de 16
barras (4h), eso son ~72h de **mercado** — pero el reloj puede pararse entre
medio. Todo lo que importa vive en SQLite, no en memoria.

Tres preguntas, ninguna sobre dirección (ver la decisión del 2026-08-25 en
CLAUDE.md):

1. ¿La sigma pronosticada se pareció a la volatilidad que hubo?
2. ¿El cono conformal cubrió al precio en su nivel nominal?
3. ¿El EV proyectado se pareció al retorno neto realizado?

**Criterio de término**: `n_resolved >= 280` en el reporte del tracker.

### Cuánto tarda, y por qué son 74 h y no 70

Un pronóstico sale con cada vela cerrada (15 min) y **se resuelve 16 barras
después** (H = 16 → 4 h). Así que el reloj tiene dos tramos que se suman:

```
280 barras × 15 min          = 70 h   emitir el pronóstico n.º 280
+ 16 barras × 15 min         =  4 h   esperar a que ESE madure
                               ----
                               74 h   de mercado, sin pausas
```

Las 4 h de cola son las que se olvidan: cuando el último pronóstico se emite
todavía no hay 280 resueltos, faltan las 16 barras de su horizonte. Cada pausa
del proceso se suma encima, porque las barras que ocurrieron con el backend
abajo no se pronostican nunca.

Arrancando el **2026-08-31 ~03:30 UTC** (23:30 del 30-08 hora de Chile) y sin
pausas, el objetivo se alcanza cerca del **2026-09-03 ~05:30 UTC**, o sea la
madrugada del miércoles 3 hora local. Con pausas nocturnas de 8 h, un día más
por cada noche. El `ETA` de `watch_run.py` recalcula esto solo sobre el ritmo
observado.

---

## Antes de empezar (una sola vez)

**1. La suspensión de Windows tiene que estar apagada — en AC *y* en batería.**
Si el equipo se duerme, el feed se corta igual que si lo apagaras, y sin aviso.

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String "Power Setting Index"
```

Las **dos** líneas tienen que decir `0x00000000`.

> ⚠️ **La de batería es la que muerde.** Medido el 2026-08-30: AC estaba en 0
> (bien) pero **DC estaba en `0x000000f0` = 4 minutos**. O sea que basta
> desenchufar el equipo cuatro minutos para que la corrida muera, y el síntoma
> es el mismo que el de apagarlo: ninguno. Es el mismo pisotón que en NODO
> vació la ventana de scraping —`DisallowStartIfOnBatteries` en `True`— y por
> la misma razón: los defaults de energía de Windows están escritos para
> ahorrar batería, no para sostener una medición. Si no querés tocar el perfil
> de energía, la alternativa es dejarlo enchufado y no desenchufarlo.

**2. Poner los datos al día y confirmar que no hay huecos.**

```powershell
cd C:\Users\niche\proyectos\bob\backend
uv run python -m bob.data.download --symbol ETHUSDT --timeframe 15m --repair
uv run python -m bob.data.snapshots --symbol ETHUSDT --period 5m
```

Esperado en el primero: `completitud 100.00%`, `huecos 0`. El segundo cierra el
hueco de derivados de la pausa anterior — el analista también lo hace al
arrancar, pero conviene cerrarlo antes de empezar a contar, porque es el único
que tiene fecha de vencimiento (ver el acantilado de ~41 h más abajo).

**3. Correr el preflight.** Es la comprobación que decide si la corrida va a
producir algo, y cuesta 10 segundos:

```powershell
uv run python scripts/preflight.py
```

Ejercita el mismo camino que el arranque real —carga, ensambla la matriz y
corre `assert_tail_observable`— y **se detiene justo antes del ajuste**, que es
la parte que tarda 80 s y la única que no hace falta para responder la
pregunta. Sale con código 0 si se puede arrancar.

Existe porque el fallo que ataja es invisible desde afuera: un backend con
`status: ok`, feed conectado y `fitted: true` que no emite **nunca**, porque
una familia de features llega con retraso y deja la cola en NaN. Sin esto se
descubre horas después, notando que el contador de resueltos no sube.

**4. Que la muestra forward sea de un solo modelo.** El tracker **no segrega
por `model_version`**: si en `ForecastRecord` conviven dos versiones, las
promedia en silencio y el reporte final describe una mezcla de dos modelos que
no existe en ninguna parte.

```powershell
uv run python scripts/purge_stale_forecasts.py            # simula
uv run python scripts/purge_stale_forecasts.py --apply    # respalda a logs/ y borra
```

Compara lo que hay en la tabla contra la versión que **emitiría el código de
hoy** (no contra "la más reciente de la tabla": antes de arrancar, la versión
nueva todavía no existe ahí, y ese default conservaría justo la vieja). Si
coinciden, no hace nada.

Es la misma regla que obligó a meter XGBoost antes de arrancar la acumulación y
no después (Fase 4-bis): tocar el código del que depende el pronóstico a mitad
de la corrida mezcla dos modelos en la misma muestra y la invalida. Corolario
operativo: **mientras la corrida acumula, no se toca `models/` ni `live/`.**

*Ejemplo real del 2026-08-30*: quedaban 2 pronósticos del 25-08 etiquetados
`bob-forecast-0.1.0`, sin el sufijo `+vol=`, emitidos antes de que el refactor
de Fase 4-bis cambiara `production.py`, `forecast.py` y `analyst.py`. Dos de
280 es 0,7% de la muestra, pero la etiqueta decía la verdad: eran de un código
que ya no existe.

---

## Arrancar

```powershell
cd C:\Users\niche\proyectos\bob\backend
uv run python -m uvicorn bob.main:app --host 127.0.0.1 --port 8000
```

> ⚠️ **`uv run uvicorn ...` (sin `python -m`) falla** en este equipo con
> `uv trampoline failed to canonicalize script path`. Es el mismo problema que
> con `pytest`. Usar siempre `uv run python -m <módulo>`.

**Dónde correrlo.** Preferir una **terminal dedicada de VSCode**, en primer
plano, y no dejar que Claude lo tenga como proceso hijo. Razón: un proceso
lanzado por el CLI de Claude muere si la sesión de Claude termina —
compactación de contexto, `/clear`, o un cierre accidental— y el analista se
apagaría sin que nadie lo note. En una terminal propia solo lo mata Ctrl+C o
apagar el equipo, que son las dos cosas que sí decides tú.

El arranque **tarda ~90 segundos**: repara las tres series y ajusta el modelo
sobre ~69.500 barras. Durante ese rato el backend ya responde pero el analista
todavía no emite; es normal.

En el log tienen que aparecer, en este orden:

```
live: feed arriba — ETHUSDT 15m (snapshots on)
analista: velas al día — N hueco(s) cerrados, M nuevas
analista: derivados al día — ~500 punto(s)
analista: funding al día — ~500 fila(s)
bundle ajustado — ~65.000 filas de volatilidad, 2 cono(s), 2 dirección(es)
analista: ETHUSDT 15m listo — variante price+deriv, 81 features, ~69.500 barras
```

Dos mensajes que **son normales y no indican problema**:

- `feed: el WS está mudo en ethusdt@aggTrade, kline_15m, markPrice — se rellena
  por REST`. Es el hallazgo de la Fase 1: Binance calla esos streams y entrega
  otros por la misma conexión. El híbrido está previsto y funciona.
- `analista: vela recibida durante el ajuste inicial — se recupera de la DB al
  terminar`. El ajuste tarda más que el hueco entre velas; la vela no se pierde.

---

### Guardar el log en un archivo

El scrollback de una terminal no sobrevive tres días, y el log es la única
fuente que dice *a qué hora* pasó algo. Setear `BOB_LOG_FILE` agrega un sink de
archivo **sin sacar el de consola** — lo ves en vivo y queda en disco
(`logs/` está en `.gitignore`; rota a los 50 MB, retiene 7 días):

```powershell
cd C:\Users\niche\proyectos\bob\backend
$env:BOB_LOG_FILE = "logs\backend.log"
uv run python -m uvicorn bob.main:app --host 127.0.0.1 --port 8000
```

> El sink lo pone la aplicación a propósito, en vez de redirigir la consola.
> `... 2>&1 | Tee-Object` **no sirve en PowerShell 5.1**: loguru escribe a
> stderr y PowerShell envuelve cada línea de un ejecutable nativo en un
> `ErrorRecord`, así que el log entero sale en rojo, con formato de excepción,
> y `$?` queda en `$false` aunque el proceso esté perfecto. Se ve como una
> catástrofe que no ocurrió.

---

## Vigilar la corrida (segunda terminal)

Una corrida de ~72 h no tiene a nadie mirando `/api/health`. Este script lo
mira solo y escribe una línea por chequeo:

```powershell
cd C:\Users\niche\proyectos\bob\backend
uv run python scripts/watch_run.py --every-min 10
```

Es de solo lectura: consulta la DB y hace un GET al health, no toca nada.
Vigila cinco cosas, en orden de gravedad:

1. **Staleness de derivados** — el único fallo irreversible. Avisa a las 24 h,
   con 17 h de margen para reaccionar, no cuando ya se perdió.
2. **El analista dejó de emitir** — `fitted` en false pasado el arranque, o el
   último pronóstico congelado más de 2 barras.
3. **El feed se cayó.**
4. **`gap` creciendo** — corte de feed dentro de un horizonte.
5. **Mezcla de `model_version`** — contaminación de la muestra.

Y calcula el ETA con el **ritmo real observado**, no con el nominal: si hubo
pausas, la cuenta ya las incluye.

Salida de un tick:

```
2026-08-31 03:30:03 UTC | resueltos 41/280 abiertos 16 gap 0 | fitted=True
refit=False bars_since_fit=12 | ult.pronostico 08-31 03:00 vela 08-31 03:00
deriv 08-31 03:20 | feed=True | ETA 09-03 05:31 UTC
```

---

## Comprobar que está vivo (a los ~2 minutos)

```powershell
curl http://127.0.0.1:8000/api/health
```

Lo que hay que mirar, en este orden:

| Campo | Valor esperado | Si no |
|---|---|---|
| `feed.connected` | `true` | El feed se cayó; mirar `feed.last_error` |
| `analyst.fitted` | `true` | Sigue ajustando (espera) o falló (mirar el log) |
| `analyst.last_forecast_open_time` | un timestamp, no `null` | Está ajustado pero no emite: buscar `analysis.error` en el log |
| `analyst.feature_set` | `price+deriv` | — |

**`status: ok` no basta.** Un backend en verde que no emite nada se ve igual
que uno sano si no se mira el bloque `analyst`: el ajuste inicial tarda, un
reajuste fallido lo deja sirviendo con el modelo previo, y una familia de
features que dejó de llegar lo deja mudo. Las tres se distinguen ahí.

---

## Ver el avance (cuando quieras)

```powershell
cd C:\Users\niche\proyectos\bob\backend
uv run python -m bob.paper.tracker --symbol ETHUSDT
```

Resuelve lo que ya maduró e imprime el reporte. La línea que importa:

```
pronósticos: N resueltos, M abiertos, K descartados por huecos
```

- **`N resueltos`** es el contador contra el objetivo de ~280.
- **`K descartados por huecos`** debería quedarse en 0 o casi. Si crece, hubo
  cortes de feed dentro de horizontes; un `--repair` puede rescatarlos, porque
  un `gap` se vuelve a mirar cuando el backfill lo cierra.

Se puede correr con el backend arriba: son procesos distintos hablando con la
misma DB.

---

## Pausar (apagar el equipo)

1. `Ctrl+C` en la terminal del backend. Si no responde o se corrió en otra
   parte, matar por puerto:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

> No sirve `Get-Process python | Where-Object { $_.CommandLine -like '*uvicorn*' }`:
> `CommandLine` no es propiedad de `Process` y el filtro no encuentra nada,
> con lo que el proceso queda vivo y el siguiente arranque falla con
> `error while attempting to bind on address ... 8000`.

2. Apagar. No hace falta nada más: SQLite ya tiene todo.

---

## Reanudar

Exactamente el mismo comando de arranque. El analista repara solo:

- cierra los **huecos de velas** del rango caído (`repair_series` pide los
  huecos interiores uno por uno, porque la descarga incremental los saltaría);
- pone al día **derivados** (un request trae ~41h de grilla de 5m);
- pone al día **funding** (su tolerancia de staleness es de 8h exactas);
- reconstruye el **estado del ACI** del cono desde los registros resueltos, así
  que la adaptación acumulada no se pierde al reiniciar.

### El único límite duro de una pausa: ~41 horas

Los snapshots de derivados recuperan **~41h por request** y la ventana de
Binance es de ~30 días. Una pausa mayor a ~41h deja un hueco de derivados
**irrecuperable a ningún precio**: las 26 columnas de esa familia quedan NaN
en ese tramo para siempre.

Pausas nocturnas de 8-12h están holgadamente dentro del margen. Si vas a
parar más de un día y medio, avisa antes — hay que dejar el snapshot corriendo
por otra vía o aceptar el hueco.

---

## Cuando termine

Con `n_resolved >= 280`:

```powershell
uv run python -m bob.paper.tracker --symbol ETHUSDT
```

El reporte trae, en el **mismo formato que el backtest** (a propósito: usa
`models/metrics.py`, no métricas propias, para que los números se comparen sin
traducir):

- **Volatilidad**: R², QLIKE y razón realizada/pronosticada. Se compara contra
  el R² OOS de +0,400 del walk-forward.
- **Cono**: cobertura empírica por nivel nominal. Se compara contra 94,8% al
  95% y 79,9% al 80% del backtest.
- **Setups**: EV proyectado vs retorno neto realizado.

**Si la cobertura forward diverge mucho de la del backtest, es bandera de
sobreajuste, no de mala suerte** — y hay que decirlo en el informe, no
suavizarlo.

---

## Qué sobrevive a apagar el equipo

| | ¿sobrevive? |
|---|---|
| Pronósticos emitidos + vector de features completo | ✅ `ForecastRecord` en SQLite |
| Resultados resueltos y el reporte | ✅ se recalculan de la DB |
| Estado del ACI (`alpha_t`, cobertura) | ✅ se **deriva** de los resueltos, por eso no se persiste aparte |
| Velas, derivados y funding del rango caído | ✅ el analista los repara al arrancar |
| Barras que ocurrieron con el proceso abajo | ❌ esas no se pronostican nunca |
| Derivados de una pausa > ~41h | ❌ irrecuperable |

---

## Si algo va mal

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `bind on address ... 8000` | Quedó un backend vivo | Matar por puerto (arriba) |
| `uv trampoline failed to canonicalize` | Se usó `uv run uvicorn` | Usar `uv run python -m uvicorn` |
| `analyst.fitted` sigue en `false` tras 5 min | El ajuste falló | Buscar `no pudo arrancar` en el log |
| `analysis.error` con `columna(s) densas por debajo del 70%` | Una familia de features dejó de llegar | El mensaje nombra las columnas; casi siempre se arregla con `--repair` + reinicio |
| `analysis.error` con `warm-up` | Huecos de velas en la ventana de contexto | `uv run python -m bob.data.download --symbol ETHUSDT --timeframe 15m --repair` |
| `descartados por huecos` creciendo | Cortes de feed dentro de horizontes | `--repair`; los `gap` se re-examinan solos |
| El analista no emite y no hay error | Revisar `analyst.bars_since_fit` y el log del feed | — |
| La corrida murió y el equipo estaba desenchufado | Standby en batería (default: 4 min) | `powercfg /change standby-timeout-dc 0` |
| El reporte final mezcla dos `model_version` | Se tocó código del pronóstico a mitad de corrida | La muestra no sirve; ver §4 de "Antes de empezar" |
