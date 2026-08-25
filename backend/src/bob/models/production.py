"""Ajuste de producción — el puente entre el experimento y el vivo. PURO.

`experiment.py` entrena un modelo por fold y lo tira: su producto son
métricas, no un predictor. Para operar en vivo hace falta lo contrario —
**un** ajuste sobre toda la historia disponible que se pueda consultar en la
barra que acaba de cerrar. Este módulo es esa pieza, y sigue siendo puro
(regla 3): recibe arrays, devuelve un forecast; no sabe qué es SQLite.

Qué se ajusta y qué NO se emite
--------------------------------
Tras la decisión del 2026-08-25 (CLAUDE.md), el bundle ajusta tres cosas
pero solo dos sostienen lo que el usuario ve:

* `VolatilityModel` — **sí**. Pasó el gate: le gana a EWMA, GARCH y HAR-RV
  con p=0.0000 en Diebold-Mariano. De acá salen TP, SL, EV y la distancia a
  liquidación.
* `ConformalReturnInterval` — **sí**. El cono de precio con cobertura
  medida, no asumida.
* `BarrierProbabilityModel` — se ajusta y se **registra**, no se emite. El
  gate lo rechazó por discriminación (AUC 0.52) y la regla 2 dice que un KPI
  sin discriminación demostrada no se muestra como operable. Se guarda para
  poder medirlo forward: apagarlo del todo haría imposible saber si algún día
  mejora.

Las dos sigmas, y por qué el forecast NO es la sigma del etiquetado
-------------------------------------------------------------------
Hay dos números de volatilidad en juego y confundirlos es el error silencioso
más caro de esta fase:

* `sigma_backward` = `labeling.target_volatility` — realizada de la ventana
  pasada, escalada por sqrt(H). Es la que **usó el etiquetado** para poner las
  barreras del triple-barrier, o sea la que define el setup del que habla la
  probabilidad del KPI 1.
* `sigma_forecast` = salida del `VolatilityModel` — el pronóstico de la
  volatilidad que **viene**. Es el target que pasó el gate.

La decisión del 25-08 manda dimensionar TP y SL con la pronosticada, y eso es
lo que hace `build_analysis`. La consecuencia hay que decirla en voz alta: la
probabilidad que se registra al lado describe barreras a `sigma_backward`, no
las que se muestran. Por eso `MarketAnalysis` lleva las dos sigmas, su razón,
y `probability_matches_barriers=False` cuando divergen. No se emite señal con
esa probabilidad — pero si algún día se emite, ese campo es el que dice si el
número corresponde al setup dibujado.

El techo aritmético del EV
---------------------------
Conviene tenerlo presente antes de leer un EV negativo como un bug. Para un
camino sin deriva con barreras a +a y −b, la probabilidad de tocar la de
arriba primero es b/(a+b), y entonces:

    EV_bruto = [b/(a+b)]·a − [a/(a+b)]·b = 0     para TODO a, b
    EV_neto  = −costo

O sea: **sin edge direccional el EV neto es exactamente menos el costo, con
cualquier configuración de barreras**. Mover el TP o cambiar el ratio
riesgo/beneficio no lo levanta — reordena probabilidad y pago en la
proporción exacta que deja el bruto en cero. Y el edge direccional es
justamente lo que el gate rechazó.

Por eso lo que esta proyección entrega no es expectativa positiva sino
**dimensionamiento y riesgo**: barreras escaladas a la volatilidad que viene,
distancia a la liquidación en sigmas, leverage máximo seguro y un cono con
cobertura medida. El EV se devuelve igual, con su probabilidad de equilibrio
al lado, porque es el listón que habría que superar — no una promesa.

Purga en la cola
-----------------
El target de volatilidad de la barra i mira las barras i+1..i+H, así que las
últimas H filas tienen label incompleto. `forward_volatility` y
`forward_return` devuelven NaN ahí y el filtro de finitud las descarta solo:
el bundle nunca entrena con una etiqueta que todavía no terminó de ocurrir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from loguru import logger

from bob.models.experiment import MODEL_VERSION, ExperimentConfig
from bob.models.forecast import (
    BarrierProbabilityModel,
    ConformalReturnInterval,
    VolatilityModel,
)
from bob.models.labeling import (
    forward_return,
    forward_volatility,
    target_volatility,
    triple_barrier_labels,
    uniqueness_weights,
)
from bob.models.projection import (
    LeverageProfile,
    SetupProjection,
    breakeven_probability,
    funding_cost_pct,
    project_setup,
)

EPS = 1e-12

#: Mínimo de filas utilizables para ajustar. Bajo esto el forecast sería una
#: opinión con forma de número: mejor no producirlo que producirlo débil.
MIN_FIT_ROWS = 2_000


@dataclass
class OnlineConformalCone:
    """Cono conformal con ACI en línea — la versión operable de CQR.

    `ConformalReturnInterval.predict_interval_adaptive` recorre un test
    completo con las verdades ya conocidas: sirve para medir, no para vivir.
    En vivo la retroalimentación llega H barras tarde, cuando el tracker
    resuelve el registro, así que el estado de alpha vive acá y se actualiza
    con `observe()`.

    La aritmética es **la misma** que la del experimento (misma escala, misma
    actualización de alpha, mismos clips). Si divergiera, la cobertura medida
    en el gate dejaría de describir la que el usuario recibe.
    """

    model: ConformalReturnInterval
    alpha: float
    gamma: float = 0.01
    alpha_t: float = field(init=False)
    n_observed: int = 0
    n_covered: int = 0

    def __post_init__(self) -> None:
        self.alpha_t = self.alpha

    @property
    def scale(self) -> float:
        """Ensanche/estrechamiento vigente respecto del intervalo base."""
        return max(0.1, 1.0 + (self.alpha - self.alpha_t) / max(self.alpha, EPS))

    def interval(self, x: np.ndarray) -> tuple[float, float]:
        """Intervalo para el retorno log de UNA fila de features."""
        lo_b, hi_b = self.model.predict_interval(x.reshape(1, -1))
        center = float((hi_b[0] + lo_b[0]) / 2.0)
        half = float((hi_b[0] - lo_b[0]) / 2.0) * self.scale
        return center - half, center + half

    def observe(self, y_true: float, lo: float, hi: float) -> bool:
        """Registra el resultado de un intervalo ya emitido y mueve alpha.

        Devuelve si cubrió. Se llama cuando el horizonte cerró — nunca antes:
        alimentarlo con el retorno parcial de una barra en curso sería
        exactamente el lookahead que el proyecto persigue.
        """
        covered = bool(lo <= y_true <= hi)
        err = 0.0 if covered else 1.0
        self.alpha_t = float(
            np.clip(self.alpha_t + self.gamma * (self.alpha - err), 1e-3, 0.999)
        )
        self.n_observed += 1
        self.n_covered += int(covered)
        return covered

    @property
    def empirical_coverage(self) -> float:
        if self.n_observed == 0:
            return float("nan")
        return self.n_covered / self.n_observed


@dataclass(frozen=True)
class ConeBand:
    """Un nivel del cono, en retorno log y en precio."""

    alpha: float
    nominal: float
    ret_lo: float
    ret_hi: float
    price_lo: float
    price_hi: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "nominal": self.nominal,
            "ret_lo": self.ret_lo,
            "ret_hi": self.ret_hi,
            "price_lo": self.price_lo,
            "price_hi": self.price_hi,
        }


@dataclass(frozen=True)
class BarForecast:
    """Salida cruda del bundle para una barra: sigmas, cono y probabilidades."""

    open_time: int
    reference_price: float
    sigma_forecast: float
    sigma_backward: float
    cones: dict[float, tuple[float, float]]  # alpha -> (ret_lo, ret_hi)
    probabilities: dict[str, float]  # direction -> P(TP antes que SL)

    @property
    def sigma_ratio(self) -> float:
        """Pronosticada / realizada pasada. >1 = viene más movimiento del habitual."""
        if self.sigma_backward <= 0:
            return float("nan")
        return self.sigma_forecast / self.sigma_backward


@dataclass(frozen=True)
class MarketAnalysis:
    """Todo lo que BOB tiene para decir de una barra cerrada.

    Es el objeto que viaja al dashboard, al WS y a la DB. Lleva el feature
    vector completo por la regla 10: sin él no hay post-mortem.
    """

    symbol: str
    timeframe: str
    open_time: int
    reference_price: float

    sigma_forecast: float
    sigma_backward: float
    sigma_ratio: float
    barrier_sigma_source: str  # "forecast" | "backward"

    cones: list[ConeBand]
    projections: dict[str, SetupProjection]
    probabilities: dict[str, float]

    #: Falso mientras las barreras se dimensionen con una sigma distinta de la
    #: que usó el etiquetado. Ver el docstring del módulo.
    probability_matches_barriers: bool
    #: Regla 2: el KPI 1 no pasó discriminación, así que nunca sale operable.
    probability_calibrated: bool

    model_version: str
    fit_through_ms: int
    n_train: int
    features: dict[str, float]

    def as_dict(self, *, include_features: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": self.open_time,
            "reference_price": self.reference_price,
            "sigma_forecast": self.sigma_forecast,
            "sigma_backward": self.sigma_backward,
            "sigma_ratio": self.sigma_ratio,
            "barrier_sigma_source": self.barrier_sigma_source,
            "cones": [c.as_dict() for c in self.cones],
            "projections": {d: p.as_dict() for d, p in self.projections.items()},
            "probabilities": self.probabilities,
            "probability_matches_barriers": self.probability_matches_barriers,
            "probability_calibrated": self.probability_calibrated,
            "model_version": self.model_version,
            "fit_through_ms": self.fit_through_ms,
            "n_train": self.n_train,
        }
        if include_features:
            out["features"] = self.features
        return out


@dataclass
class ForecastBundle:
    """Los tres modelos ajustados sobre una historia, listos para consultar.

    Se construye con `fit_bundle`. Deliberadamente **no** se serializa a
    disco: reajustar sobre 69k barras toma segundos, y un pickle de sklearn
    es una bomba de versiones que envejece peor que el costo de reentrenar.
    """

    config: ExperimentConfig
    feature_names: list[str]
    dense_idx: np.ndarray
    fit_through_ms: int
    n_train: int
    timeframe_ms: int

    volatility: VolatilityModel
    cones: dict[float, OnlineConformalCone]
    barrier: dict[str, BarrierProbabilityModel]

    model_version: str = MODEL_VERSION

    def row_is_usable(self, x: np.ndarray) -> bool:
        """¿La fila tiene completas las columnas densas? Sin eso no se predice.

        Las de cobertura parcial (near-touch) pueden faltar: el GBM las trata
        como rama propia. Las densas no: un NaN ahí es un hueco de datos, y
        pronosticar sobre un hueco es inventar.
        """
        return bool(np.all(np.isfinite(x[self.dense_idx])))

    def missing_dense(self, x: np.ndarray) -> list[str]:
        """Nombres de las columnas densas que faltan — para el log del fallo."""
        return [self.feature_names[i] for i in self.dense_idx if not np.isfinite(x[i])]

    def predict_bar(
        self,
        x: np.ndarray,
        *,
        open_time: int,
        reference_price: float,
        sigma_backward: float,
    ) -> BarForecast:
        """Forecast de UNA barra a partir de su fila de features."""
        row = x.reshape(1, -1)
        sigma_f = float(self.volatility.predict(row)[0])
        return BarForecast(
            open_time=open_time,
            reference_price=reference_price,
            sigma_forecast=sigma_f,
            sigma_backward=sigma_backward,
            cones={a: cone.interval(x) for a, cone in self.cones.items()},
            probabilities={
                d: float(m.predict_proba(row)[0]) for d, m in self.barrier.items()
            },
        )


def assert_tail_observable(
    X: np.ndarray,
    feature_names: list[str],
    sparse_names: set[str],
    n_bars: int = 96,
) -> None:
    """Falla si las últimas `n_bars` no tienen completas las columnas densas.

    El gemelo en vivo de `experiment.assert_columns_trainable`, y ataja el
    fallo simétrico. Aquel protege el pasado —una columna que no existe donde
    el modelo aprende—; este protege el presente: una familia que existe en
    toda la historia pero **no llega a tiempo** deja la barra actual con NaN, y
    el analista se queda callado para siempre sin decir por qué.

    Pasa de verdad con el libro. `bookDepth` sale del archivo diario de
    data.binance.vision, que aparece con ~1 día de retraso, y
    `microstructure.reindex_to_bars` hace un join **exacto** por open_time —
    no un forward-fill, porque rellenar sería inventar liquidez. Resultado:
    toda barra posterior a la última del archivo tiene NaN en las 15 columnas
    del núcleo, y con `--features full` el vivo no puede pronosticar nada.
    Correr en vivo con libro exige una fuente de baja latencia (el stream
    `@depth`), no el archivo.

    Los derivados sí llegan: `data/snapshots.py` corre cada 30 min sobre la
    grilla de 5m y `align_to_bars` tolera hasta 1h de antigüedad.
    """
    if X.shape[0] == 0:
        raise ValueError("matriz de features vacía")
    tail = X[-min(n_bars, X.shape[0]) :]
    culpables = [
        name
        for i, name in enumerate(feature_names)
        if name not in sparse_names and not np.all(np.isfinite(tail[:, i]))
    ]
    if culpables:
        muestra = ", ".join(culpables[:6])
        extra = f" (y {len(culpables) - 6} más)" if len(culpables) > 6 else ""
        raise ValueError(
            f"{len(culpables)} columna(s) densas con huecos en las últimas "
            f"{tail.shape[0]} barras: {muestra}{extra}. Una familia que no "
            "llega a tiempo deja el pronóstico en silencio permanente: correr "
            "el vivo con una combinación de features observable (price o "
            "price+deriv) o cablear una fuente de baja latencia para la que "
            "falta."
        )


def fit_bundle(
    X: np.ndarray,
    close: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    open_time: np.ndarray,
    feature_names: list[str],
    sparse_names: set[str],
    timeframe_ms: int,
    config: ExperimentConfig | None = None,
) -> ForecastBundle:
    """Ajusta los tres modelos sobre toda la historia utilizable.

    A diferencia del walk-forward, acá no hay test: el test de este ajuste es
    el futuro, y lo mide `paper/tracker.py`. Lo único que se protege es la
    causalidad — nada de entrenar con etiquetas que aún no terminaron.
    """
    cfg = config or ExperimentConfig()
    n = X.shape[0]
    if n != close.shape[0]:
        raise ValueError("X y la serie de precios deben tener el mismo largo")

    dense_idx = np.array(
        [i for i, nm_ in enumerate(feature_names) if nm_ not in sparse_names],
        dtype=np.int64,
    )
    finite_rows = np.all(np.isfinite(X[:, dense_idx]), axis=1)

    horizon = cfg.barrier.horizon_bars
    y_vol = forward_volatility(close, horizon)
    y_ret = forward_return(close, horizon)

    vol_fit = np.flatnonzero(finite_rows & np.isfinite(y_vol) & (y_vol > 0))
    if vol_fit.size < MIN_FIT_ROWS:
        raise ValueError(
            f"filas utilizables insuficientes para ajustar ({vol_fit.size} < "
            f"{MIN_FIT_ROWS}): descargar más historia antes de correr en vivo"
        )

    volatility = VolatilityModel(kind=cfg.vol_kind, seed=cfg.seed).fit(X, y_vol, vol_fit)  # type: ignore[arg-type]

    ret_fit = np.flatnonzero(finite_rows & np.isfinite(y_ret))
    cones: dict[float, OnlineConformalCone] = {}
    for alpha in cfg.conformal_alphas:
        ci = ConformalReturnInterval(alpha=alpha, seed=cfg.seed).fit(X, y_ret, ret_fit)
        cones[alpha] = OnlineConformalCone(model=ci, alpha=alpha, gamma=ci.gamma)

    barrier: dict[str, BarrierProbabilityModel] = {}
    for direction in cfg.directions:
        labels = triple_barrier_labels(
            high, low, close, open_, cfg.barrier, timeframe_ms, direction
        )
        usable = np.flatnonzero(finite_rows & labels.usable)
        if usable.size < MIN_FIT_ROWS or np.unique(labels.label[usable]).size < 2:
            logger.warning(
                "sin muestras suficientes para la probabilidad {} ({}): se omite",
                direction,
                usable.size,
            )
            continue
        weights = uniqueness_weights(labels.touch_idx, n)
        barrier[direction] = BarrierProbabilityModel(
            kind=cfg.model_kind, seed=cfg.seed
        ).fit(X, labels.label.astype(float), usable, labels.touch_idx, weights)

    fit_through = int(open_time[int(vol_fit[-1])])
    logger.info(
        "bundle ajustado — {:,} filas de volatilidad, {} cono(s), {} dirección(es), "
        "etiquetas completas hasta {}",
        vol_fit.size,
        len(cones),
        len(barrier),
        fit_through,
    )
    return ForecastBundle(
        config=cfg,
        feature_names=list(feature_names),
        dense_idx=dense_idx,
        fit_through_ms=fit_through,
        n_train=int(vol_fit.size),
        timeframe_ms=timeframe_ms,
        volatility=volatility,
        cones=cones,
        barrier=barrier,
    )


def build_analysis(
    bundle: ForecastBundle,
    forecast: BarForecast,
    *,
    symbol: str,
    timeframe: str,
    features: dict[str, float],
    profile: LeverageProfile | None = None,
    barrier_sigma_source: str = "forecast",
) -> MarketAnalysis:
    """Compone el forecast crudo en la proyección que ve el usuario (KPI 2).

    `barrier_sigma_source` decide con cuál de las dos sigmas se dimensionan TP
    y SL. Por defecto la pronosticada, que es la decisión del 25-08; con
    "backward" el setup coincide exactamente con el que etiquetó el backtest,
    que es lo que hay que usar si alguna vez se quiere emitir el KPI 1.
    """
    if barrier_sigma_source not in ("forecast", "backward"):
        raise ValueError("barrier_sigma_source debe ser 'forecast' o 'backward'")

    sigma = (
        forecast.sigma_forecast
        if barrier_sigma_source == "forecast"
        else forecast.sigma_backward
    )
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError(f"sigma no utilizable para proyectar: {sigma}")

    cfg = bundle.config
    price = forecast.reference_price
    bands = [
        ConeBand(
            alpha=alpha,
            nominal=1.0 - alpha,
            ret_lo=lo,
            ret_hi=hi,
            price_lo=price * float(np.exp(lo)),
            price_hi=price * float(np.exp(hi)),
        )
        for alpha, (lo, hi) in sorted(forecast.cones.items())
    ]

    projections: dict[str, SetupProjection] = {}
    for direction in cfg.directions:
        # Sin probabilidad del KPI 1 se proyecta con la de equilibrio: el EV
        # sale exactamente 0 y los niveles, el ROE y la liquidación —que es lo
        # validado— siguen siendo correctos. Inventar un 0.5 daría un EV falso
        # con signo.
        #
        # El equilibrio se calcula con el MISMO costo que usa `project_setup`
        # —fees + slippage + funding por las barras del horizonte— y no con
        # `BarrierConfig.breakeven_probability`, que solo mira el costo fijo.
        # Con esa versión el EV del fallback quedaba negativo por exactamente
        # el funding: chico, pero un EV negativo donde debía haber un cero.
        prob = forecast.probabilities.get(direction)
        if prob is None:
            prob = breakeven_probability(
                cfg.barrier.tp_mult * sigma,
                cfg.barrier.sl_mult * sigma,
                cfg.barrier.cost_pct
                + funding_cost_pct(
                    cfg.barrier, bundle.timeframe_ms, cfg.barrier.horizon_bars
                ),
            )
        projections[direction] = project_setup(
            entry_price=price,
            sigma_horizon=sigma,
            probability=float(np.clip(prob, 0.0, 1.0)),
            config=cfg.barrier,
            timeframe_ms=bundle.timeframe_ms,
            direction=direction,
            profile=profile,
        )

    return MarketAnalysis(
        symbol=symbol,
        timeframe=timeframe,
        open_time=forecast.open_time,
        reference_price=price,
        sigma_forecast=forecast.sigma_forecast,
        sigma_backward=forecast.sigma_backward,
        sigma_ratio=forecast.sigma_ratio,
        barrier_sigma_source=barrier_sigma_source,
        cones=bands,
        projections=projections,
        probabilities=dict(forecast.probabilities),
        probability_matches_barriers=barrier_sigma_source == "backward",
        probability_calibrated=False,  # regla 2: el gate lo rechazó, y se nota
        model_version=bundle.model_version,
        fit_through_ms=bundle.fit_through_ms,
        n_train=bundle.n_train,
        features=features,
    )


def backward_sigma(close: np.ndarray, config: ExperimentConfig) -> np.ndarray:
    """La sigma del etiquetado, expuesta para que el vivo use la misma."""
    return target_volatility(
        close, config.barrier.vol_window_bars, config.barrier.horizon_bars
    )
