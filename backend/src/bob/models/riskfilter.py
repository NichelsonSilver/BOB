"""Filtro de régimen: la skill de volatilidad como decisión de riesgo. PURO.

El target de volatilidad pasó el gate y sobrevivió la validación forward; el
direccional no. Pero los dos productos que se pueden construir encima se
apoyan en propiedades **distintas** del mismo pronóstico:

- dimensionar TP/SL con la sigma usa su **magnitud**, que está rota en la
  cola (Fase 5, punto 13: 3,6% de las barras realizaron más que el máximo que
  el modelo llegó a emitir, y el máximo realizado fue 2,75× ese techo);
- decidir *"acá no entres, o entrá con menos leverage"* usa solo su **orden**,
  que es lo que el R² contra EWMA y el QLIKE sí validan.

Este módulo mide lo segundo. La pregunta es si la sigma pronosticada señala
las barras que terminan liquidando una posición apalancada mejor que un
baseline de volatilidad, y el criterio está pre-registrado en
`docs/PREREGISTRO_FILTRO_RIESGO.md` — escrito y commiteado antes de correr
nada, que es lo que el CLAUDE.md exige de cualquier barrido.

**Nada de acá busca edge direccional.** La liquidación es un evento de
movimiento adverso, no de dirección acertada: un filtro que funcione no
vuelve positivo el EV —eso sigue siendo álgebra imposible— sino que baja la
probabilidad de ruina, que para un usuario que ya fue liquidado es el número
que importa.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from bob.models.projection import DEFAULT_MMR, LeverageProfile, liquidation_price

#: Largo de bloque por defecto para el bootstrap: debe ser el horizonte, que
#: es la distancia a la que dos setups dejan de compartir barras.
DEFAULT_N_BOOT = 5_000


# --------------------------------------------------------------------------- #
# Excursión adversa
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AdverseExcursion:
    """Cuánto llegó a moverse el precio EN CONTRA de cada dirección.

    Una sola cantidad por barra y por dirección, independiente del leverage:
    la liquidación a leverage L es después un simple umbral sobre esto. Eso
    da la curva completa de leverage con una pasada y —más importante— saca
    de la mesa la tentación de elegir el leverage después de ver el resultado.
    """

    long: np.ndarray  # float64, fracción positiva
    short: np.ndarray  # float64, fracción positiva
    entry: np.ndarray  # float64, open de la barra i+1
    valid: np.ndarray  # bool: la barra tiene un setup completo

    def for_direction(self, direction: str) -> np.ndarray:
        if direction == "long":
            return self.long
        if direction == "short":
            return self.short
        raise ValueError(f"dirección inválida: {direction!r}")


def adverse_excursion(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, horizon: int
) -> AdverseExcursion:
    """Máxima excursión adversa de un setup abierto en cada barra.

    Misma convención que `labeling.triple_barrier_labels`: se entra al open de
    la barra `i+1` y se recorren las barras `i+1 .. i+horizon`. Compartir la
    convención con el gate no es cosmético — si el simulador entrara en otro
    punto, cualquier diferencia contra el gate sería atribuible a eso y no al
    filtro.
    """
    if horizon < 1:
        raise ValueError("el horizonte debe ser >= 1")
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    n = open_.size
    if not (high.size == low.size == n):
        raise ValueError("open, high y low deben tener el mismo largo")

    mae_long = np.full(n, np.nan, dtype=np.float64)
    mae_short = np.full(n, np.nan, dtype=np.float64)
    entry_px = np.full(n, np.nan, dtype=np.float64)
    valid = np.zeros(n, dtype=bool)

    # Se necesita open[i+1] y hasta high/low[i+horizon].
    last = n - horizon - 1
    if last < 0:
        return AdverseExcursion(mae_long, mae_short, entry_px, valid)

    idx = np.arange(last + 1)
    entry = open_[idx + 1]
    # Ventanas i+1 .. i+horizon, vectorizadas.
    offsets = np.arange(horizon)
    win = idx[:, None] + 1 + offsets[None, :]
    lo_win = low[win]
    hi_win = high[win]

    ok = np.isfinite(entry) & (entry > 0)
    ok &= np.all(np.isfinite(lo_win), axis=1) & np.all(np.isfinite(hi_win), axis=1)

    with np.errstate(invalid="ignore"):
        mae_long[idx] = np.where(ok, 1.0 - lo_win.min(axis=1) / entry, np.nan)
        mae_short[idx] = np.where(ok, hi_win.max(axis=1) / entry - 1.0, np.nan)
    entry_px[idx] = np.where(ok, entry, np.nan)
    valid[idx] = ok

    return AdverseExcursion(mae_long, mae_short, entry_px, valid)


# --------------------------------------------------------------------------- #
# Liquidación
# --------------------------------------------------------------------------- #
def liquidation_distance(
    leverage: float, direction: str, mmr: float = DEFAULT_MMR
) -> float:
    """Distancia relativa del precio de entrada a la liquidación.

    Se deriva de `projection.liquidation_price` evaluada en un precio de 1.0
    en vez de reescribir la fórmula: el número que este módulo usa para
    contar liquidaciones queda garantizadamente igual al que el dashboard le
    muestra al usuario, con sus mismas simplificaciones declaradas (margen
    aislado, una posición, sin fees de cierre).
    """
    profile = LeverageProfile(leverage=leverage, maintenance_margin_rate=mmr)
    liq = liquidation_price(1.0, direction, profile)
    return (1.0 - liq) if direction == "long" else (liq - 1.0)


def liquidates(
    mae: np.ndarray, leverage: float, direction: str, mmr: float = DEFAULT_MMR
) -> np.ndarray:
    """¿La excursión adversa alcanzó la liquidación? NaN cuenta como False."""
    d = liquidation_distance(leverage, direction, mmr)
    mae = np.asarray(mae, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        return np.asarray(np.nan_to_num(mae, nan=-np.inf) >= d)


# --------------------------------------------------------------------------- #
# El filtro
# --------------------------------------------------------------------------- #
def causal_quantile_threshold(
    values: np.ndarray, q: float, min_warmup: int
) -> np.ndarray:
    """Cuantil `q` de los valores ESTRICTAMENTE anteriores a cada posición.

    El umbral del filtro no puede salir del cuantil de la muestra completa:
    eso usaría el futuro para decidir qué es "alto" hoy, que es el lookahead
    de la regla 5 y sería invisible —el filtro se vería mejor de lo que es y
    ningún test fallaría—. Las primeras `min_warmup` posiciones salen NaN: sin
    historia suficiente no hay umbral, y una barra sin umbral no se filtra.

    Implementación deliberadamente directa (un `np.quantile` por posición).
    Es O(n²) en operaciones de C y tarda segundos sobre 70k barras, contra una
    estructura incremental que sería más rápida y mucho menos auditable. Acá
    la simplicidad vale más que la velocidad: es código que decide si el
    producto existe.
    """
    if not 0.0 < q < 1.0:
        raise ValueError("q debe estar en (0, 1)")
    if min_warmup < 1:
        raise ValueError("min_warmup debe ser >= 1")
    values = np.asarray(values, dtype=np.float64)
    n = values.size
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(min_warmup, n):
        past = values[:i]
        past = past[np.isfinite(past)]
        if past.size >= min_warmup:
            out[i] = np.quantile(past, q)
    return out


@dataclass(frozen=True)
class LiftResult:
    """Qué evitó el filtro, a qué costo, y si eso supera al azar.

    `lift` es la métrica que decide: la fracción de liquidaciones evitadas
    dividida por la fracción de barras rechazadas. Un filtro que rechaza al
    azar da **exactamente 1,0** a cualquier tasa de rechazo — por eso el
    listón es 1 y no 0, y por eso valores de `q` distintos son comparables
    entre sí: la métrica ya normaliza por lo que el filtro cuesta.
    """

    label: str
    n: int
    n_rejected: int
    n_events: int
    n_events_rejected: int
    lift: float
    reject_rate: float
    capture_rate: float
    #: Fracción de barras que NO liquidaban y el filtro rechazó igual. Es el
    #: costo operativo: un lift alto que rechaza toda la actividad no sirve.
    false_reject_rate: float

    @property
    def beats_chance(self) -> bool:
        return self.lift > 1.0


def _as_setups(rejected: np.ndarray, events: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Normaliza a `rejected` (n,) y `events` (n, k).

    `k` son los setups que abre la misma barra — long y short cuando el
    pre-registro pide las direcciones agrupadas. La barra es el eje de
    bloques, y por eso las dos direcciones viajan juntas en vez de
    concatenarse: apilarlas en un solo vector de 2n rompería la estructura
    de solapamiento justo en la juntura, y el bootstrap de bloques dejaría de
    medir lo que dice medir.
    """
    rejected = np.asarray(rejected, dtype=bool)
    events = np.asarray(events, dtype=bool)
    if events.ndim == 1:
        events = events[:, None]
    if events.ndim != 2 or events.shape[0] != rejected.size:
        raise ValueError("events debe ser (n,) o (n, k) con el mismo n que rejected")
    return rejected, events


def _lift_from(rejected: np.ndarray, events: np.ndarray) -> float:
    total = int(events.sum())
    if rejected.size == 0 or total == 0:
        return float("nan")
    reject_rate = float(rejected.mean())
    if reject_rate <= 0.0:
        return float("nan")
    return (float(events[rejected].sum()) / total) / reject_rate


def filter_lift(
    rejected: np.ndarray, events: np.ndarray, label: str = "modelo"
) -> LiftResult:
    """Lift del filtro `rejected` sobre los eventos `events`, ya alineados.

    `events` puede ser (n,) —una dirección— o (n, k) —varias por barra—.
    """
    rejected, events = _as_setups(rejected, events)
    n, k = events.shape
    total = int(events.sum())
    captured = int(events[rejected].sum()) if total else 0
    n_setups = n * k
    n_safe = n_setups - total
    rejected_safe = int(rejected.sum()) * k - captured
    return LiftResult(
        label=label,
        n=n,
        n_rejected=int(rejected.sum()),
        n_events=total,
        n_events_rejected=captured,
        lift=_lift_from(rejected, events),
        reject_rate=float(rejected.mean()) if n else float("nan"),
        capture_rate=(captured / total) if total else float("nan"),
        false_reject_rate=(rejected_safe / n_safe) if n_safe > 0 else float("nan"),
    )


# --------------------------------------------------------------------------- #
# Incertidumbre: bootstrap por bloques móviles
# --------------------------------------------------------------------------- #
def block_index_samples(
    n: int, block: int, n_boot: int, seed: int, chunk: int
) -> Iterator[np.ndarray]:
    """Genera índices de bootstrap por bloques móviles, por lotes.

    Misma convención que `paper.forward_eval.coverage_interval` —bloques de
    largo `block`, `ceil(n/block)` bloques, recorte al largo original— para
    que los ICs de este reporte y los del forward sean el mismo objeto
    estadístico. Hay un test que lo verifica reproduciendo esa función.

    Se entrega por lotes porque acá `n` es ~70k: materializar 5.000 × 70.000
    índices de una vez son varios GB.

    **Las tiradas del RNG se hacen todas en UNA llamada**, antes de lotear.
    Pedirlas por lote haría que el resultado dependiera de `chunk` —el mismo
    seed con otro tamaño de lote daría otro intervalo—, o sea un parámetro de
    memoria decidiendo una cifra del reporte. El loteo solo afecta a cuánta
    RAM se usa para expandir los índices, nunca a qué se sortea.
    """
    block = max(1, min(int(block), n))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    all_starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    offsets = np.arange(block)
    for lo in range(0, n_boot, chunk):
        starts = all_starts[lo : lo + chunk]
        size = starts.shape[0]
        yield (starts[:, :, None] + offsets[None, None, :]).reshape(size, -1)[:, :n]


def _bootstrap_lift(
    rejected: np.ndarray, events: np.ndarray, idx: np.ndarray
) -> np.ndarray:
    """Lift de cada remuestreo. `idx` es (B, n) de índices de BARRA."""
    r = rejected[idx]  # (B, n)
    e = events[idx]  # (B, n, k)
    total = e.sum(axis=(1, 2))
    captured = (e & r[:, :, None]).sum(axis=(1, 2))
    reject_rate = r.mean(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        capture = np.where(total > 0, captured / np.maximum(total, 1), np.nan)
        return np.asarray(np.where(reject_rate > 0, capture / reject_rate, np.nan))


def lift_interval(
    rejected: np.ndarray,
    events: np.ndarray,
    block: int,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    level: float = 0.95,
    chunk: int = 200,
) -> tuple[float, float]:
    """IC del lift por bootstrap de bloques móviles.

    `block` debe ser el horizonte: dos setups separados por menos comparten
    barras, y tratarlos como independientes encoge el error estándar ~4× —
    es la corrección que en la Fase 5 dio vuelta la lectura del cono.
    """
    rejected, events = _as_setups(rejected, events)
    n = rejected.size
    if n == 0:
        return (float("nan"), float("nan"))
    stats: list[np.ndarray] = []
    for idx in block_index_samples(n, block, n_boot, seed, chunk):
        stats.append(_bootstrap_lift(rejected, events, idx))
    draws = np.concatenate(stats)
    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return (float("nan"), float("nan"))
    tail = (1.0 - level) / 2.0 * 100.0
    lo, hi = np.percentile(draws, [tail, 100.0 - tail])
    return (float(lo), float(hi))


def lift_difference_interval(
    rejected_a: np.ndarray,
    rejected_b: np.ndarray,
    events: np.ndarray,
    block: int,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    level: float = 0.95,
    chunk: int = 200,
) -> tuple[float, float]:
    """IC de `lift(a) − lift(b)`, **pareado** sobre los mismos remuestreos.

    Pareado y no dos ICs por separado: los dos filtros ven exactamente las
    mismas barras, así que sus errores están correlacionados y comparar dos
    intervalos marginales que se solapan NO permite concluir que no hay
    diferencia. Es el mismo motivo por el que el proyecto usa Diebold-Mariano
    en vez de comparar dos RMSE sueltos.
    """
    a, events = _as_setups(rejected_a, events)
    b = np.asarray(rejected_b, dtype=bool)
    if b.size != a.size:
        raise ValueError("los dos filtros deben tener el mismo largo")
    n = a.size
    if n == 0:
        return (float("nan"), float("nan"))
    stats: list[np.ndarray] = []
    for idx in block_index_samples(n, block, n_boot, seed, chunk):
        stats.append(
            _bootstrap_lift(a, events, idx) - _bootstrap_lift(b, events, idx)
        )
    draws = np.concatenate(stats)
    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return (float("nan"), float("nan"))
    tail = (1.0 - level) / 2.0 * 100.0
    lo, hi = np.percentile(draws, [tail, 100.0 - tail])
    return (float(lo), float(hi))


def apply_filter(
    score: np.ndarray, threshold: np.ndarray, eligible: np.ndarray
) -> np.ndarray:
    """Rechaza donde el score supera su umbral causal.

    Una barra sin umbral (warm-up) o sin score no se rechaza: el filtro solo
    puede actuar donde tiene información, y marcarla como rechazada inflaría
    la tasa de rechazo con barras sobre las que no opinó.
    """
    score = np.asarray(score, dtype=np.float64)
    threshold = np.asarray(threshold, dtype=np.float64)
    eligible = np.asarray(eligible, dtype=bool)
    with np.errstate(invalid="ignore"):
        rej = np.isfinite(score) & np.isfinite(threshold) & (score >= threshold)
    return np.asarray(rej & eligible)
