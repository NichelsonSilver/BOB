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

---

## Antes de empezar (una sola vez)

**1. La suspensión de Windows tiene que estar apagada.** Si el equipo se
duerme, el feed se corta igual que si lo apagaras — y sin aviso.

```powershell
powercfg /change standby-timeout-ac 0
powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String "Current AC Power Setting Index"
```

Tiene que decir `0x00000000`. *(Verificado el 25-08: ya estaba en 0.)*

**2. Poner los datos al día y confirmar que no hay huecos.**

```powershell
cd C:\Users\niche\proyectos\bob\backend
uv run python -m bob.data.download --symbol ETHUSDT --timeframe 15m --repair
```

Esperado: `completitud 100.00%`, `huecos 0`. El analista también repara al
arrancar, así que este paso es una comprobación, no un requisito — pero
conviene verlo antes de dejar corriendo algo por días.

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
