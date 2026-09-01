"""Figuras de diagnostico del gate — dibuja los artefactos, no los recalcula.

El veredicto de la Fase 4 ya esta escrito y versionado en
`backend/artifacts/*.json`. Este script no corre el pipeline, no toca la DB y
no entrena nada: lee **un** artefacto y lo convierte en las cinco figuras que
una persona puede mirar en 30 segundos para juzgar la calidad del modelo sin
leer 8.000 caracteres de reporte.

    uv run python scripts/plot_diagnostics.py \
        artifacts/ETHUSDT-15m-full-20260825153235.json

Requiere el grupo opcional `viz` (matplotlib + seaborn + plotly), que **no**
esta en las dependencias core a proposito — el motor numerico es numpy puro y
el backend levanta sin nada de esto instalado:

    uv pip install -e ".[viz]"

Salida: PNG a 150 dpi + un HTML autocontenido en `docs/figures/`.

--------------------------------------------------------------------------
DOS COSAS QUE ESTE SCRIPT NO PUEDE INVENTAR, Y POR ESO ROTULA
--------------------------------------------------------------------------

1. **El artefacto no guarda predicciones por muestra**, solo metricas
   agregadas. El Mincer-Zarnowitz sale entonces como las **rectas ajustadas**
   (alpha, beta) de cada modelo, no como una nube de puntos. Es la
   informacion que hay; dibujar una nube seria fabricarla.

2. **La importancia por permutacion del artefacto es del target de DIRECCION**,
   medida en Delta Brier sobre el ultimo fold de `long`
   (`_permutation_importance` en `models/experiment.py`). No existe una
   importancia del target de volatilidad en los artefactos versionados. La
   figura se rotula por lo que es: etiquetarla como "features de volatilidad"
   le atribuiria al target que SI paso el gate la evidencia del que NO.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # sin backend interactivo: esto corre desatendido

import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from bob.models.experiment import (  # noqa: E402
    GATE_MAX_CALIBRATION_ERROR_PP,
    GATE_MIN_AUC,
    GATE_MIN_BSS,
)
from bob.utils.console import enable_utf8_stdout  # noqa: E402

#: `reliability_curve(min_bucket_n=20)`: los buckets por debajo se reportan
#: pero NO entran al criterio de calibracion del gate. La figura hace la misma
#: distincion, o promete precision donde hay un solo caso.
MIN_BUCKET_N = 20

#: z de dos colas al 95%, el mismo que usa la banda gaussiana del experimento.
_Z95 = 1.959963984540054

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_DIR = _BACKEND_DIR.parent
_DEFAULT_OUTDIR = _REPO_DIR / "docs" / "figures"

# --------------------------------------------------------------------------
# Paleta — instancia de referencia de dataviz, validada para daltonismo
# (peor par adyacente Delta E 9.1 protan, 22.9 vision normal). El color sigue
# a la ENTIDAD (el modelo), no a su ranking, y es el mismo en todas las
# figuras: quien aprendio que el GBM es azul no lo reaprende.
# --------------------------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
DIMMED = "#c9c8c2"

MODEL = "GBM (features)"
SERIES = {
    MODEL: "#2a78d6",  # slot 1 · azul
    "EWMA RiskMetrics": "#eb6834",  # slot 2 · naranja
    "HAR-RV": "#1baf7a",  # slot 3 · aqua
    "GARCH(1,1)": "#eda100",  # slot 4 · amarillo
}
CQR_COLOR = "#2a78d6"
GAUSS_COLOR = "#eb6834"
CRITICAL = "#d03b3b"


class ArtifactError(SystemExit):
    """Falta un campo: se muere nombrandolo, en vez de dibujar un grafico vacio."""

    def __init__(self, message: str) -> None:
        super().__init__(f"\n  ERROR de artefacto: {message}\n")


def need(node: Any, *path: str | int, where: str = "artefacto") -> Any:
    """Accede a `node[p0][p1]...` o muere nombrando el campo que falta.

    Un `.get(k, 0)` produciria una barra en cero indistinguible de un modelo
    que de verdad puntuo cero. Ese es exactamente el fallo silencioso que el
    proyecto no acepta en ninguna capa.
    """
    cur = node
    walked: list[str] = []
    for key in path:
        walked.append(str(key))
        trail = " -> ".join(walked)
        if isinstance(key, int):
            if not isinstance(cur, (list, tuple)) or not -len(cur) <= key < len(cur):
                raise ArtifactError(f"{where}: falta el indice '{trail}'")
        elif not isinstance(cur, dict) or key not in cur:
            disponibles = sorted(cur) if isinstance(cur, dict) else "(no es un dict)"
            raise ArtifactError(
                f"{where}: falta el campo '{trail}'. Disponibles aqui: {disponibles}"
            )
        cur = cur[key]
    if cur is None:
        raise ArtifactError(f"{where}: el campo '{' -> '.join(walked)}' viene en null")
    return cur


def _finite(value: Any, where: str) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"{where}: se esperaba un numero, vino {value!r}") from exc
    if not math.isfinite(num):
        raise ArtifactError(f"{where}: se esperaba un numero finito, vino {value!r}")
    return num


def _style() -> None:
    sns.set_theme(style="whitegrid", context="notebook")
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": "sans-serif",
            "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK_2,
            "axes.titlecolor": INK,
            "text.color": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK_2,
            "ytick.labelcolor": INK_2,
            "grid.color": GRID,
            "grid.linestyle": "-",
            "grid.linewidth": 0.8,
            "axes.grid": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
        }
    )


def _header(
    ax: Axes,
    title: str,
    subtitle: str,
    *,
    title_color: str = INK,
    title_size: float = 13.0,
    sub_size: float = 9.0,
    pad: float = 14.0,
) -> None:
    """Titulo y bajada apilados en puntos sobre el eje.

    `set_title` + un `text` en fraccion de ejes se pisan en cuanto cambia el
    tamano de la figura. Apilar en offset de PUNTOS es independiente del
    tamano, que es lo que hace reproducible el layout.
    """
    sub_height = (subtitle.count("\n") + 1) * sub_size * 1.45
    ax.annotate(
        subtitle,
        xy=(0, 1),
        xycoords="axes fraction",
        textcoords="offset points",
        xytext=(0, pad),
        ha="left",
        va="bottom",
        fontsize=sub_size,
        color=INK_2,
        annotation_clip=False,
    )
    ax.annotate(
        title,
        xy=(0, 1),
        xycoords="axes fraction",
        textcoords="offset points",
        xytext=(0, pad + sub_height + 8),
        ha="left",
        va="bottom",
        fontsize=title_size,
        color=title_color,
        annotation_clip=False,
    )


def _footer(ax: Axes, meta: dict[str, Any], extra: str = "", offset: float = 64.0) -> None:
    """Procedencia al pie: sin el run que la produjo, una figura no es evidencia."""
    base = (
        f"{meta['symbol']} {meta['timeframe']} · {meta['date_from']} .. {meta['date_to']} · "
        f"{meta['n_bars']:,} velas · {meta['n_features']} features · "
        f"walk-forward purgado, {meta['n_splits']} folds · run {meta['run_id']}"
    )
    ax.annotate(
        base + (f"\n{extra}" if extra else ""),
        xy=(0, 0),
        xycoords="axes fraction",
        textcoords="offset points",
        xytext=(0, -offset),
        fontsize=7,
        color=MUTED,
        ha="left",
        va="top",
        annotation_clip=False,
    )


def _show(path: Path) -> str:
    """Ruta relativa al repo cuando se puede; absoluta si `--outdir` cae afuera."""
    try:
        return str(path.relative_to(_REPO_DIR))
    except ValueError:
        return str(path)


def _save(fig: Figure, outdir: Path, name: str, dpi: int) -> None:
    path = outdir / name
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"  OK  {_show(path)}")


# ==========================================================================
# (a) Diagrama de confiabilidad — TARGET DE DIRECCION (NO paso el gate)
# ==========================================================================
def _draw_reliability_panel(
    ax: Axes,
    solid: list[dict[str, Any]],
    thin: list[dict[str, Any]],
    base_rate: float,
    where: str,
    *,
    zoomed: bool,
) -> None:
    """Dibuja diagonal, tasa base y buckets. El zoom solo cambia el rotulado."""
    lo, hi = ax.get_xlim()
    ax.plot([lo, hi], [lo, hi], color=AXIS, lw=1.4, zorder=1)
    ax.axhline(base_rate, color=MUTED, lw=1.0, ls=(0, (4, 3)), zorder=1)

    if solid:
        xs = [_finite(need(b, "mean_predicted", where=where), where) for b in solid]
        ys = [_finite(need(b, "observed_rate", where=where), where) for b in solid]
        ns = [int(need(b, "n", where=where)) for b in solid]
        span = max(ns) or 1
        ax.plot(xs, ys, color=SERIES[MODEL], lw=2.0, zorder=3)
        ax.scatter(
            xs,
            ys,
            s=[70 + 330 * (n / span) for n in ns],
            color=SERIES[MODEL],
            edgecolor=SURFACE,  # anillo de superficie, no un borde de contraste
            linewidth=2.0,
            zorder=4,
        )
        if zoomed:
            # Alternar arriba/abajo: los buckets caen casi encima uno del otro,
            # y en una sola banda vertical los rotulos se pisan.
            for i, (x, y, n, bucket) in enumerate(zip(xs, ys, ns, solid, strict=True)):
                err = _finite(need(bucket, "error_pp", where=where), f"{where}.error_pp")
                above = i % 2 == 1
                ax.annotate(
                    f"dijo {x:.0%} → acertó {y:.0%}\nn={n:,} · error {err:.1f}pp",
                    (x, y),
                    textcoords="offset points",
                    xytext=(0, 20 if above else -34),
                    ha="center",
                    fontsize=8,
                    color=INK_2,
                )

    for bucket in thin:
        x = _finite(need(bucket, "mean_predicted", where=where), where)
        y = _finite(need(bucket, "observed_rate", where=where), where)
        if not (lo <= x <= hi and lo <= y <= hi):
            continue
        # Sin rotulo: en el zoom quedan fuera de rango, y en el recuadro de
        # contexto el texto se sale del marco. El circulo hueco y la nota al
        # pie ya dicen que es un bucket con n<20.
        ax.scatter([x], [y], s=80, facecolor="none", edgecolor=MUTED, linewidth=1.6, zorder=3)


def plot_reliability(doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int) -> None:
    dirs = need(doc, "directions", where="confiabilidad")
    order = [d for d in ("long", "short") if d in dirs]
    if not order:
        raise ArtifactError("confiabilidad: 'directions' no trae ni 'long' ni 'short'")

    fig, raw_axes = plt.subplots(1, len(order), figsize=(13.0, 6.8))
    axes = list(raw_axes) if len(order) > 1 else [raw_axes]

    for ax, direction in zip(axes, order, strict=True):
        where = f"confiabilidad[{direction}]"
        model = need(dirs, direction, "model", where=where)
        buckets = need(model, "buckets", where=where)
        if not buckets:
            raise ArtifactError(f"{where}: 'buckets' viene vacio")

        auc = _finite(need(model, "auc", where=where), f"{where}.auc")
        bss = _finite(need(model, "brier_skill_score", where=where), f"{where}.bss")
        cal = _finite(
            need(model, "mean_calibration_error_pp", where=where),
            f"{where}.mean_calibration_error_pp",
        )
        base_rate = _finite(need(model, "base_rate", where=where), f"{where}.base_rate")

        solid = [b for b in buckets if int(need(b, "n", where=where)) >= MIN_BUCKET_N]
        thin = [b for b in buckets if int(need(b, "n", where=where)) < MIN_BUCKET_N]
        if not solid:
            raise ArtifactError(f"{where}: ningun bucket llega a n>={MIN_BUCKET_N}")

        # El eje principal va ZOOMEADO: toda la informacion vive en una franja
        # de ~15pp y en el marco 0-1 no se lee nada. El marco completo queda de
        # contexto en el recuadro chico, que es donde se ve el mensaje de
        # verdad: el modelo nunca sale de esa franja.
        pts = [_finite(need(b, "mean_predicted", where=where), where) for b in solid] + [
            _finite(need(b, "observed_rate", where=where), where) for b in solid
        ]
        pad = max(max(pts) - min(pts), 0.05) * 0.75
        zlo, zhi = min(pts) - pad, max(pts) + pad

        ax.set_xlim(zlo, zhi)
        ax.set_ylim(zlo, zhi)
        ax.set_aspect("equal")
        _draw_reliability_panel(ax, solid, thin, base_rate, where, zoomed=True)
        ax.text(
            zhi - (zhi - zlo) * 0.012,
            zhi - (zhi - zlo) * 0.012,
            "calibración ideal",
            color=MUTED,
            fontsize=8.5,
            rotation=45,
            rotation_mode="anchor",
            ha="right",
            va="bottom",
        )
        ax.text(
            zlo + (zhi - zlo) * 0.02,
            base_rate + (zhi - zlo) * 0.015,
            f"tasa base {base_rate:.1%}",
            color=MUTED,
            fontsize=8.5,
        )

        axins = ax.inset_axes((0.06, 0.61, 0.34, 0.35))
        axins.set_xlim(-0.02, 1.02)
        axins.set_ylim(-0.02, 1.02)
        axins.set_aspect("equal")
        _draw_reliability_panel(axins, solid, thin, base_rate, where, zoomed=False)
        axins.add_patch(
            Rectangle(
                (zlo, zlo),
                zhi - zlo,
                zhi - zlo,
                fill=False,
                edgecolor=CRITICAL,
                lw=1.2,
                zorder=6,
            )
        )
        axins.set_xticks([0, 0.5, 1.0])
        axins.set_yticks([0, 0.5, 1.0])
        axins.tick_params(labelsize=7, colors=MUTED, length=2, pad=1)
        for spine in axins.spines.values():
            spine.set_edgecolor(AXIS)
        axins.set_facecolor(SURFACE)
        axins.grid(color=GRID, lw=0.5)
        axins.text(  # en el triangulo vacio bajo la diagonal, no sobre el titulo
            0.96,
            0.05,
            "rango completo 0–100%\nrecuadro rojo = este panel",
            transform=axins.transAxes,
            fontsize=7.5,
            color=MUTED,
            ha="right",
            va="bottom",
        )

        cal_ok = "PASA" if cal < GATE_MAX_CALIBRATION_ERROR_PP else "NO PASA"
        disc_ok = "PASA" if (auc > GATE_MIN_AUC and bss > GATE_MIN_BSS) else "NO PASA"
        ax.annotate(
            f"{direction.upper()}\n"
            f"calibración {cal:.1f}pp  (umbral <{GATE_MAX_CALIBRATION_ERROR_PP:.0f}pp)"
            f"  →  {cal_ok}\n"
            f"AUC {auc:.3f}  (umbral >{GATE_MIN_AUC:.2f})   ·   "
            f"BSS {bss:+.4f}  (umbral >{GATE_MIN_BSS:.0f})  →  {disc_ok}",
            xy=(0.5, 1),
            xycoords="axes fraction",
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            va="bottom",
            fontsize=10.5,
            color=INK,
            annotation_clip=False,
        )
        ax.set_xlabel("probabilidad predicha")
        ax.set_ylabel("frecuencia observada")

    _header(
        axes[0],
        "TARGET 1 — Dirección: P(TP antes que SL)\n"
        "NO habilitado para operar — se muestra en gris, etiquetado «experimental»",
        "El modelo CALIBRA (dentro del zoom, los puntos caen sobre la diagonal) y NO DISCRIMINA: "
        "colapsa sobre la tasa base — nunca sale de\nuna franja de ~10pp — y no separa setups "
        "buenos de malos. El gate exige los dos criterios y este target falla el segundo, así que "
        "no emite\nseñales. Un modelo que predice siempre la tasa base está perfectamente "
        "calibrado por construcción, y es inútil.",
        title_color=CRITICAL,
        title_size=13.5,
        pad=54,  # deja pasar el titulo de panel, que va centrado sobre cada eje
    )
    _footer(
        axes[0],
        meta,
        f"Buckets fijos de 10pp; el área del punto es el n del bucket. Los buckets con "
        f"n<{MIN_BUCKET_N} (círculo hueco) se reportan pero no entran al criterio de calibración.",
    )
    _save(fig, outdir, "reliability_direction.png", dpi)


# ==========================================================================
# (b) Mincer-Zarnowitz — TARGET DE VOLATILIDAD (paso el gate)
# ==========================================================================
_VOL_MODELS = (
    (MODEL, "model"),
    ("EWMA RiskMetrics", "baseline_ewma"),
    ("HAR-RV", "baseline_har"),
    ("GARCH(1,1)", "baseline_garch"),
)


def _sigma_scale(doc: dict[str, Any]) -> float:
    """Sigma media implicita, para que el eje tenga la escala real del activo.

    El artefacto no guarda predicciones por muestra. Lo unico que codifica una
    escala de sigma es el ancho de la banda gaussiana, que el experimento
    construye como +-z*sigma: `mean_width = 2*z*sigma`. Es una derivacion del
    artefacto, no un rango elegido a ojo.
    """
    width = _finite(
        need(doc, "intervals", "gaussian", "0.05", "mean_width", where="mincer-zarnowitz"),
        "intervals.gaussian[0.05].mean_width",
    )
    return width / (2.0 * _Z95)


def plot_mincer_zarnowitz(
    doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int
) -> None:
    vol = need(doc, "volatility", where="mincer-zarnowitz")
    sigma_bar = _sigma_scale(doc)
    hi = 3.0 * sigma_bar

    fig, ax = plt.subplots(figsize=(9.6, 6.6))
    ax.plot([0, hi], [0, hi], color=AXIS, lw=1.6, zorder=1)
    ax.text(
        hi * 0.99,
        hi * 0.99,
        "insesgado y eficiente (α=0, β=1)",
        color=MUTED,
        fontsize=8.5,
        rotation=45,
        rotation_mode="anchor",
        ha="right",
        va="bottom",
    )

    ends: list[tuple[float, str, str]] = []
    for label, key in _VOL_MODELS:
        node = need(vol, key, where="mincer-zarnowitz")
        alpha = _finite(
            need(node, "mincer_zarnowitz_alpha", where=f"volatility.{key}"),
            f"volatility.{key}.mincer_zarnowitz_alpha",
        )
        beta = _finite(
            need(node, "mincer_zarnowitz_beta", where=f"volatility.{key}"),
            f"volatility.{key}.mincer_zarnowitz_beta",
        )
        end = alpha + beta * hi
        ax.plot([0, hi], [alpha, end], color=SERIES[label], lw=2.2, zorder=3)
        ends.append((end, label, f"{label}\nα={alpha:+.5f}   β={beta:.3f}"))

    ax.set_xlim(0, hi)
    ax.set_ylim(0, hi)

    # EWMA y GARCH terminan casi en el mismo punto y sus rotulos se montan.
    # Se separan verticalmente sin mover las rectas: la etiqueta lleva su guia.
    gap = hi * 0.115
    ends.sort()
    placed: list[float] = []
    for value, _label, _text in ends:
        y = value if not placed else max(value, placed[-1] + gap)
        placed.append(y)
    shift = max(0.0, placed[-1] - hi) if placed else 0.0
    for (value, label, text), y_raw in zip(ends, placed, strict=True):
        y = y_raw - shift
        # <=4 series -> etiqueta directa; ademas el WARN de contraste de aqua y
        # amarillo sobre la superficie clara obliga a rotular, no basta el color.
        ax.annotate(
            text,
            xy=(hi, value),
            xytext=(24, y),
            textcoords=("offset points", "data"),
            fontsize=9,
            color=INK_2,
            va="center",
            annotation_clip=False,
            arrowprops={
                "arrowstyle": "-",
                "color": SERIES[label],
                "lw": 1.0,
                "shrinkA": 2,
                "shrinkB": 4,
            },
        )

    ax.set_xlabel("volatilidad pronosticada  σ̂   (retorno, H = 16 barras)")
    ax.set_ylabel("volatilidad realizada esperada  E[σ | σ̂]")
    _header(
        ax,
        "TARGET 2 — Volatilidad realizada: regresión de Mincer-Zarnowitz\n"
        "Validado — es el target sobre el que se apoya el producto",
        "β<1 = pronóstico atenuado: el modelo aplasta los movimientos hacia su media y hay que "
        "reescalarlo. El GBM llega a β=1.06\ncon α≈0, casi sobre la diagonal; EWMA y GARCH se "
        "quedan en β≈0.53, o sea pierden la mitad de la señal.",
    )
    fig.subplots_adjust(right=0.68)
    _footer(
        ax,
        meta,
        "El artefacto guarda (α, β) y no las predicciones por muestra: son las RECTAS ajustadas, "
        "no una nube de puntos.\n"
        f"El eje llega a 3·σ̄ con σ̄={sigma_bar:.5f}, deducida del ancho medio de la banda "
        "gaussiana al 95% (ancho = 2zσ).",
    )
    _save(fig, outdir, "mincer_zarnowitz_volatility.png", dpi)


# ==========================================================================
# (c) Baselines: RMSE y QLIKE + Diebold-Mariano
# ==========================================================================
#: El artefacto solo trae DM contra EWMA y HAR. Contra GARCH no se corrio, y
#: eso se rotula "no está en el artefacto" — el hueco no se deja mudo.
_DM_KEY = {"baseline_ewma": "dm_vs_ewma", "baseline_har": "dm_vs_har"}

_BASELINE_ORDER = (
    (MODEL, "model"),
    ("EWMA RiskMetrics", "baseline_ewma"),
    ("GARCH(1,1)", "baseline_garch"),
    ("HAR-RV", "baseline_har"),
)


def _baseline_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    vol = need(doc, "volatility", where="baselines")
    rows: list[dict[str, Any]] = []
    for label, key in _BASELINE_ORDER:
        node = need(vol, key, where="baselines")
        dm_p: float | None = None
        dm_key = _DM_KEY.get(key)
        if dm_key is not None and dm_key in vol:
            dm_p = _finite(
                need(vol, dm_key, "p_value", where="baselines"), f"volatility.{dm_key}.p_value"
            )
        rows.append(
            {
                "label": label,
                "key": key,
                "rmse": _finite(need(node, "rmse", where=f"volatility.{key}"), f"{key}.rmse"),
                "qlike": _finite(need(node, "qlike", where=f"volatility.{key}"), f"{key}.qlike"),
                "r2": _finite(need(node, "r2", where=f"volatility.{key}"), f"{key}.r2"),
                "dm_p": dm_p,
            }
        )
    return rows


def _dm_note(row: dict[str, Any]) -> str:
    if row["key"] == "model":
        return "modelo de referencia"
    if row["dm_p"] is None:
        return "sin DM en el artefacto"
    p = float(row["dm_p"])
    return "DM p<0.0001" if p < 1e-4 else f"DM p={p:.4f}"


def plot_baselines(doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int) -> None:
    rows = _baseline_rows(doc)
    labels = [str(r["label"]) for r in rows]
    # Una sola serie por panel sobre categorias nominales -> un solo color, con
    # enfasis en el modelo. Nada de rampa por valor: duplicaria el largo de la
    # barra en el tono sin agregar informacion.
    colors = [SERIES[MODEL] if r["key"] == "model" else DIMMED for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(12.6, 6.0))
    for ax, metric, nice in (
        (axes[0], "rmse", "RMSE  ·  menor es mejor"),
        (axes[1], "qlike", "QLIKE  ·  menor es mejor"),
    ):
        values = [float(r[metric]) for r in rows]
        bars = ax.bar(labels, values, color=colors, width=0.62, zorder=3)
        for bar, row, value in zip(bars, rows, values, strict=True):
            is_model = row["key"] == "model"
            ax.annotate(
                f"{value:.5f}\n{_dm_note(row)}"
                if metric == "rmse"
                else f"{value:.4f}\n{_dm_note(row)}",
                (bar.get_x() + bar.get_width() / 2, value),
                textcoords="offset points",
                xytext=(0, 7),
                ha="center",
                fontsize=8.5,
                color=INK if is_model else INK_2,
                fontweight="bold" if is_model else "normal",
            )
        ax.set_ylim(0, max(values) * 1.32)
        ax.annotate(
            nice,
            xy=(0, 1),
            xycoords="axes fraction",
            textcoords="offset points",
            xytext=(0, 8),
            ha="left",
            va="bottom",
            fontsize=11,
            color=INK,
            annotation_clip=False,
        )
        ax.grid(axis="x", visible=False)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", labelrotation=12)

    _header(
        axes[0],
        "TARGET 2 — El GBM sobre features le gana a los tres baselines econométricos\n"
        "Validado — Diebold-Mariano contra EWMA y HAR-RV con p < 0.0001",
        "Los baselines están escritos desde cero en numpy (models/baselines.py): el número que "
        "decide no puede ser una caja negra.\nEl GARCH(1,1) cae a EWMA si no converge, para que "
        "una no-convergencia silenciosa no aparente skill que no hay.",
        pad=34,  # deja pasar el titulo de cada panel
    )
    _footer(
        axes[0],
        meta,
        f"{int(need(doc, 'volatility', 'n_samples', where='baselines')):,} predicciones "
        "out-of-sample.",
    )
    _save(fig, outdir, "baselines_volatility.png", dpi)


# ==========================================================================
# (d) Cobertura conformal vs nominal
# ==========================================================================
_METHODS = ("CQR + ACI", "gaussiano ±zσ")
_METHOD_COLOR = {"CQR + ACI": CQR_COLOR, "gaussiano ±zσ": GAUSS_COLOR}


def _coverage_rows(doc: dict[str, Any]) -> list[tuple[float, int, dict[str, tuple[float, float]]]]:
    intervals = need(doc, "intervals", where="cobertura")
    conformal = need(intervals, "conformal", where="cobertura")
    gaussian = need(intervals, "gaussian", where="cobertura")
    if not conformal:
        raise ArtifactError("cobertura: 'intervals.conformal' viene vacio")

    out: list[tuple[float, int, dict[str, tuple[float, float]]]] = []
    for alpha_key in sorted(conformal, key=float, reverse=True):
        if alpha_key not in gaussian:
            raise ArtifactError(
                f"cobertura: el nivel '{alpha_key}' esta en conformal y falta en gaussian"
            )
        where = f"intervals[alpha={alpha_key}]"
        nominal = _finite(
            need(conformal, alpha_key, "nominal_coverage", where=where), f"{where}.nominal_coverage"
        )
        n = int(need(conformal, alpha_key, "n", where=where))
        per: dict[str, tuple[float, float]] = {}
        for name, node in (("CQR + ACI", conformal), ("gaussiano ±zσ", gaussian)):
            per[name] = (
                _finite(
                    need(node, alpha_key, "empirical_coverage", where=where),
                    f"{where}.empirical_coverage",
                ),
                _finite(
                    need(node, alpha_key, "winkler_score", where=where), f"{where}.winkler_score"
                ),
            )
        out.append((nominal, n, per))
    return out


def plot_coverage(doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int) -> None:
    rows = _coverage_rows(doc)

    # Puntos y no barras: la escala util es de ~15pp y una barra truncada
    # deja de ser proporcional a su valor — el largo pasa a mentir. La
    # posicion sobre el eje no tiene ese problema.
    fig, ax = plt.subplots(figsize=(10.6, 4.6))
    lanes = list(range(len(rows)))
    lane_gap = 0.26

    for lane, (nominal, _n, per) in zip(lanes, rows, strict=True):
        # Marca corta dentro de su propia banda: una axvline de punta a punta
        # cruzaria el otro nivel nominal y se leeria como si aplicara ahi.
        ax.plot(
            [nominal, nominal],
            [lane - 0.30, lane + 0.30],
            color=INK,
            lw=1.8,
            zorder=2,
            solid_capstyle="butt",
        )
        ax.annotate(
            f"nominal {nominal:.0%}",
            (nominal, lane + 0.31),
            fontsize=9,
            color=INK,
            ha="center",
            va="bottom",
        )
        for i, method in enumerate(_METHODS):
            y = lane + (i - 0.5) * lane_gap
            empirical, winkler = per[method]
            ax.plot(
                [nominal, empirical],
                [y, y],
                color=_METHOD_COLOR[method],
                lw=2.0,
                alpha=0.45,
                zorder=3,
            )
            ax.scatter(
                [empirical],
                [y],
                s=150,
                color=_METHOD_COLOR[method],
                edgecolor=SURFACE,  # anillo de superficie sobre la linea nominal
                linewidth=2.0,
                zorder=4,
                label=method if lane == 0 else None,
            )
            ax.annotate(
                f"{empirical:.1%}  ({(empirical - nominal) * 100.0:+.1f}pp)"
                f"   ·   Winkler {winkler:.5f}",
                (empirical, y),
                textcoords="offset points",
                xytext=(-14 if empirical < nominal else 14, 0),
                ha="right" if empirical < nominal else "left",
                va="center",
                fontsize=9,
                color=INK_2,
            )

    ax.set_yticks(lanes)
    ax.set_yticklabels([f"nivel\nnominal {n:.0%}" for n, _n, _p in rows], fontsize=10)
    ax.set_ylim(len(rows) - 0.48, -0.52)  # el 95% arriba, como se lee la tabla
    ax.set_xlim(0.715, 1.005)  # deja aire a la izquierda para el rotulo del 79.9%
    ax.xaxis.set_major_formatter(lambda v, _pos: f"{v:.0%}")
    ax.set_xlabel("cobertura empírica out-of-sample")
    ax.grid(axis="y", visible=False)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", ncol=2, fontsize=9.5)
    _header(
        ax,
        "TARGET 3 — Cono de precio: cobertura empírica vs nominal\n"
        "Validado — CQR + ACI se pega al nominal; la banda gaussiana se queda corta al 95%",
        "La línea negra es la promesa; el punto es lo que ocurrió, y el segmento mide la "
        "distancia entre las dos. Sub-cubrir es el\nerror caro: promete un cono que el precio "
        "atraviesa más seguido de lo dicho. Winkler castiga a la vez el ancho y las\n"
        "violaciones, y es donde se ve el precio real de la banda gaussiana (0.084 vs 0.057 al "
        "95%): menor es mejor.",
    )
    _footer(ax, meta, f"{rows[0][1]:,} predicciones out-of-sample.")
    _save(fig, outdir, "conformal_coverage.png", dpi)


# ==========================================================================
# (e) Importancia por permutacion — del target de DIRECCION (ver docstring)
# ==========================================================================
def plot_importance(doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int) -> None:
    top = need(doc, "importance_top", where="importancia")
    if not isinstance(top, list) or not top:
        raise ArtifactError("importancia: 'importance_top' viene vacio o no es una lista")

    pairs: list[tuple[str, float]] = []
    for i in range(min(20, len(top))):
        name = need(top, i, 0, where="importancia")
        value = _finite(need(top, i, 1, where="importancia"), f"importance_top[{i}]")
        pairs.append((str(name), value))
    pairs.reverse()  # barh dibuja de abajo hacia arriba

    names = [p[0] for p in pairs]
    values = [p[1] for p in pairs]

    fig, ax = plt.subplots(figsize=(9.8, 7.6))
    ax.barh(names, values, color=SERIES[MODEL], height=0.68, zorder=3)  # 1 serie -> 1 color
    for name, value in zip(names, values, strict=True):
        ax.annotate(
            f"{value:+.5f}",
            (value, name),
            textcoords="offset points",
            xytext=(6, 0),
            va="center",
            fontsize=8.5,
            color=INK_2,
        )
    ax.set_xlim(0, (max(values) or 1.0) * 1.22)
    ax.set_xlabel("Δ Brier al permutar el feature   (mayor = más lo usaba el modelo)")
    ax.grid(axis="y", visible=False)
    ax.set_axisbelow(True)
    _header(
        ax,
        f"Importancia por permutación — top {len(pairs)} del TARGET 1, el de DIRECCIÓN\n"
        "Es el target que NO pasó el gate: describe de qué se agarró un modelo que igual "
        "no discrimina",
        "No hay importancia del target de volatilidad en los artefactos versionados: "
        "`_permutation_importance` se mide en Brier\nsobre el último fold de `long`. Y una "
        "importancia positiva NO es ganancia fuera de muestra: en la variante `price+deriv` "
        "la familia\n`derivados` salía segunda, y agregarla igual empeoró el AUC.",
        title_size=12.5,
    )
    _footer(ax, meta, "Permutación sobre datos de TEST (3 repeticiones), nunca de train.")
    _save(fig, outdir, "permutation_importance_direction.png", dpi)


# ==========================================================================
# Interactivo (Plotly) — version de (c)
# ==========================================================================
def plot_baselines_interactive(
    doc: dict[str, Any], meta: dict[str, Any], outdir: Path, plotly_js: str = "inline"
) -> None:
    import plotly.graph_objects as go

    rows = _baseline_rows(doc)
    labels = [str(r["label"]) for r in rows]
    colors = [SERIES[MODEL] if r["key"] == "model" else DIMMED for r in rows]
    hover = [
        f"<b>{r['label']}</b><br>RMSE {float(r['rmse']):.5f}<br>QLIKE {float(r['qlike']):.4f}"
        f"<br>R² vs la media {float(r['r2']):+.3f}<br>{_dm_note(r)}<extra></extra>"
        for r in rows
    ]

    fig = go.Figure()
    for i, (metric, nice) in enumerate((("rmse", "RMSE"), ("qlike", "QLIKE"))):
        fig.add_bar(
            x=labels,
            y=[float(r[metric]) for r in rows],
            marker_color=colors,
            name=nice,
            hovertemplate=hover,
            text=[
                f"{float(r[metric]):.5f}" if metric == "rmse" else f"{float(r[metric]):.4f}"
                for r in rows
            ],
            textposition="outside",
            textfont={"color": INK_2, "size": 12},
            visible=(i == 0),
        )

    fig.update_layout(
        title={
            "text": (
                "TARGET 2 — Volatilidad realizada: GBM vs baselines econométricos<br>"
                f"<span style='font-size:12.5px;color:{INK_2}'>"
                "Validado. Diebold-Mariano contra EWMA y HAR-RV con p &lt; 0.0001. "
                "Menor es mejor en las dos métricas.</span>"
            ),
            "x": 0,
            "xanchor": "left",
            "font": {"size": 18, "color": INK},
        },
        updatemenus=[
            {
                "type": "buttons",
                "direction": "right",
                "x": 0,
                "xanchor": "left",
                "y": 1.14,
                "yanchor": "top",
                "showactive": True,
                "bgcolor": SURFACE,
                "bordercolor": AXIS,
                "font": {"color": INK_2, "size": 12},
                "buttons": [
                    {
                        "label": "  RMSE  ",
                        "method": "update",
                        "args": [
                            {"visible": [True, False]},
                            {"yaxis": {"title": "RMSE", "gridcolor": GRID, "zeroline": False}},
                        ],
                    },
                    {
                        "label": "  QLIKE  ",
                        "method": "update",
                        "args": [
                            {"visible": [False, True]},
                            {"yaxis": {"title": "QLIKE", "gridcolor": GRID, "zeroline": False}},
                        ],
                    },
                ],
            }
        ],
        plot_bgcolor=SURFACE,
        paper_bgcolor=SURFACE,
        font={"family": "Segoe UI, system-ui, sans-serif", "color": INK_2},
        showlegend=False,
        hoverlabel={"bgcolor": SURFACE, "bordercolor": AXIS, "font": {"color": INK}},
        margin={"l": 75, "r": 45, "t": 175, "b": 145},
        height=640,
        xaxis={"showgrid": False, "linecolor": AXIS, "tickfont": {"color": INK_2}},
        yaxis={
            "gridcolor": GRID,
            "zeroline": False,
            "linecolor": AXIS,
            "title": "RMSE",
            "tickfont": {"color": INK_2},
        },
    )
    fig.add_annotation(
        text=(
            f"{meta['symbol']} {meta['timeframe']} · {meta['date_from']} .. {meta['date_to']} · "
            f"{meta['n_bars']:,} velas · {meta['n_features']} features · walk-forward purgado, "
            f"{meta['n_splits']} folds<br>"
            f"{int(need(doc, 'volatility', 'n_samples', where='baselines')):,} predicciones "
            f"out-of-sample · run {meta['run_id']}<br>"
            "Los baselines están escritos desde cero en numpy: el número que decide no puede ser "
            "una caja negra."
        ),
        xref="paper",
        yref="paper",
        x=0,
        y=-0.22,
        showarrow=False,
        align="left",
        font={"size": 10.5, "color": MUTED},
    )

    path = outdir / "baselines_volatility.html"
    fig.write_html(str(path), include_plotlyjs=plotly_js, full_html=True)
    size_mb = path.stat().st_size / 1e6
    modo = "autocontenido" if plotly_js == "inline" else "plotly.js desde CDN"
    print(f"  OK  {_show(path)}  ({modo}, {size_mb:.1f} MB)")


# ==========================================================================
def _meta(doc: dict[str, Any], artifact: Path) -> dict[str, Any]:
    return {
        "symbol": need(doc, "symbol", where="cabecera"),
        "timeframe": need(doc, "timeframe", where="cabecera"),
        "date_from": need(doc, "date_from", where="cabecera"),
        "date_to": need(doc, "date_to", where="cabecera"),
        "n_bars": int(need(doc, "n_bars", where="cabecera")),
        "n_features": int(need(doc, "n_features", where="cabecera")),
        "n_splits": int(need(doc, "config", "n_splits", where="cabecera")),
        "run_id": artifact.stem,
    }


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Dibuja las figuras de diagnostico de un artefacto del gate. No recalcula nada."
    )
    parser.add_argument("artifact", type=Path, help="ruta al .json de backend/artifacts/")
    parser.add_argument(
        "--outdir", type=Path, default=_DEFAULT_OUTDIR, help="destino (default: docs/figures/)"
    )
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument(
        "--plotly-js",
        choices=("inline", "cdn"),
        default="inline",
        help=(
            "inline (default) empotra plotly.js y deja el HTML autocontenido, a costa de "
            "~4 MB por archivo; cdn lo deja en ~30 KB pero exige internet para abrirlo."
        ),
    )
    args = parser.parse_args(argv)

    if not args.artifact.is_file():
        raise ArtifactError(f"no existe el artefacto '{args.artifact}'")
    try:
        doc = json.loads(args.artifact.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"'{args.artifact}' no es JSON valido: {exc}") from exc
    if not isinstance(doc, dict):
        raise ArtifactError(f"'{args.artifact}' no es un objeto JSON")

    meta = _meta(doc, args.artifact)
    args.outdir.mkdir(parents=True, exist_ok=True)
    _style()

    print(f"\n  artefacto : {args.artifact}")
    print(f"  run       : {meta['symbol']} {meta['timeframe']} · {meta['n_features']} features")
    print(f"  salida    : {args.outdir}\n")

    plot_reliability(doc, meta, args.outdir, args.dpi)
    plot_mincer_zarnowitz(doc, meta, args.outdir, args.dpi)
    plot_baselines(doc, meta, args.outdir, args.dpi)
    plot_coverage(doc, meta, args.outdir, args.dpi)
    plot_importance(doc, meta, args.outdir, args.dpi)
    plot_baselines_interactive(doc, meta, args.outdir, args.plotly_js)

    print("\n  listo — 5 PNG + 1 HTML interactivo\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
