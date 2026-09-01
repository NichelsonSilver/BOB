"""El analista en vivo — datos → features → modelo → proyección.

Es la capa de I/O que le da de comer a `models/production.py`: lee la historia
de SQLite, escucha la vela que cierra, arma la matriz de features, pide el
pronóstico y publica la proyección. Los modelos siguen siendo puros; acá vive
todo lo que toca el mundo.

Qué emite, tras la decisión del 2026-08-25
-------------------------------------------
**No emite dirección.** Emite una proyección apoyada en el target de
volatilidad, que es el que pasó el gate: niveles de TP y SL dimensionados por
la sigma pronosticada, EV neto de costos, ROE por leverage, precio de
liquidación y el cono conformal de precio. El KPI 1 se calcula y se **guarda**
con su feature vector completo (regla 10) para poder medirlo forward, pero
viaja marcado `probability_calibrated=False` y ninguna parte de este módulo
decide nada con él.

Tres decisiones que no son obvias
----------------------------------
1. **Los features se recalculan sobre la serie completa, no sobre una cola.**
   Tienta usar las últimas ~3.000 barras y ahorrarse el resto. Medido sobre
   los 69.119 barras reales: con cola de 3.000 la última fila difiere de la
   de historia completa hasta 3e-7 relativo en `oi_z_ctx`, y **no converge**
   al alargar la cola a 5.000. Es una diferencia irrelevante para operar y
   perfectamente relevante para la honestidad de "el vivo computa lo mismo
   que el backtest". Armar las 81 columnas sobre las 69k barras toma 0,68s
   una vez cada 15 minutos, en un hilo aparte. No hay nada que ahorrar.

2. **La vela nueva se agrega en memoria, no se relee de la DB.** El hub
   persiste la vela cerrada en un hilo de fondo; releer justo después es una
   carrera que a veces pierde la última barra y nadie se entera. La DB se
   vuelve a leer solo al reajustar, cuando la escritura ya terminó hace rato.

3. **El reajuste no bloquea la emisión.** Ajustar el bundle son ~10 fits de
   boosting sobre la historia completa. Corre en background y el analista
   sigue respondiendo con el bundle vigente hasta que el nuevo está listo:
   un pronóstico de un modelo de ayer es mucho mejor que ningún pronóstico.

Familias de features en vivo
-----------------------------
El default es `price+deriv` y no `full` por una razón de datos: `bookDepth`
sale del archivo diario de data.binance.vision, que aparece con ~1 día de
retraso, y el join a la grilla de velas es exacto. Toda barra posterior a la
última del archivo tiene NaN en las 15 columnas del núcleo y el analista se
quedaría mudo para siempre. `assert_tail_observable` convierte eso en un
error al arrancar, con nombres, en vez de en silencio. Los derivados sí
llegan: `data/snapshots.py` corre cada 30 min y `align_to_bars` tolera 1h.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from loguru import logger
from sqlmodel import select

from bob.data.binance_rest import Kline
from bob.data.binance_ws import KlineEvent, MarketEvent
from bob.data.download import repair_series
from bob.data.download_vision import ingest_funding
from bob.data.snapshots import snapshot_once
from bob.data.store import (
    BookDepthSeries,
    DerivativesSeries,
    OHLCVSeries,
    load_book_depth,
    load_derivatives,
    load_series,
)
from bob.db.models import ForecastRecord
from bob.db.session import get_session, init_db
from bob.models.experiment import FEATURE_SETS, ExperimentConfig, assemble_features
from bob.models.production import (
    ForecastBundle,
    MarketAnalysis,
    assert_tail_observable,
    backward_sigma,
    build_analysis,
    fit_bundle,
)
from bob.models.projection import LeverageProfile
from bob.signals.features import CONTEXT_H, window_bars

Publisher = Callable[[str, Any], Awaitable[None]]

#: Cada cuántas velas cerradas se reajusta el bundle. 96 barras de 15m = 1 día.
#: Más seguido no aporta —el modelo aprende de dos años— y más espaciado deja
#: al conformal calibrando contra un régimen viejo.
DEFAULT_REFIT_EVERY_BARS = 96

#: Familia por defecto en vivo. Ver la nota del encabezado sobre `full`.
DEFAULT_FEATURE_SET = "price+deriv"

#: Cuánto hacia atrás se repide el funding en cada reparación. Son 3 cobros
#: por día, así que una semana es una página y sobra para cubrir cualquier
#: pausa razonable; el upsert es idempotente y reescribir lo mismo no cuesta.
FUNDING_LOOKBACK_MS = 7 * 86_400_000
#: A partir de qué antigüedad se vuelve a pedir el funding por REST.
#: Binance lo publica cada 8h y la tolerancia de staleness del feature es
#: de 8h exactas, así que pedirlo al filo es llegar tarde: 4h da holgura
#: de una publicación entera y cuesta ~6 requests por día.
FUNDING_REFRESH_MS = 4 * 3_600_000


@dataclass(frozen=True)
class AnalystInputs:
    """Todo lo que el analista necesita leer del disco para pensar."""

    series: OHLCVSeries
    derivatives: DerivativesSeries | None
    funding: DerivativesSeries | None
    book: BookDepthSeries | None


class LiveAnalyst:
    """Escucha velas cerradas y publica la proyección de cada una.

    Recibe `publish` por inyección (igual que `LiveDataService`), así el test
    lo reemplaza por una lista y no hace falta un WebSocket para verificar que
    el analista dice lo que tiene que decir.
    """

    #: Grilla de los snapshots de derivados. Debe coincidir con la del archivo
    #: histórico: con periods distintos, archivo y REST escriben dos series que
    #: nunca se tocan (hallazgo de la Fase 2b).
    snapshot_period = "5m"

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        *,
        publish: Publisher,
        config: ExperimentConfig | None = None,
        feature_set: str = DEFAULT_FEATURE_SET,
        profile: LeverageProfile | None = None,
        refit_every_bars: int = DEFAULT_REFIT_EVERY_BARS,
        barrier_sigma_source: str = "forecast",
        persist: bool = True,
        repair_on_fit: bool = True,
    ) -> None:
        if feature_set not in FEATURE_SETS:
            raise ValueError(
                f"feature_set desconocido: {feature_set} "
                f"(opciones: {', '.join(sorted(FEATURE_SETS))})"
            )
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.feature_set = feature_set
        self._publish = publish
        self._profile = profile or LeverageProfile()
        self._refit_every_bars = refit_every_bars
        self._barrier_sigma_source = barrier_sigma_source
        self._persist_enabled = persist
        self._repair_on_fit = repair_on_fit

        use_deriv, use_book, use_near = FEATURE_SETS[feature_set]
        self.config = replace(
            config or ExperimentConfig(),
            use_derivatives=use_deriv,
            use_book=use_book,
            use_book_near=use_near,
        )

        self._bundle: ForecastBundle | None = None
        self._inputs: AnalystInputs | None = None
        self._bars_since_fit = 0
        #: La reparación de velas falló y hay que reintentarla en la próxima
        #: barra. Ver `_repair_candles`: es el único de los tres arreglos de
        #: `_repair` que no tenía camino de reintento propio.
        self._candle_repair_pending = False
        self._lock = asyncio.Lock()
        self._refit_task: asyncio.Task[None] | None = None
        self.last_analysis: MarketAnalysis | None = None

    @property
    def bundle(self) -> ForecastBundle | None:
        """El bundle vigente, o None si todavía no ajustó.

        Se expone porque el tracker necesita los conos para realimentar el ACI,
        y porque el reajuste **reemplaza** el objeto: quien guarde una
        referencia al bundle viejo va a estar realimentando conos que ya no
        emiten. Por eso se consulta, no se guarda.
        """
        return self._bundle

    def status(self) -> dict[str, Any]:
        """Estado operativo del analista, para `/api/health`.

        Existe porque durante una corrida larga la pregunta que importa no es
        "¿el backend responde?" sino "¿el analista está emitiendo?". Son cosas
        distintas: el ajuste inicial toma ~80s, un reajuste fallido lo deja
        sirviendo con el bundle previo, y una familia de features que dejó de
        llegar lo deja mudo. Sin este bloque, las tres se ven igual desde
        afuera — un backend en verde que no produce nada.
        """
        analysis = self.last_analysis
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "feature_set": self.feature_set,
            #: Qué estimador de volatilidad está emitiendo. Va al lado del
            #: feature_set porque los dos definen a qué corrida pertenece la
            #: muestra forward que se está acumulando.
            "vol_kind": self.config.vol_kind,
            # False durante el arranque: el ajuste corre en background.
            "fitted": self._bundle is not None,
            "refitting": self._refit_task is not None and not self._refit_task.done(),
            "bars_since_fit": self._bars_since_fit,
            #: True cuando la reparación de velas se quedó sin red y todavía
            #: no pudo reintentarse. Mientras esté en True el analista puede
            #: estar mudo por un hueco que se arregla solo en la próxima
            #: barra — que es distinto de un corte de datos de Binance.
            "candle_repair_pending": self._candle_repair_pending,
            "n_train": self._bundle.n_train if self._bundle else 0,
            "fit_through_ms": self._bundle.fit_through_ms if self._bundle else None,
            "model_version": self._bundle.model_version if self._bundle else None,
            "last_forecast_open_time": analysis.open_time if analysis else None,
            "last_reference_price": analysis.reference_price if analysis else None,
            "cone_coverage": {
                f"{alpha:.2f}": {
                    "n": cone.n_observed,
                    "empirical": cone.empirical_coverage,
                    "alpha_t": cone.alpha_t,
                }
                for alpha, cone in (self._bundle.cones.items() if self._bundle else [])
            },
        }

    # -- Ciclo de vida ---------------------------------------------------- #

    async def start(self) -> None:
        """Repara la serie, ajusta el bundle y analiza la última barra cerrada."""
        await self._repair()
        await asyncio.to_thread(self._load_and_fit)
        await asyncio.to_thread(self._replay_cone_state)
        await self.analyze_latest()

    async def _repair(self) -> None:
        """Pone al día las DOS series que alimentan la matriz, tras cualquier parada.

        Se corre ANTES de ajustar y antes de cada reajuste, y cubre los dos
        modos en que una pausa del proceso deja al analista mudo:

        * **Velas.** El feed reconecta y escribe la vela actual, así que la
          descarga incremental —que reanuda desde la última vela en DB— salta
          el rango caído y nadie vuelve a mirarlo. `repair_series` pide los
          huecos interiores uno por uno. Un hueco no es cosmético: las
          ventanas de `signals/features.py` cuentan barras, no tiempo.
        * **Derivados.** Los snapshots corren con el backend, así que mientras
          está caído no se escriben, y `align_to_bars` marca NaN por staleness
          pasada 1h. Sin esto las 26 columnas de derivados salen vacías en la
          cola y `assert_tail_observable` aborta el arranque — que es lo
          correcto, pero arreglable solo. Cada request trae ~41h de grilla de
          5m, así que un ciclo recupera cualquier pausa menor a eso.
        * **Funding.** Vive en su propia grilla de 8h y hasta ahora solo lo
          escribía la ingesta del archivo. Su tolerancia de staleness es de
          8h exactas, así que basta perderse un cobro para que las 4 columnas
          de funding salgan NaN: medido, 9 barras de 96 en la cola real.

        Un fallo de red acá no impide arrancar: se sigue con lo que haya en DB
        y las guardas de observabilidad y contigüidad deciden si esa serie
        sirve para pronosticar. Pero tampoco se descarta — el de velas queda
        pendiente y se reintenta barra a barra (`_repair_candles`).
        """
        if not self._repair_on_fit:
            return
        await self._repair_candles()

        if not self.config.use_derivatives:
            return
        try:
            escritos = await snapshot_once([self.symbol], self.snapshot_period)
            logger.info(
                "analista: derivados al día — {} punto(s)", escritos.get(self.symbol, 0)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("analista: no se pudieron poner al día los derivados ({})", exc)

        try:
            desde = int(self._inputs.series.open_time[-1]) if self._inputs else 0
            report = await ingest_funding(
                self.symbol, max(desde - FUNDING_LOOKBACK_MS, 0)
            )
            logger.info("analista: funding al día — {} fila(s)", report.rows_written)
        except Exception as exc:  # noqa: BLE001
            logger.warning("analista: no se pudo poner al día el funding ({})", exc)

    async def _repair_candles(self) -> bool:
        """Pone al día la serie de velas. Devuelve False si la red no dejó.

        El fallo se **recuerda** en `_candle_repair_pending` en vez de
        descartarse, y esa es toda la diferencia con la versión anterior.

        El incidente que lo motivó (2026-09-01, mudanza de casa): el backend
        arrancó a los pocos segundos de encender el equipo en una red nueva,
        cuando el DNS todavía no resolvía. Los cuatro intentos de
        `repair_series` murieron con `getaddrinfo failed`, se logueó un
        WARNING y la corrida siguió. Cuando la red volvió —un minuto después—
        el poll REST escribió la vela en curso, y las dos barras del apagón
        quedaron como hueco interior **para siempre**: `--resume` avanza desde
        la última vela en DB y no vuelve a mirar hacia atrás. El analista no
        emitió una sola barra hasta que se corrió el `--repair` a mano.

        La ironía es la que ordena el diseño: el arranque tras mover el equipo
        es a la vez el momento en que la red es menos probable que esté, y el
        único en que esta reparación es imprescindible. Una sola pasada, justo
        ahí, es la peor política posible.

        Las otras dos series de `_repair` ya tenían su reintento y por eso
        nunca mostraron el problema: los derivados los reescribe el scheduler
        del feed cada 30 min y `_refresh_context` los relee en cada barra; el
        funding lo reingesta `_maybe_ingest_funding` cuando se acerca a su
        tolerancia. Las velas eran la única sin camino de vuelta.

        Un éxito de red cierra lo pendiente aunque queden huecos sin llenar:
        que Binance no devuelva un rango es un hecho de los datos —lo reporta
        `repair_series` y lo vigila `_assert_tail_contiguous`—, y reintentarlo
        cada 15 minutos sería rafaguear REST contra la regla 7.
        """
        try:
            resultado = await repair_series(self.symbol, self.timeframe)
        except Exception as exc:  # noqa: BLE001 — sin red se sigue con la DB
            self._candle_repair_pending = True
            logger.warning(
                "analista: no se pudieron reparar las velas ({}) — "
                "pendiente, se reintenta en la próxima barra",
                exc,
            )
            return False

        self._candle_repair_pending = False
        if resultado["gaps_found"] or resultado["extended"]:
            logger.info(
                "analista: velas al día — {} hueco(s) cerrados, {} nuevas",
                resultado["filled"],
                resultado["extended"],
            )
        return True

    async def _retry_candle_repair(self) -> bool:
        """Reintenta la reparación pendiente. True si además resincronizó.

        Si la reparación escribió velas, lo hizo **en el medio** de la serie:
        la de memoria quedó vieja y agregarle la barra nueva encima
        arrastraría el hueco. Por eso se relee de la DB, que es la autoridad,
        con el mismo `_resync` que usa `_compute_and_heal`.
        """
        if not await self._repair_candles():
            return False
        await asyncio.to_thread(self._resync)
        return True

    def _replay_cone_state(self) -> None:
        """Devuelve al cono el estado de ACI que ya había ganado (ver tracker)."""
        from bob.paper.tracker import replay_cone_state

        if self._bundle is not None:
            replay_cone_state(self.symbol, self.timeframe, self._bundle.cones)

    async def stop(self) -> None:
        if self._refit_task is not None and not self._refit_task.done():
            self._refit_task.cancel()
            try:
                await self._refit_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._refit_task = None
        logger.info("analista: detenido")

    def attach(self, hub: Any) -> None:
        """Se engancha al `MarketDataHub` para recibir las velas cerradas."""
        hub.add_listener(self.on_event)

    # -- Entrada de eventos ------------------------------------------------ #

    async def on_event(self, event: MarketEvent) -> None:
        """Listener del hub. Solo reacciona a la vela CERRADA de su símbolo.

        La vela en curso no se analiza a propósito: todo el etiquetado y todos
        los features del proyecto son causales sobre velas cerradas, y
        pronosticar sobre una barra a medio formar produciría un número que el
        backtest nunca midió.
        """
        if not isinstance(event, KlineEvent) or not event.is_closed:
            return
        if event.symbol != self.symbol or event.timeframe != self.timeframe:
            return
        await self.on_closed_candle(event.kline)

    async def on_closed_candle(self, kline: Kline) -> None:
        """Incorpora la vela y publica el análisis de la barra que acaba de cerrar."""
        async with self._lock:
            if self._inputs is None:
                # No se pierde: el hub la persiste y `_load_and_fit` relee la
                # serie al terminar de ajustar.
                logger.info(
                    "analista: vela recibida durante el ajuste inicial — "
                    "se recupera de la DB al terminar"
                )
                return
            # Si la reparación del arranque se quedó sin red, este es el
            # reintento: la barra nueva es la señal de que el reloj avanzó y
            # la red bien puede haber vuelto.
            resincronizado = False
            if self._candle_repair_pending:
                resincronizado = await self._retry_candle_repair()

            appended = _append_candle(self._inputs.series, kline)
            if appended is None:
                # Vela repetida o fuera de orden: el hub ya la reportó. Salvo
                # que venga de resincronizar, donde la DB puede traerla ya
                # escrita: entonces no es un duplicado sino la misma barra por
                # el otro camino, y hay que analizarla igual.
                if not resincronizado or (
                    int(self._inputs.series.open_time[-1]) != kline.open_time
                ):
                    return
            else:
                self._inputs = AnalystInputs(
                    series=appended,
                    derivatives=self._inputs.derivatives,
                    funding=self._inputs.funding,
                    book=self._inputs.book,
                )
            self._bars_since_fit += 1
            await self._refresh_context()

        await self.analyze_latest()
        self._maybe_schedule_refit()

    # -- Análisis ---------------------------------------------------------- #

    async def analyze_latest(self) -> MarketAnalysis | None:
        """Analiza la última barra cerrada y publica el resultado.

        Devuelve None —y publica `analysis.error`— cuando no se puede
        pronosticar honestamente. Callarse sin explicación es peor que no
        tener número: el usuario creería que el mercado no da señal cuando en
        realidad falta un dato (regla 8, aplicada al analista y no solo al
        feed).
        """
        try:
            analysis = await asyncio.to_thread(self._compute_and_heal)
        except Exception as exc:  # noqa: BLE001 — el analista no puede tumbar el backend
            logger.exception("analista: fallo al computar el análisis: {}", exc)
            await self._publish(
                "analysis.error",
                {"symbol": self.symbol, "timeframe": self.timeframe, "detail": str(exc)},
            )
            return None

        if analysis is None:
            return None

        self.last_analysis = analysis
        if self._persist_enabled:
            await asyncio.to_thread(self._persist, analysis)
        await self._publish("analysis.forecast", self._payload(analysis))
        return analysis

    def _compute_and_heal(self) -> MarketAnalysis | None:
        """Computa, y si la serie en memoria perdió una vela, la resincroniza.

        El reintento es UNO solo y solo para este fallo: si tras releer la DB
        la serie sigue sin ser contigua, el hueco es de los datos y no de la
        memoria, y entonces el error tiene que salir a la superficie en vez de
        reintentarse en círculos.
        """
        try:
            return self._compute_latest()
        except ValueError as exc:
            if "warm-up" not in str(exc):
                raise
            logger.warning("analista: serie en memoria desincronizada, releo la DB")
            self._resync()
            return self._compute_latest()

    def _compute_latest(self) -> MarketAnalysis | None:
        """Parte síncrona y pesada del análisis. Corre en un hilo."""
        bundle, inputs = self._bundle, self._inputs
        if bundle is None or inputs is None:
            raise RuntimeError("el analista no está ajustado")

        X, names, sparse, _ = self._assemble(inputs)
        if names != bundle.feature_names:
            raise RuntimeError(
                "la matriz de features dejó de coincidir con la del ajuste "
                f"({len(names)} columnas vs {len(bundle.feature_names)}): "
                "reajustar antes de emitir"
            )

        _assert_tail_contiguous(inputs.series)

        row = X[-1]
        if not bundle.row_is_usable(row):
            faltan = bundle.missing_dense(row)
            raise ValueError(
                f"la última barra no tiene {len(faltan)} feature(s) densos "
                f"({', '.join(faltan[:5])}): no se pronostica sobre un hueco"
            )

        series = inputs.series
        sigma_back = float(backward_sigma(series.close, self.config)[-1])
        forecast = bundle.predict_bar(
            row,
            open_time=int(series.open_time[-1]),
            reference_price=float(series.close[-1]),
            sigma_backward=sigma_back,
        )
        return build_analysis(
            bundle,
            forecast,
            symbol=self.symbol,
            timeframe=self.timeframe,
            features=dict(zip(names, (float(v) for v in row), strict=True)),
            profile=self._profile,
            barrier_sigma_source=self._barrier_sigma_source,
        )

    def _payload(self, analysis: MarketAnalysis) -> dict[str, Any]:
        """Frame del WS. Sin el feature vector: 81 números por barra al
        navegador son ruido, y la DB ya los tiene para el post-mortem."""
        data = analysis.as_dict(include_features=False)
        data["feature_set"] = self.feature_set
        data["bars_since_fit"] = self._bars_since_fit
        data["cone_coverage"] = {
            f"{alpha:.2f}": {
                "n": cone.n_observed,
                "empirical": cone.empirical_coverage,
                "alpha_t": cone.alpha_t,
            }
            for alpha, cone in (self._bundle.cones.items() if self._bundle else {}.items())
        }
        return data

    # -- Ajuste ------------------------------------------------------------ #

    def _load_inputs(self) -> AnalystInputs:
        """Lee de SQLite todo lo que necesita la matriz. Síncrono, va en hilo."""
        init_db()
        series = load_series(self.symbol, self.timeframe)
        if len(series) == 0:
            raise ValueError(
                f"no hay velas persistidas de {self.symbol} {self.timeframe}: "
                "correr `python -m bob.data.download` antes del vivo"
            )
        deriv, fund, book = self._load_context()
        return AnalystInputs(series=series, derivatives=deriv, funding=fund, book=book)

    def _load_context(
        self,
    ) -> tuple[DerivativesSeries | None, DerivativesSeries | None, BookDepthSeries | None]:
        """Lee del disco las tres series que NO son la de velas.

        Separada de `_load_inputs` porque tienen cadencias distintas: la serie
        de velas se mantiene incremental en memoria (el hub la va extendiendo)
        y estas tres las escribe otro proceso —el loop de snapshots del feed—
        cada 30 minutos. Releerlas es barato y es lo único que las trae.
        """
        init_db()
        deriv = fund = None
        book = None
        if self.config.use_derivatives:
            deriv = load_derivatives(self.symbol, self.snapshot_period)
            fund = load_derivatives(self.symbol, "funding")
        if self.config.use_book:
            book = load_book_depth(self.symbol, self.timeframe)
        return deriv, fund, book

    async def _refresh_context(self) -> None:
        """Vuelve a leer derivados, funding y libro antes de analizar la barra.

        Existe por un bug que dejaba al analista mudo sin decir nada.
        `on_closed_candle` extendía la serie de velas pero construía el
        `AnalystInputs` nuevo **arrastrando** las otras tres de la barra
        anterior, y esas solo se releían en `_load_inputs`, o sea al ajustar y
        al reajustar. El feed escribía los snapshots al disco cada 30 min y el
        analista no los veía nunca: las series quedaban congeladas en la foto
        del arranque mientras el reloj avanzaba, y cada familia iba cruzando su
        tolerancia de staleness por orden de exigencia —taker, después funding,
        después OI— hasta que `row_is_usable` rechazaba la barra entera.

        El efecto medido: un pronóstico al arrancar y después silencio hasta el
        reajuste. Con `refit_every_bars=96` eso es ~1 pronóstico por día en vez
        de 96, y la corrida de la Fase 5 no termina nunca. No fallaba ruidoso
        porque el error que sí emite —"no se pronostica sobre un hueco"— es el
        correcto: la barra de verdad no tenía los features. Lo que estaba mal
        era la razón por la que no los tenía.
        """
        if self._inputs is None:
            return
        if self.config.use_derivatives:
            await self._maybe_ingest_funding()
        deriv, fund, book = await asyncio.to_thread(self._load_context)
        self._inputs = AnalystInputs(
            series=self._inputs.series, derivatives=deriv, funding=fund, book=book
        )

    async def _maybe_ingest_funding(self) -> None:
        """Trae funding nuevo por REST si el que hay se está por vencer.

        Nadie más lo hace: `snapshot_once` escribe la grilla de 5m pero no el
        funding, y la reingesta de `_repair` corre solo en el arranque y en el
        reajuste. Entre dos reajustes el funding se vence solo — que es la
        mitad de por qué el analista se quedaba sin `funding_bps`.
        """
        if self._inputs is None:
            return
        fund = self._inputs.funding
        ahora = int(self._inputs.series.open_time[-1])
        ultimo = (
            int(fund.timestamp[-1])
            if fund is not None and len(fund.timestamp) > 0
            else 0
        )
        if ahora - ultimo < FUNDING_REFRESH_MS:
            return
        try:
            report = await ingest_funding(
                self.symbol, max(ultimo - FUNDING_LOOKBACK_MS, 0)
            )
            logger.info(
                "analista: funding refrescado — {} fila(s)", report.rows_written
            )
        except Exception as exc:  # noqa: BLE001 — sin red se sigue con lo que hay
            logger.warning("analista: no se pudo refrescar el funding ({})", exc)

    def _resync(self) -> None:
        """Vuelve a tomar todo de la DB, que es la autoridad.

        Se llama cuando la serie en memoria dejó de ser contigua. Cualquier
        camino que haga perder una vela —una que cerró mientras se ajustaba,
        un evento que llegó fuera de orden— se arregla igual: el hub persiste
        todas las velas cerradas, así que la DB siempre tiene la verdad.

        Pasa por `_load_inputs` y no por una lectura propia a propósito: es la
        única costura del analista contra el disco, y tener dos caminos de
        lectura es cómo se termina con uno de los dos sin arreglar.
        """
        self._inputs = self._load_inputs()

    def _assemble(
        self, inputs: AnalystInputs
    ) -> tuple[np.ndarray, list[str], set[str], dict[str, list[str]]]:
        return assemble_features(
            inputs.series,
            self.config,
            inputs.derivatives,
            inputs.funding,
            inputs.book,
        )

    def _load_and_fit(self) -> None:
        """Carga + ajuste completos. Síncrono; siempre se llama desde un hilo."""
        inputs = self._load_inputs()
        X, names, sparse, _ = self._assemble(inputs)
        assert_tail_observable(X, names, sparse)

        series = inputs.series
        bundle = fit_bundle(
            X,
            series.close,
            series.open,
            series.high,
            series.low,
            series.open_time,
            names,
            sparse,
            series.interval_ms,
            self.config,
        )
        # La serie se relee DESPUÉS de ajustar. El ajuste toma ~90s y las
        # barras son de 15 min, así que ~1 de cada 10 arranques tiene una vela
        # cerrando en medio: el hub la persiste, el analista todavía no puede
        # procesarla, y la serie en memoria queda una barra corta. La siguiente
        # vela se agregaría dejando un hueco y `_assert_tail_contiguous`
        # apagaría la emisión hasta el próximo reajuste — 24 horas después.
        # Releer es un segundo y la DB es la autoridad.
        self._bundle = bundle
        # Se relee DESPUÉS de ajustar. El ajuste toma ~90s y las barras son de
        # 15 min, así que ~1 de cada 10 arranques tiene una vela cerrando en
        # medio: el hub la persiste, el analista todavía no puede procesarla, y
        # la serie en memoria queda una barra corta. La siguiente vela se
        # agregaría dejando un hueco y `_assert_tail_contiguous` apagaría la
        # emisión hasta el próximo reajuste — 24 horas después.
        self._inputs = self._load_inputs()
        self._bars_since_fit = 0
        logger.info(
            "analista: {} {} listo — variante {}, {} features, {:,} barras",
            self.symbol,
            self.timeframe,
            self.feature_set,
            len(names),
            len(series),
        )

    def _maybe_schedule_refit(self) -> None:
        """Lanza el reajuste en background si toca y no hay uno corriendo."""
        if self._bars_since_fit < self._refit_every_bars:
            return
        if self._refit_task is not None and not self._refit_task.done():
            return
        logger.info(
            "analista: {} barras desde el ajuste — reajustando en background",
            self._bars_since_fit,
        )
        self._refit_task = asyncio.create_task(self._refit(), name="analyst-refit")

    async def _refit(self) -> None:
        """Reajusta sin dejar de emitir: el bundle viejo sigue vigente mientras."""
        try:
            await self._repair()
            await asyncio.to_thread(self._load_and_fit)
            await asyncio.to_thread(self._replay_cone_state)
        except Exception as exc:  # noqa: BLE001
            logger.exception("analista: el reajuste falló, sigue el bundle previo: {}", exc)
            await self._publish(
                "analysis.error",
                {
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "detail": f"reajuste fallido: {exc}",
                    "still_serving": True,
                },
            )

    # -- Persistencia ------------------------------------------------------ #

    def _persist(self, analysis: MarketAnalysis) -> None:
        """Guarda el pronóstico. Idempotente por `forecast_id`.

        Reprocesar la misma barra no debe duplicar el registro: la cobertura
        que reporta el tracker se calcula sobre estas filas, y un duplicado la
        contaría dos veces.
        """
        forecast_id = f"{analysis.symbol}-{analysis.timeframe}-{analysis.open_time}"
        with get_session() as session:
            existing = session.exec(
                select(ForecastRecord).where(ForecastRecord.forecast_id == forecast_id)
            ).first()
            if existing is not None:
                return
            session.add(
                ForecastRecord(
                    forecast_id=forecast_id,
                    symbol=analysis.symbol,
                    timeframe=analysis.timeframe,
                    open_time=analysis.open_time,
                    reference_price=str(analysis.reference_price),
                    sigma_forecast=analysis.sigma_forecast,
                    sigma_backward=analysis.sigma_backward,
                    barrier_sigma_source=analysis.barrier_sigma_source,
                    horizon_bars=self.config.barrier.horizon_bars,
                    cones_json=json.dumps([c.as_dict() for c in analysis.cones]),
                    projections_json=json.dumps(
                        {d: p.as_dict() for d, p in analysis.projections.items()}
                    ),
                    probabilities_json=json.dumps(analysis.probabilities),
                    probability_calibrated=analysis.probability_calibrated,
                    features_json=json.dumps(analysis.features),
                    model_version=analysis.model_version,
                    fit_through_ms=analysis.fit_through_ms,
                    n_train=analysis.n_train,
                )
            )
            session.commit()


def _append_candle(series: OHLCVSeries, kline: Kline) -> OHLCVSeries | None:
    """Serie con la vela nueva al final, o None si no aporta una barra nueva.

    Devuelve None ante una vela repetida o anterior a la última conocida: en
    vivo eso pasa cuando el feed cae a REST y rellena solapando, y agregarla
    rompería la monotonía que `OHLCVSeries` garantiza.
    """
    if len(series) and kline.open_time <= int(series.open_time[-1]):
        return None

    def push(arr: np.ndarray, value: Any, dtype: Any) -> np.ndarray:
        return np.append(arr, np.array([value], dtype=dtype))

    return OHLCVSeries(
        symbol=series.symbol,
        timeframe=series.timeframe,
        open_time=push(series.open_time, kline.open_time, np.int64),
        open=push(series.open, float(kline.open), np.float64),
        high=push(series.high, float(kline.high), np.float64),
        low=push(series.low, float(kline.low), np.float64),
        close=push(series.close, float(kline.close), np.float64),
        volume=push(series.volume, float(kline.volume), np.float64),
        quote_volume=push(series.quote_volume, float(kline.quote_volume), np.float64),
        taker_buy_volume=push(
            series.taker_buy_volume, float(kline.taker_buy_volume), np.float64
        ),
        n_trades=push(series.n_trades, kline.n_trades, np.int64),
    )


def _assert_tail_contiguous(series: OHLCVSeries) -> None:
    """Falla si a la ventana de warm-up de la última barra le faltan velas.

    Las ventanas rodantes de `signals/features.py` cuentan **barras, no
    tiempo**: si en las últimas 168 horas falta una hora de velas, la ventana
    de contexto de la barra actual abarca en realidad 169 horas y todas las
    features de contexto —z-scores, rangos percentiles— describen algo que no
    pasó. El número saldría igual de convincente y sería falso.

    Se mira solo la cola porque es lo que alimenta la fila que se va a emitir;
    un hueco viejo ya estaba en el backtest y no cambia el pronóstico de hoy.
    """
    warmup = window_bars(CONTEXT_H, series.interval_ms)
    tail = series.slice(max(0, len(series) - (warmup + 1)))
    huecos = tail.gaps
    if huecos:
        inicio, fin = huecos[0]
        raise ValueError(
            f"faltan velas en la ventana de warm-up de la última barra "
            f"({len(huecos)} hueco(s), el primero entre {inicio} y {fin}): las "
            "ventanas de features cuentan barras, no tiempo, así que el "
            "pronóstico describiría un contexto que no existió. Correr "
            "`python -m bob.data.download --repair` y reintentar."
        )
