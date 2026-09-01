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

# La variante se lee de la CONFIG del artefacto, no del nombre del archivo, y
# se reutiliza el helper de compare.py en vez de reimplementarlo: ya aprendio
# que parsear por posicion del nombre se rompe (al agregar el estimador de
# volatilidad, `parts[-2]` empezo a devolver `xgb`). Dos implementaciones de
# esto divergen; una sola, no.
from bob.backtest.compare import _variant_from_config  # noqa: E402
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


def _save(fig: Figure, outdir: Path, stem: str, meta: dict[str, Any], dpi: int) -> None:
    """Guarda como `<stem>_<variante>.png`.

    El sufijo no es cosmetico: dibujar dos runs en el mismo directorio sin el
    lo unico que deja es el ultimo, y con los dos README apuntando al mismo
    archivo nadie se entera de cual esta viendo.
    """
    path = outdir / f"{stem}_{meta['slug']}.png"
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
            # Tabla en la esquina, no un rotulo por punto: la cantidad de
            # buckets depende del run —2 en `full`, 5 en `price+deriv`— y
            # cualquier esquema de offsets por punto se pisa en cuanto hay mas
            # de dos. El triangulo bajo la diagonal siempre esta vacio.
            lines = ["dijo   acertó          n     error"]
            for x, y, n, bucket in zip(xs, ys, ns, solid, strict=True):
                err = _finite(need(bucket, "error_pp", where=where), f"{where}.error_pp")
                lines.append(f"{x:>4.0%} → {y:>5.0%}   {n:>8,}   {err:>5.1f}pp")
            ax.text(
                0.985,
                0.03,
                "\n".join(lines),
                transform=ax.transAxes,
                fontsize=8,
                color=INK_2,
                family="monospace",  # columnas que tienen que alinearse
                ha="right",
                va="bottom",
                linespacing=1.5,
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
            # A la derecha: por la izquierda el recuadro de contexto se le monta
            # encima cuando la tasa base cae en la banda alta del eje, que es lo
            # que pasa en `price+deriv` short.
            zhi - (zhi - zlo) * 0.02,
            base_rate + (zhi - zlo) * 0.012,
            f"tasa base {base_rate:.1%}",
            color=MUTED,
            fontsize=8.5,
            ha="right",
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
    _save(fig, outdir, "reliability_direction", meta, dpi)


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
    _save(fig, outdir, "mincer_zarnowitz_volatility", meta, dpi)


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
    _save(fig, outdir, "baselines_volatility", meta, dpi)


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
    _save(fig, outdir, "conformal_coverage", meta, dpi)


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
    _save(fig, outdir, "permutation_importance_direction", meta, dpi)


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

    path = outdir / f"baselines_volatility_{meta['slug']}.html"
    fig.write_html(str(path), include_plotlyjs=plotly_js, full_html=True)
    size_mb = path.stat().st_size / 1e6
    modo = "autocontenido" if plotly_js == "inline" else "plotly.js desde CDN"
    print(f"  OK  {_show(path)}  ({modo}, {size_mb:.1f} MB)")


# ==========================================================================
# (f) Estabilidad por fold — lo que el pooled esconde
# ==========================================================================
DIR_COLOR = {"long": "#2a78d6", "short": "#eb6834"}


def _folds_by_direction(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    folds = need(doc, "folds", where="folds")
    if not isinstance(folds, list) or not folds:
        raise ArtifactError("folds: viene vacio o no es una lista")
    out: dict[str, list[dict[str, Any]]] = {}
    for row in folds:
        out.setdefault(str(need(row, "direction", where="folds")), []).append(row)
    return out


def _upper_first(text: str) -> str:
    """Mayuscula inicial sin tocar el resto: `.capitalize()` convertiria AUC en Auc."""
    return text[:1].upper() + text[1:]


def _crossings(
    by_dir: dict[str, list[dict[str, Any]]], dirs: list[str], key: str, threshold: float
) -> list[tuple[int, str]]:
    """(numero de fold, direccion) de los folds que cruzan el umbral por su cuenta."""
    out: list[tuple[int, str]] = []
    for direction in dirs:
        for i, row in enumerate(by_dir[direction], start=1):
            if _finite(need(row, key, where="folds"), f"folds.{key}") > threshold:
                out.append((i, direction))
    return out


def _crossing_phrase(items: list[tuple[int, str]], verbo: str) -> str | None:
    if not items:
        return None
    folds = sorted({i for i, _ in items})
    dirs = sorted({d for _, d in items})
    cual = f"el fold {folds[0]}" if len(folds) == 1 else "los folds " + ", ".join(map(str, folds))
    donde = "en las dos direcciones" if len(dirs) > 1 else f"en {dirs[0]}"
    return f"{cual} {verbo} {donde}"


def plot_fold_stability(doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int) -> None:
    by_dir = _folds_by_direction(doc)
    dirs = [d for d in ("long", "short") if d in by_dir]
    if not dirs:
        raise ArtifactError("folds: no hay filas de 'long' ni de 'short'")

    fig, axes = plt.subplots(1, 2, figsize=(13.4, 5.8))
    panels = (
        (axes[0], "auc", "AUC por fold", GATE_MIN_AUC, "umbral del gate", True),
        (
            axes[1],
            "calibration_error_pp",
            "Error de calibración por fold (pp)",
            GATE_MAX_CALIBRATION_ERROR_PP,
            "umbral del gate",
            False,
        ),
    )

    for ax, key, nice, threshold, thr_label, higher_is_better in panels:
        for direction in dirs:
            rows = by_dir[direction]
            xs = list(range(1, len(rows) + 1))
            ys = [_finite(need(r, key, where="folds"), f"folds.{key}") for r in rows]
            ax.plot(xs, ys, color=DIR_COLOR[direction], lw=2.0, zorder=3, label=direction)
            ax.scatter(
                xs,
                ys,
                s=110,
                color=DIR_COLOR[direction],
                edgecolor=SURFACE,
                linewidth=2.0,
                zorder=4,
            )
            # El fold que cruza el umbral por su cuenta se marca. Es el punto
            # de la figura: con ese fold solo, el gate se declararia aprobado.
            for x, y in zip(xs, ys, strict=True):
                crosses = y > threshold if higher_is_better else y > threshold
                if not crosses:
                    continue
                ax.scatter([x], [y], s=300, facecolor="none", edgecolor=CRITICAL, lw=1.8, zorder=5)
                ax.annotate(
                    f"{y:.3f} ({direction})" if higher_is_better else f"{y:.1f}pp ({direction})",
                    (x, y),
                    textcoords="offset points",
                    # long abajo, short arriba: en el fold 6 los dos valores
                    # caen casi en el mismo punto y en una sola banda se pisan.
                    xytext=(0, 16 if direction == "short" else -26),
                    ha="center",
                    fontsize=8.5,
                    color=CRITICAL,
                    fontweight="bold",
                )

            pooled_node = need(doc, "directions", direction, "model", where="folds")
            pooled = _finite(
                need(
                    pooled_node,
                    "auc" if key == "auc" else "mean_calibration_error_pp",
                    where="folds",
                ),
                "pooled",
            )
            ax.axhline(pooled, color=DIR_COLOR[direction], lw=1.0, ls=(0, (2, 3)), zorder=2)
            ax.annotate(
                f"pooled {direction} = {pooled:.3f}"
                if key == "auc"
                else f"pooled {direction} = {pooled:.1f}pp",
                (len(rows) + 0.06, pooled),
                fontsize=8,
                color=DIR_COLOR[direction],
                va="center",
                annotation_clip=False,
            )

        ax.axhline(threshold, color=CRITICAL, lw=1.4, ls=(0, (5, 4)), zorder=2)
        ax.annotate(
            f"{thr_label} ({threshold:.2f})"
            if higher_is_better
            else f"{thr_label} ({threshold:.0f}pp)",
            (0.62, threshold),
            fontsize=8.5,
            color=CRITICAL,
            va="bottom",
        )
        if higher_is_better:
            ax.axhline(0.5, color=MUTED, lw=1.0, zorder=1)
            ax.annotate("0.50 = moneda al aire", (0.62, 0.5), fontsize=8, color=MUTED, va="top")

        seen = [
            _finite(need(r, key, where="folds"), key)
            for direction in dirs
            for r in by_dir[direction]
        ] + [threshold]
        span = (max(seen) - min(seen)) or 1.0
        # El error de calibracion no puede ser negativo: un eje que baja a -2pp
        # sugiere un rango que no existe.
        floor = min(seen) - span * 0.22
        ax.set_ylim(max(floor, 0.0) if key != "auc" else floor, max(seen) + span * 0.20)

        n_folds = len(by_dir[dirs[0]])
        ax.set_xticks(list(range(1, n_folds + 1)))
        ax.set_xticklabels(
            [
                f"{i}\n{need(by_dir[dirs[0]][i - 1], 'test_from', where='folds')[:7]}"
                for i in range(1, n_folds + 1)
            ],
            fontsize=8.5,
        )
        ax.set_xlim(0.55, n_folds + 0.45)
        ax.set_xlabel("fold de test (inicio del bloque)")
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
        ax.legend(loc="lower left" if higher_is_better else "upper left", ncol=2, fontsize=9.5)

    # El texto se DEDUCE de los cruces medidos. Escrito a mano describia el run
    # `full` y quedaba falso en `price+deriv`, donde ningun fold falla la
    # calibracion — un pie de figura que miente sobre su propia figura.
    cruces = {
        "AUC": _crossings(by_dir, dirs, "auc", GATE_MIN_AUC),
        "calib": _crossings(by_dir, dirs, "calibration_error_pp", GATE_MAX_CALIBRATION_ERROR_PP),
    }
    frases = [
        p
        for p in (
            _crossing_phrase(cruces["AUC"], "supera el AUC exigido"),
            _crossing_phrase(cruces["calib"], "falla la calibración"),
        )
        if p
    ]
    if len(frases) == 2:
        subtitulo = "Los dos umbrales del gate se cruzan — cada uno en un fold distinto"
    elif len(frases) == 1:
        subtitulo = "Un umbral del gate se cruza en un fold suelto"
    else:
        subtitulo = "Ningún fold cruza los umbrales por su cuenta, pero el rango es amplio"
    detalle = (
        (
            _upper_first(", y ".join(frases)) + ": quedarse con un fold solo deja declarar\n"
            "aprobado o reprobado lo que uno quiera."
        )
        if frases
        else "Ninguno cruza un umbral por su cuenta, pero el rango entre el mejor y el peor\n"
        "fold es varias veces la distancia que separa al pooled de su umbral."
    )

    fig.subplots_adjust(right=0.87, wspace=0.30)
    _header(
        axes[0],
        "El número pooled esconde la varianza entre folds\n" + subtitulo,
        "Cada fold es un período de mercado distinto y el modelo se comporta distinto "
        "en cada uno.\n"
        + detalle
        + "\nPor eso el gate se evalúa sobre las predicciones pooled de todos los folds, "
        "y no sobre el mejor de ellos.",
        pad=32,
    )
    _footer(axes[0], meta, "Círculo rojo = el fold cruza ese umbral por su cuenta.")
    _save(fig, outdir, "fold_stability", meta, dpi)


# ==========================================================================
# (g) Qué compra y qué cuesta la calibración isotónica
# ==========================================================================
def plot_calibration_effect(
    doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int
) -> None:
    dirs = need(doc, "directions", where="isotonica")
    order = [d for d in ("long", "short") if d in dirs]
    if not order:
        raise ArtifactError("isotonica: 'directions' no trae ni 'long' ni 'short'")

    fig, raw_axes = plt.subplots(1, len(order), figsize=(12.6, 6.2))
    axes = list(raw_axes) if len(order) > 1 else [raw_axes]

    for ax, direction in zip(axes, order, strict=True):
        where = f"isotonica[{direction}]"
        ax.plot([0, 1], [0, 1], color=AXIS, lw=1.4, zorder=1)
        ax.text(
            0.985,
            0.99,
            "calibración ideal",
            color=MUTED,
            fontsize=8,
            rotation=45,
            rotation_mode="anchor",
            ha="right",
            va="bottom",
        )

        notes: list[str] = []
        for key, label, color in (
            ("uncalibrated", "sin calibrar", GAUSS_COLOR),
            ("model", "con isotónica", CQR_COLOR),
        ):
            node = need(dirs, direction, key, where=where)
            buckets = [
                b
                for b in need(node, "buckets", where=where)
                if int(need(b, "n", where=where)) >= MIN_BUCKET_N
            ]
            if not buckets:
                raise ArtifactError(f"{where}.{key}: ningun bucket llega a n>={MIN_BUCKET_N}")
            xs = [_finite(need(b, "mean_predicted", where=where), where) for b in buckets]
            ys = [_finite(need(b, "observed_rate", where=where), where) for b in buckets]
            ax.plot(xs, ys, color=color, lw=2.2, zorder=3, label=label)
            ax.scatter(xs, ys, s=90, color=color, edgecolor=SURFACE, linewidth=2.0, zorder=4)
            worst = max(_finite(need(b, "error_pp", where=where), where) for b in buckets)
            auc = _finite(need(node, "auc", where=where), f"{where}.auc")
            notes.append(f"{label}:  AUC {auc:.3f}   ·   peor bucket {worst:.1f}pp")

        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-0.02, 1.02)
        ax.set_aspect("equal")
        ax.set_xlabel("probabilidad predicha")
        ax.set_ylabel("frecuencia observada")
        ax.legend(loc="lower right", fontsize=9.5)
        ax.annotate(
            f"{direction.upper()}\n" + "\n".join(notes),
            xy=(0.5, 1),
            xycoords="axes fraction",
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            va="bottom",
            fontsize=10,
            color=INK,
            annotation_clip=False,
        )

    _header(
        axes[0],
        "Qué compra la calibración isotónica, y qué cuesta\n"
        "Arregla la fiabilidad y degrada el ranking — las dos cosas, medidas",
        "Sin calibrar, el modelo promete hasta 72% donde acierta 45%. La isotónica lo devuelve "
        "a la diagonal, que es lo que\nhace verificable la frase «cuando digo 70%, acierto 70%». "
        "El precio: siendo monótona no debería tocar el AUC, pero\naplana tramos enteros, y los "
        "empates que crea cuestan resolución de ranking. Ninguna de las dos versiones\n"
        "discrimina lo suficiente, así que el veredicto no cambia — pero el costo queda escrito.",
        pad=54,
    )
    _footer(
        axes[0],
        meta,
        f"Solo buckets con n>={MIN_BUCKET_N}, los mismos que usa el criterio de calibración.",
    )
    _save(fig, outdir, "calibration_effect", meta, dpi)


# ==========================================================================
# (h) La brecha de EV — por qué no se emite ninguna señal
# ==========================================================================
def plot_ev_gap(doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int) -> None:
    dirs = need(doc, "directions", where="brecha-ev")
    order = [d for d in ("long", "short") if d in dirs]
    if not order:
        raise ArtifactError("brecha-ev: 'directions' no trae ni 'long' ni 'short'")

    fig, ax = plt.subplots(figsize=(11.0, 3.9))
    for lane, direction in enumerate(order):
        where = f"brecha-ev[{direction}]"
        model = need(dirs, direction, "model", where=where)
        base = _finite(need(model, "base_rate", where=where), f"{where}.base_rate")
        ceiling = max(
            _finite(need(b, "mean_predicted", where=where), where)
            for b in need(model, "buckets", where=where)
            if int(need(b, "n", where=where)) >= MIN_BUCKET_N
        )
        breakeven = _finite(
            need(dirs, direction, "breakeven_prob", where=where), f"{where}.breakeven_prob"
        )
        n_signals = int(need(dirs, direction, "trading", "n_signals", where=where))

        ax.plot(
            [ceiling, breakeven],
            [lane, lane],
            color=CRITICAL,
            lw=8,
            alpha=0.18,
            zorder=2,
            solid_capstyle="butt",
        )
        ax.annotate(
            f"brecha {(breakeven - ceiling) * 100:.1f}pp — {n_signals} señales emitidas",
            ((ceiling + breakeven) / 2, lane - 0.20),
            ha="center",
            va="bottom",
            fontsize=9.5,
            color=CRITICAL,
        )

        for value, label, color, marker in (
            (base, "tasa base", MUTED, "o"),
            (ceiling, "techo del modelo", CQR_COLOR, "o"),
            (breakeven, "equilibrio de EV", INK, "D"),
        ):
            ax.scatter(
                [value],
                [lane],
                s=170,
                color=color,
                marker=marker,
                edgecolor=SURFACE,
                linewidth=2.0,
                zorder=4,
                label=label if lane == 0 else None,
            )
            ax.annotate(
                f"{value:.1%}",
                (value, lane + 0.16),
                ha="center",
                va="top",
                fontsize=9,
                color=color if color != MUTED else INK_2,
            )

    ax.set_yticks(list(range(len(order))))
    ax.set_yticklabels([d.upper() for d in order], fontsize=11)
    ax.set_ylim(len(order) - 0.35, -0.45)
    ax.set_xlim(0.38, 0.70)
    ax.xaxis.set_major_formatter(lambda v, _pos: f"{v:.0%}")
    ax.set_xlabel("probabilidad de que el setup toque TP antes que SL")
    ax.grid(axis="y", visible=False)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", ncol=3, fontsize=9.5)
    _header(
        ax,
        "Por qué no se emite ninguna señal: el techo del modelo no llega al equilibrio\n"
        "No es que el umbral esté mal puesto — es que nada lo cruza",
        "El equilibrio es la probabilidad a la que el EV neto de costos vale cero: por debajo, "
        "operar pierde plata en\nesperanza. La probabilidad más alta que el modelo llega a emitir "
        "queda por debajo, así que ningún setup es\noperable a ningún umbral que respete el EV. "
        "Bajar el umbral no crea señales buenas: crea señales de EV negativo.",
    )
    _footer(
        ax,
        meta,
        "Techo = media del bucket más alto con n>=20. El equilibrio incluye fees, slippage y "
        "funding estimado.",
    )
    _save(fig, outdir, "ev_gap", meta, dpi)


# ==========================================================================
# (i) Diseño del walk-forward purgado
# ==========================================================================
def plot_walkforward(doc: dict[str, Any], meta: dict[str, Any], outdir: Path, dpi: int) -> None:
    import datetime as dt

    by_dir = _folds_by_direction(doc)
    rows = by_dir.get("long") or next(iter(by_dir.values()))

    def iso(text: Any) -> dt.date:
        return dt.date.fromisoformat(str(text)[:10])

    # Eje en dias desde el inicio del run, no en fechas de matplotlib:
    # `matplotlib.dates` no esta tipada y el proyecto corre mypy en strict.
    # Las marcas llevan la fecha real, asi que no se pierde nada.
    origin = iso(need(doc, "date_from", where="walk-forward"))

    def day(text: Any) -> float:
        return float((iso(text) - origin).days)

    start = 0.0
    fig, ax = plt.subplots(figsize=(11.6, 5.0))

    for i, row in enumerate(rows):
        t0 = day(need(row, "test_from", where="walk-forward"))
        t1 = day(need(row, "test_to", where="walk-forward"))
        n_train = int(need(row, "n_train", where="walk-forward"))
        n_test = int(need(row, "n_test", where="walk-forward"))
        ax.barh(i, t0 - start, left=start, height=0.62, color=DIMMED, zorder=3)
        ax.barh(i, t1 - t0, left=t0, height=0.62, color=CQR_COLOR, zorder=3)
        ax.annotate(
            f"train {n_train:,}",
            ((start + t0) / 2, i),
            ha="center",
            va="center",
            fontsize=8.5,
            color=INK_2,
        )
        ax.annotate(
            f"test {n_test:,}",
            (t1, i),
            textcoords="offset points",
            xytext=(8, 0),
            va="center",
            fontsize=8.5,
            color=CQR_COLOR,
        )

    ax.set_yticks(list(range(len(rows))))
    ax.set_yticklabels([f"fold {i + 1}" for i in range(len(rows))], fontsize=10)
    ax.set_ylim(len(rows) - 0.4, -0.6)
    ticks = [0.0] + [day(need(r, "test_from", where="walk-forward")) for r in rows]
    ticks.append(day(need(rows[-1], "test_to", where="walk-forward")))
    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [(origin + dt.timedelta(days=int(t))).strftime("%Y-%m") for t in ticks],
        fontsize=8.5,
        rotation=30,
        ha="right",
    )
    ax.set_xlabel("tiempo (marcas = frontera de cada bloque de test)")
    ax.grid(axis="y", visible=False)
    ax.set_axisbelow(True)
    _header(
        ax,
        "Diseño del walk-forward: ventana expansiva, test siempre en el futuro\n"
        f"{len(rows)} folds · el train crece de "
        f"{int(need(rows[0], 'n_train', where='walk-forward')):,} a "
        f"{int(need(rows[-1], 'n_train', where='walk-forward')):,} barras · test fijo en "
        f"{int(need(rows[0], 'n_test', where='walk-forward')):,}",
        "Cada modelo se entrena solo con barras anteriores a su bloque de test, y las métricas "
        "del gate se calculan\nconcatenando los test de los seis folds: ninguna predicción "
        "reportada la vio su propio modelo al entrenar.",
    )
    _footer(
        ax,
        meta,
        "El train se dibuja hasta el inicio de su test; `n_train` es menor que las barras de ese "
        "tramo por la purga, el\nembargo y el warm-up de las ventanas largas. El artefacto no "
        "guarda el ancho de la purga, así que no se dibuja a escala.",
    )
    _save(fig, outdir, "walkforward_design", meta, dpi)


# ==========================================================================
# (j) Ablación entre variantes — CROSS-RUN
# ==========================================================================
_ABLATION_ORDER = ("price", "price+deriv", "full", "full+near")


def plot_ablation(docs: list[tuple[Path, dict[str, Any]]], outdir: Path, dpi: int) -> None:
    """Compara variantes. Exige que sean comparables, o se niega.

    Dos runs sobre muestras distintas no son una ablacion: son dos
    experimentos. Es exactamente la degradacion silenciosa que documenta el
    README —`load_series` lee lo que haya en la DB, y la DB crece sola—, asi
    que aca se verifica en vez de asumirse.
    """
    runs = []
    for path, doc in docs:
        cfg = need(doc, "config", where=f"ablacion[{path.name}]")
        runs.append(
            {
                "variant": _variant_from_config(cfg, path.stem),
                "run_id": path.stem,
                "n_bars": int(need(doc, "n_bars", where="ablacion")),
                "n_features": int(need(doc, "n_features", where="ablacion")),
                "seed": need(cfg, "seed", where="ablacion"),
                "n_splits": need(cfg, "n_splits", where="ablacion"),
                "barrier": json.dumps(need(cfg, "barrier", where="ablacion"), sort_keys=True),
                "doc": doc,
            }
        )

    for field in ("n_bars", "seed", "n_splits", "barrier"):
        values = {r[field] for r in runs}
        if len(values) > 1:
            detalle = ", ".join(f"{r['run_id']}={r[field]}" for r in runs)
            raise ArtifactError(
                f"ablacion: los runs no son comparables, difieren en '{field}' ({detalle}). "
                "Una ablacion exige la misma muestra, la misma semilla y las mismas barreras; "
                "si no, la diferencia que se ve no es la de las familias de features."
            )
    if len({r["variant"] for r in runs}) != len(runs):
        raise ArtifactError(
            "ablacion: hay dos runs de la misma variante — no hay nada que comparar"
        )

    runs.sort(
        key=lambda r: (
            _ABLATION_ORDER.index(str(r["variant"]))
            if r["variant"] in _ABLATION_ORDER
            else len(_ABLATION_ORDER)
        )
    )
    labels = [f"{r['variant']}\n{r['n_features']} features" for r in runs]

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.6))
    for ax, key, nice, threshold, fmt in (
        (axes[0], "auc", "AUC out-of-sample", GATE_MIN_AUC, "{:.3f}"),
        (axes[1], "brier_skill_score", "Brier skill score", GATE_MIN_BSS, "{:+.4f}"),
    ):
        seen: list[float] = [threshold]
        for direction in ("long", "short"):
            ys = [
                _finite(
                    need(r["doc"], "directions", direction, "model", key, where="ablacion"),
                    f"ablacion.{key}",
                )
                for r in runs
            ]
            xs = list(range(len(runs)))
            ax.plot(xs, ys, color=DIR_COLOR[direction], lw=2.2, zorder=3, label=direction)
            ax.scatter(
                xs, ys, s=120, color=DIR_COLOR[direction], edgecolor=SURFACE, lw=2.0, zorder=4
            )
            seen.extend(ys)
            for x, y in zip(xs, ys, strict=True):
                ax.annotate(
                    fmt.format(y),
                    (x, y),
                    textcoords="offset points",
                    xytext=(0, 12 if direction == "short" else -20),
                    ha="center",
                    fontsize=9,
                    color=INK_2,
                )

        span = (max(seen) - min(seen)) or 1.0
        ax.set_ylim(min(seen) - span * 0.26, max(seen) + span * 0.16)
        ax.axhline(threshold, color=CRITICAL, lw=1.4, ls=(0, (5, 4)), zorder=2)
        ax.annotate(
            f"umbral del gate ({fmt.format(threshold)})",
            (-0.42, threshold),
            fontsize=8.5,
            color=CRITICAL,
            va="bottom",
        )
        ax.set_xticks(list(range(len(runs))))
        ax.set_xticklabels(labels, fontsize=9.5)
        ax.set_xlim(-0.45, len(runs) - 0.55)
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
        ax.legend(loc="lower left", ncol=2, fontsize=9.5)

    fig.subplots_adjust(wspace=0.22)
    _header(
        axes[0],
        "Agregar derivados y libro EMPEORA la discriminación\n"
        "La hipótesis que motivó la Fase 2b queda refutada con sus propios datos",
        "La Fase 2b se hizo bajo la premisa de que el gate no pasaba discriminación por FALTA de "
        "datos de derivados y\nmicroestructura. Se consiguieron 730/730 días de los dos, y el "
        "resultado empeora — de forma monótona con el\nnúmero de features y en las dos "
        "direcciones a la vez. Misma muestra, mismos folds, misma semilla: lo único que\ncambia "
        "son las familias. La causa del fallo no era la disponibilidad de datos.",
        pad=32,
    )
    base = runs[0]["doc"]
    axes[0].annotate(
        f"{int(need(base, 'n_bars', where='ablacion')):,} velas · "
        f"{need(base, 'date_from', where='ablacion')} .. "
        f"{need(base, 'date_to', where='ablacion')} · "
        f"semilla {runs[0]['seed']} · {runs[0]['n_splits']} folds purgados\n"
        + " · ".join(str(r["run_id"]) for r in runs),
        xy=(0, 0),
        xycoords="axes fraction",
        textcoords="offset points",
        xytext=(0, -64),
        fontsize=7,
        color=MUTED,
        ha="left",
        va="top",
        annotation_clip=False,
    )
    path = outdir / "ablation_variants.png"
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"  OK  {_show(path)}")


# ==========================================================================
def _meta(doc: dict[str, Any], artifact: Path) -> dict[str, Any]:
    variant = _variant_from_config(need(doc, "config", where="cabecera"), artifact.stem)
    return {
        "symbol": need(doc, "symbol", where="cabecera"),
        "timeframe": need(doc, "timeframe", where="cabecera"),
        "date_from": need(doc, "date_from", where="cabecera"),
        "date_to": need(doc, "date_to", where="cabecera"),
        "n_bars": int(need(doc, "n_bars", where="cabecera")),
        "n_features": int(need(doc, "n_features", where="cabecera")),
        "n_splits": int(need(doc, "config", "n_splits", where="cabecera")),
        "run_id": artifact.stem,
        "variant": variant,
        # `price+deriv` en un nombre de archivo obliga a %2B en el markdown del
        # README y el link se ve roto. El guion es equivalente y legible.
        "slug": variant.replace("+", "-"),
    }


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ArtifactError(f"no existe el artefacto '{path}'")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"'{path}' no es JSON valido: {exc}") from exc
    if not isinstance(doc, dict):
        raise ArtifactError(f"'{path}' no es un objeto JSON")
    return doc


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
    parser.add_argument(
        "--ablation",
        type=Path,
        nargs="+",
        metavar="ARTEFACTO",
        help=(
            "artefactos a comparar entre si en la figura de ablacion, nombrados "
            "explicitamente. Se niega a comparar runs con distinta muestra, semilla, "
            "folds o barreras: eso no seria una ablacion."
        ),
    )
    args = parser.parse_args(argv)

    doc = _load(args.artifact)
    meta = _meta(doc, args.artifact)
    args.outdir.mkdir(parents=True, exist_ok=True)
    _style()

    print(f"\n  artefacto : {args.artifact}")
    print(
        f"  run       : {meta['symbol']} {meta['timeframe']} · variante {meta['variant']} · "
        f"{meta['n_features']} features"
    )
    print(f"  salida    : {args.outdir}\n")

    plot_reliability(doc, meta, args.outdir, args.dpi)
    plot_mincer_zarnowitz(doc, meta, args.outdir, args.dpi)
    plot_baselines(doc, meta, args.outdir, args.dpi)
    plot_coverage(doc, meta, args.outdir, args.dpi)
    plot_importance(doc, meta, args.outdir, args.dpi)
    plot_fold_stability(doc, meta, args.outdir, args.dpi)
    plot_calibration_effect(doc, meta, args.outdir, args.dpi)
    plot_ev_gap(doc, meta, args.outdir, args.dpi)
    plot_walkforward(doc, meta, args.outdir, args.dpi)
    plot_baselines_interactive(doc, meta, args.outdir, args.plotly_js)

    n_png = 9
    if args.ablation:
        print()
        plot_ablation([(p, _load(p)) for p in args.ablation], args.outdir, args.dpi)
        n_png += 1

    print(f"\n  listo — {n_png} PNG + 1 HTML interactivo\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
