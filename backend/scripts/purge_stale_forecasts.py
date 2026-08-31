"""Saca de `ForecastRecord` los pronósticos de un `model_version` superado.

Existe por la lección de la Fase 4-bis: **el tracker no segrega por
`model_version`**. Si en la tabla conviven pronósticos de dos versiones del
modelo, `paper/tracker.py` los promedia sin decir nada, y el reporte forward
—que es todo el producto de la corrida— describe una mezcla de dos modelos que
no existe en ninguna parte. Ese es exactamente el motivo por el que XGBoost se
agregó *antes* de arrancar la acumulación y no después.

Es una herramienta de una vez, para correr **antes** de empezar a acumular. A
mitad de corrida no arregla nada: lo que hay que hacer entonces es decidir qué
mitad de la muestra se tira, y eso no lo decide un script.

Respalda a JSON antes de borrar, así que es reversible.

    uv run python scripts/purge_stale_forecasts.py            # muestra qué haría
    uv run python scripts/purge_stale_forecasts.py --apply    # respalda y borra
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", default=str(BACKEND_DIR / "bob.db"))
    p.add_argument(
        "--keep",
        default=None,
        help="model_version a conservar (default: la que emite el código actual)",
    )
    p.add_argument("--apply", action="store_true", help="sin esto, solo reporta")
    args = p.parse_args(argv)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    versiones = con.execute(
        "SELECT model_version, COUNT(*) n, MIN(open_time) desde, MAX(open_time) hasta "
        "FROM forecastrecord GROUP BY model_version ORDER BY hasta DESC"
    ).fetchall()

    if not versiones:
        print("forecastrecord está vacía — nada que purgar.")
        return 0

    print("versiones presentes:")
    for v in versiones:
        print(
            f"  {v['model_version']:<34} {v['n']:>5} filas  "
            f"{_utc(v['desde'])} .. {_utc(v['hasta'])}"
        )

    # El default se le pregunta al CÓDIGO, no a la tabla. Antes de arrancar, la
    # versión que va a emitir esta corrida todavía no está en la DB — mirar la
    # tabla y quedarse con "la más reciente" conservaría justo la vieja.
    keep = args.keep or _version_actual()
    print(f"\nversión que emite el código actual: {keep}")

    if all(v["model_version"] == keep for v in versiones):
        print("la muestra ya es homogénea y coincide con el código — nada que hacer.")
        return 0

    print(f"se conserva: {keep}")
    doomed = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM forecastrecord WHERE model_version IS NOT ? "
            "AND model_version != ?",
            (keep, keep),
        )
    ]
    print(f"se purgan  : {len(doomed)} fila(s)")

    if not args.apply:
        print("\n(simulación — volver a correr con --apply para ejecutarlo)")
        return 0

    backup = BACKEND_DIR / "logs" / (
        f"forecast_purgados_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(doomed, indent=2, default=str), encoding="utf-8")
    print(f"respaldo   : {backup}")

    con.execute(
        "DELETE FROM forecastrecord WHERE model_version IS NOT ? AND model_version != ?",
        (keep, keep),
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM forecastrecord").fetchone()[0]
    print(f"listo — quedan {n} fila(s), todas de {keep}")
    return 0


def _version_actual() -> str:
    """El `model_version` que produciría `fit_bundle` hoy.

    Se arma igual que en `production.fit_bundle` para que las dos no se puedan
    ir separando en silencio.
    """
    from bob.config import settings
    from bob.models.experiment import MODEL_VERSION, ExperimentConfig

    vol_kind = getattr(settings, "bob_vol_kind", None) or ExperimentConfig().vol_kind
    return f"{MODEL_VERSION}+vol={vol_kind}"


def _utc(ms: int | None) -> str:
    if ms is None:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%m-%d %H:%M")


if __name__ == "__main__":
    raise SystemExit(main())
