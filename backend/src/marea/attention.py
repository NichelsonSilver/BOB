"""Atención y actividad cripto, para contrastar la narrativa (M5.7). PURO + CLI.

Responde una pregunta: **¿lo que dice una noticia se ve en los datos?** "Hay
furor por cripto" se contrasta con las vistas de Wikipedia; "DeFi está
explotando", con el TVL y el volumen de DEX. Peso bajo y declarado: es la
capa 4, nunca feature de un modelo (`docs/REESTRUCTURACION_MAREA.md` §3.1).

Para cada serie: media de los últimos 7 días, su cambio contra los 30 días
previos, y su percentil contra las medias de 7 días del **año anterior** (no
de toda la historia: la atención a Wikipedia cae año a año por cambios de
hábito que nada tienen que ver con cripto).

    uv run python -m marea.attention
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

WINDOW = 7
LOOKBACK_DAYS = 365


@dataclass(frozen=True)
class AttentionRow:
    key: str
    as_of: date
    mean_7d: float
    #: Cambio de la media de 7 días contra la de 7 días que terminó 30 días antes.
    change_30d: float | None
    #: Percentil contra las medias de 7 días de los 365 días previos.
    pct_1y: float | None


def _means(series: Sequence[tuple[date, float]]) -> dict[date, float]:
    values = dict(series)
    out: dict[date, float] = {}
    for d in sorted(values):
        span = [values.get(d - timedelta(days=k)) for k in range(WINDOW)]
        if all(x is not None for x in span):
            out[d] = sum(x for x in span if x is not None) / WINDOW
    return out


def attention_row(key: str, series: Sequence[tuple[date, float]]) -> AttentionRow | None:
    means = _means(series)
    if not means:
        return None
    day = max(means)
    now = means[day]
    prev = means.get(day - timedelta(days=30))
    past = [v for d, v in means.items() if day - timedelta(days=LOOKBACK_DAYS) <= d < day]
    return AttentionRow(
        key=key,
        as_of=day,
        mean_7d=now,
        change_30d=now / prev - 1.0 if prev else None,
        pct_1y=sum(v < now for v in past) / len(past) if len(past) >= 30 else None,
    )


def reading(row: AttentionRow) -> str:
    if row.pct_1y is None:
        return "sin historia suficiente"
    if row.pct_1y >= 0.80:
        return "alta para el último año"
    if row.pct_1y <= 0.20:
        return "baja para el último año"
    return "en rango del último año"


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — I/O
    from datetime import UTC, datetime

    from bob.utils.console import enable_utf8_stdout
    from marea.config import DB_PATH
    from marea.data import store
    from marea.data.wikimedia import ARTICLES
    from marea.stablecoins import known_series

    enable_utf8_stdout()
    today = datetime.now(UTC).date()
    conn = store.connect(DB_PATH)
    try:
        rows: list[tuple[str, AttentionRow | None]] = []
        for sid, label in (
            ("DEFI_TVL", "TVL DeFi (USD)"),
            ("DEX_VOLUME", "Volumen DEX diario (USD)"),
        ):
            pts = known_series(store.load_stable_periods(conn, sid), today)
            rows.append(
                (
                    label,
                    attention_row(sid, [(p.obs_date, p.value) for p in pts if p.value is not None]),
                )
            )
        for art in ARTICLES:
            views = [(d, float(v)) for d, v in store.load_wiki(conn, art.key)]
            rows.append((f"Wikipedia: {art.title} (vistas)", attention_row(art.key, views)))
    finally:
        conn.close()
    print("Contraste de narrativa: atención y actividad (capa 4, peso bajo, no pronostica)")
    print(f"{'serie':<36}{'al':>12}{'media 7 d':>16}{'vs 30 d':>9}{'pct 1 año':>10}  lectura")
    for label, r in rows:
        if r is None:
            hint = (
                "falta WIKI_CONTACT en backend/.env"
                if "Wikipedia" in label
                else "correr `marea.data.ingest atencion`"
            )
            print(f"{label:<36}  sin datos: {hint}")
            continue
        chg = "—" if r.change_30d is None else f"{r.change_30d:+.0%}"
        pct = "—" if r.pct_1y is None else f"{r.pct_1y:.2f}"
        print(f"{label:<36}{r.as_of!s:>12}{r.mean_7d:>16,.0f}{chg:>9}{pct:>10}  {reading(r)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
