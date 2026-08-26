"""Runner del experimento con I/O — la capa que conecta DB, modelos y disco.

`models/` y `signals/` son puros por diseño (regla 3 de CLAUDE.md). Este
módulo es el que tiene permiso de tocar el mundo: lee velas de SQLite, corre
el experimento, escribe el reporte y persiste el run en `BacktestRun`.

Uso:
    uv run python -m bob.backtest.runner --symbol ETHUSDT --timeframe 15m
    uv run python -m bob.backtest.runner --tp 2.0 --sl 1.0 --horizon 24 --folds 8
    uv run python -m bob.backtest.runner --features price     # baseline Fase 2
    uv run python -m bob.backtest.runner --features full+near # todo, Fase 2b

`--features` decide qué familias entran. Es el eje de la comparación del gate:
sin él no se puede afirmar que las familias nuevas aportan, solo que el número
cambió.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from bob.data.store import load_book_depth, load_derivatives, load_series
from bob.db.models import BacktestRun
from bob.db.session import get_session, init_db
from bob.models.experiment import (
    FEATURE_SETS,
    ExperimentConfig,
    ExperimentResult,
    feature_set_name,
    run_experiment,
)
from bob.models.labeling import BarrierConfig
from bob.models.report import render_report, render_summary
from bob.utils.console import enable_utf8_stdout

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent
ARTIFACTS_DIR = _BACKEND_DIR / "artifacts"


def _end_time_ms(until: str | None) -> int | None:
    """Última barra a incluir, en epoch ms UTC. Inclusive en las dos formas.

    Acepta `2026-08-21` (todo ese día) o `2026-08-21T20:30` (esa barra y no
    la siguiente). La precisión de minutos no es adorno: el run del 25-08
    cerró en la barra de las 20:30 de un día que la DB después completó hasta
    las 23:45, así que cortar por fecha deja 13 barras de diferencia y el
    control bit a bit no cierra. Reproducir un run exige nombrar su última
    barra, que es justo lo que imprime el reporte.
    """
    if until is None:
        return None
    if "T" in until:
        bar = datetime.strptime(until, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)
        return int(bar.timestamp() * 1000)
    day = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=UTC)
    return int((day + timedelta(days=1)).timestamp() * 1000) - 1


def build_run_id(result: ExperimentResult) -> str:
    """Nombre del run: símbolo, timeframe, variante de features, modelo, hora.

    El estimador de volatilidad entra en el nombre porque dos runs que solo
    difieren en él son indistinguibles de otra forma, y el archivo es lo que
    se abre para verificar una cifra. La variante de features sigue estando,
    pero quien la lee es `compare.py` desde el JSON, no desde el nombre.
    """
    variante = feature_set_name(result.config)
    return (
        f"{result.symbol}-{result.timeframe}-{variante}-{result.config.vol_kind}-"
        f"{datetime.now(UTC):%Y%m%d%H%M%S}"
    )


def persist_run(result: ExperimentResult) -> str:
    """Guarda el run en `BacktestRun`. Devuelve el run_id."""
    init_db()
    run_id = build_run_id(result)

    # Métricas agregadas: se toma la peor dirección, no el promedio. Un
    # promedio esconde que una de las dos está descalibrada, y el usuario
    # puede operar cualquiera de las dos.
    worst = max(
        result.directions.values(),
        key=lambda d: d.metrics_model.mean_calibration_error_pp,
    )
    trading = worst.trading

    with get_session() as session:
        run = BacktestRun(
            run_id=run_id,
            symbol=result.symbol,
            timeframe=result.timeframe,
            date_from=result.date_from,
            date_to=result.date_to,
            config_json=json.dumps(result.config.to_dict(), default=float),
            n_signals=int(trading["n_signals"]),
            win_rate=str(trading["win_rate"]),
            profit_factor=str(trading["profit_factor"]),
            max_drawdown_pct=str(trading["max_drawdown_pct"]),
            expectancy_pct=str(trading["expectancy_pct"]),
            calibration_error_pp=str(worst.metrics_model.mean_calibration_error_pp),
            buckets_json=json.dumps(
                {
                    direction: [
                        {
                            "lower": b.lower,
                            "upper": b.upper,
                            "n": b.n,
                            "predicted": b.mean_predicted,
                            "observed": b.observed_rate,
                        }
                        for b in dr.metrics_model.buckets
                    ]
                    for direction, dr in result.directions.items()
                }
            ),
            status="done",
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
    return run_id


def write_artifacts(result: ExperimentResult, run_id: str) -> tuple[Path, Path]:
    """Escribe reporte .txt y resultado .json en `backend/artifacts/`."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = ARTIFACTS_DIR / f"{run_id}.txt"
    json_path = ARTIFACTS_DIR / f"{run_id}.json"
    report_path.write_text(render_report(result), encoding="utf-8")
    json_path.write_text(result.to_json(), encoding="utf-8")
    return report_path, json_path


def main() -> None:
    enable_utf8_stdout()
    # Los defaults se leen de las dataclasses, no se reescriben acá. Duplicarlos
    # deja el CLI desincronizado del modelo en cuanto uno de los dos cambia, y
    # el experimento corre con una configuración distinta de la documentada
    # sin que nada falle.
    barrier_defaults = BarrierConfig()
    exp_defaults = ExperimentConfig()

    parser = argparse.ArgumentParser(description="Corre el experimento de forecasting de BOB")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument(
        "--tp",
        type=float,
        default=barrier_defaults.tp_mult,
        help="múltiplo de sigma_H para el TP",
    )
    parser.add_argument(
        "--sl",
        type=float,
        default=barrier_defaults.sl_mult,
        help="múltiplo de sigma_H para el SL",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=barrier_defaults.horizon_bars,
        help="barras de la barrera vertical",
    )
    parser.add_argument("--folds", type=int, default=exp_defaults.n_splits)
    parser.add_argument("--threshold", type=float, default=exp_defaults.signal_threshold)
    parser.add_argument("--model", default="gbm", choices=["gbm", "logistic"])
    parser.add_argument(
        "--vol-model",
        default=exp_defaults.vol_kind,
        choices=["gbm", "xgb", "ridge"],
        help=(
            "estimador del target de volatilidad (el que pasó el gate): "
            "gbm = HistGradientBoosting de sklearn, el del gate de Fase 4; "
            "xgb = XGBoost con los mismos hiperparámetros; ridge = lineal de control"
        ),
    )
    parser.add_argument("--rolling", action="store_true", help="train rodante en vez de anclado")
    parser.add_argument(
        "--features",
        default="full",
        choices=sorted(FEATURE_SETS),
        help=(
            "qué familias entran: price = solo las 55 de Fase 2 (baseline); "
            "price+deriv = agrega derivados; full = agrega el núcleo del libro; "
            "full+near = agrega también el near-touch (cobertura ~30%%)"
        ),
    )
    parser.add_argument(
        "--until",
        default=None,
        metavar="YYYY-MM-DD[THH:MM]",
        help=(
            "corta la serie en esa fecha o barra UTC (inclusive). Existe para "
            "poder REPRODUCIR un run viejo: la DB crece con el feed en vivo, "
            "así que sin corte el mismo comando devuelve otra muestra cada día "
            "y el control de regresión bit a bit deja de ser comprobable"
        ),
    )
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    end_time = _end_time_ms(args.until)
    series = load_series(args.symbol, args.timeframe, end_time=end_time)
    if len(series) == 0:
        raise SystemExit(
            f"no hay velas de {args.symbol} {args.timeframe} en DB. "
            f"Correr primero: uv run python -m bob.data.download --symbol {args.symbol}"
        )
    logger.info("cargadas {:,} velas de {} {}", len(series), args.symbol, args.timeframe)

    use_deriv, use_book, use_near = FEATURE_SETS[args.features]
    config = ExperimentConfig(
        barrier=BarrierConfig(
            tp_mult=args.tp, sl_mult=args.sl, horizon_bars=args.horizon
        ),
        n_splits=args.folds,
        model_kind=args.model,
        vol_kind=args.vol_model,
        signal_threshold=args.threshold,
        expanding=not args.rolling,
        use_derivatives=use_deriv,
        use_book=use_book,
        use_book_near=use_near,
    )

    # El I/O de las familias de Fase 2b vive acá, no en `models/` (regla 3).
    derivatives = load_derivatives(args.symbol, "5m") if use_deriv else None
    funding = load_derivatives(args.symbol, "funding") if use_deriv else None
    book = load_book_depth(args.symbol, args.timeframe) if use_book else None
    if derivatives is not None:
        logger.info("derivados: {:,} puntos | funding: {:,}", len(derivatives), len(funding or []))
    if book is not None:
        logger.info("libro: {:,} barras", len(book))

    result = run_experiment(series, config, derivatives, funding, book)
    report = render_report(result)
    print("\n" + report)

    run_id = "dry-run"
    if not args.no_persist:
        run_id = persist_run(result)
    report_path, json_path = write_artifacts(result, run_id)

    logger.info("resumen: {}", render_summary(result))
    print(f"\nreporte : {report_path}")
    print(f"json    : {json_path}")
    if not args.no_persist:
        print(f"run_id  : {run_id}")


if __name__ == "__main__":
    main()
