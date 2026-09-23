"""Features y target transversales del panel — PUROS, sin I/O.

**Por qué existe este módulo, en una medición.** Sobre 238 perpetuos cripto ×
2.456 fechas, la correlación media entre pares de retornos diarios crudos es
**+0,422**: PC1 explica el 48,4% de la varianza y el panel entero equivale a
**2,4 símbolos independientes**. Preguntarle a 238 símbolos si suben es casi
preguntárselo a dos, y el IC95% del AUC queda en ±0,033 — peor que el gate de
15m, con 70 veces más filas.

Demediando por fecha, esa correlación cae a −0,007, PC1 a 6,8%, y quedan 238
observaciones genuinamente distintas por día (IC ±0,004). O sea: **el panel
solo paga si la pregunta es relativa.** Todo lo de acá abajo existe para
formular la pregunta en esos términos y no en los otros.

Convención de ejes en todo el módulo: `(S, T)` es símbolos × fechas, y las
matrices de features son `(S, T, F)`. NaN significa "no hay dato", nunca cero.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from bob.signals import numeric as nm

__all__ = [
    "PanelFeatures",
    "market_factor",
    "rolling_beta",
    "cross_sectional_rank",
    "panel_log_returns",
    "forward_path_return",
    "relative_target",
]


@dataclass(frozen=True)
class PanelFeatures:
    """Cubo de features del panel, alineado con `PanelData`.

    `X[i, j, f]` es el feature `names[f]` del símbolo `symbols[i]` observable
    **al cierre de la fecha `open_time[j]`**.
    """

    X: np.ndarray  # (S, T, F) float64
    names: list[str]
    symbols: tuple[str, ...]
    open_time: np.ndarray  # (T,) int64
    valid: np.ndarray  # (S, T) bool — todas las features finitas

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.X.shape[0], self.X.shape[1], self.X.shape[2])

    def index_of(self, name: str) -> int:
        return self.names.index(name)


def panel_log_returns(close: np.ndarray) -> np.ndarray:
    """Retornos log barra a barra, fila por fila. NaN en la primera columna.

    Un hueco de datos produce NaN y **no** se puentea: saltearlo uniría el
    precio de antes del hueco con el de después y fabricaría un retorno que
    nadie pudo operar.
    """
    close = np.asarray(close, dtype=np.float64)
    out = np.full_like(close, np.nan)
    prev, cur = close[:, :-1], close[:, 1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:, 1:] = np.log(cur / prev)
    return np.where(np.isfinite(out), out, np.nan)


def market_factor(returns: np.ndarray, eligible: np.ndarray | None = None) -> np.ndarray:
    """El factor común de cada fecha: la **mediana** transversal de los retornos.

    Mediana y no media ponderada por capitalización: el panel no tiene
    capitalización confiable para símbolos deslistados, y una media simple la
    domina cualquier monedita que hizo +300% ese día. La mediana es el "qué
    hizo el mercado" robusto que después se resta.
    """
    values = np.asarray(returns, dtype=np.float64)
    if eligible is not None:
        values = np.where(eligible, values, np.nan)
    n_dates = values.shape[1]
    out = np.full(n_dates, np.nan, dtype=np.float64)
    usable = np.sum(np.isfinite(values), axis=0) > 0
    if np.any(usable):
        out[usable] = np.nanmedian(values[:, usable], axis=0)
    return out


def rolling_beta(returns: np.ndarray, factor: np.ndarray, window: int) -> np.ndarray:
    """Beta rodante de cada símbolo contra el factor, con ventana causal.

    Entra como feature **explícito** por una razón de atribución, no de poder
    predictivo (§6.4 del pre-registro): demediar por fecha quita el factor
    común pero no los betas, así que en un día de caída general los símbolos
    de beta alto quedan bajo la mediana por construcción. Si el modelo
    aprende beta, está prediciendo el signo del mercado disfrazado de
    transversal — y con el beta a la vista eso es auditable en vez de
    invisible.
    """
    if window < 2:
        raise ValueError("window debe ser >= 2")
    r = np.asarray(returns, dtype=np.float64)
    f = np.asarray(factor, dtype=np.float64)
    n_sym, n_dates = r.shape
    out = np.full((n_sym, n_dates), np.nan, dtype=np.float64)

    for j in range(window, n_dates):
        lo = j - window
        rw = r[:, lo:j]  # estrictamente anterior a j
        fw = f[lo:j]
        ok_f = np.isfinite(fw)
        if ok_f.sum() < window // 2:
            continue
        fv = np.where(ok_f, fw, np.nan)
        f_mean = np.nanmean(fv)
        f_dev = fv - f_mean
        var = np.nansum(f_dev * f_dev)
        if var <= nm.EPS:
            continue
        ok_r = np.isfinite(rw) & ok_f[None, :]
        enough = ok_r.sum(axis=1) >= max(2, window // 2)
        rv = np.where(ok_r, rw, np.nan)
        # Una fila sin ningún dato en la ventana da "Mean of empty slice": es
        # el caso normal de un símbolo que todavía no listaba, y `enough` ya
        # la descarta más abajo. Se silencia el aviso, no el caso.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            r_mean = np.nanmean(rv, axis=1)
        cov = np.nansum((rv - r_mean[:, None]) * f_dev[None, :], axis=1)
        beta = cov / var
        out[enough, j] = beta[enough]
    return out


def cross_sectional_rank(
    values: np.ndarray, eligible: np.ndarray | None = None
) -> np.ndarray:
    """Rango percentil dentro de la sección transversal de cada fecha, en [0, 1].

    Es la única información que el modelo de 15m no podía tener, y por lo
    tanto la única capaz de explicar un resultado distinto del suyo.

    Usa **solo datos de la misma fecha**, de otros símbolos. Eso no es
    lookahead: el rango de hoy contra los demás de hoy es observable hoy. Lo
    que sí sería lookahead es rankear contra la distribución de todo el
    período, y por eso el rango se recomputa fecha por fecha.

    Con `k` valores válidos el rango va de `1/(k+1)` a `k/(k+1)`, nunca 0 ni
    1: los extremos abiertos evitan que el corte de deciles dependa de si hay
    empates en la punta.
    """
    v = np.asarray(values, dtype=np.float64)
    if eligible is not None:
        v = np.where(eligible, v, np.nan)
    n_sym, n_dates = v.shape
    out = np.full((n_sym, n_dates), np.nan, dtype=np.float64)

    for j in range(n_dates):
        col = v[:, j]
        ok = np.isfinite(col)
        k = int(ok.sum())
        if k < 2:
            continue
        vals = col[ok]
        # Rango promedio para empates: dos símbolos con el mismo valor deben
        # recibir el mismo rango, o el orden lo decidiría el índice del array.
        order = np.argsort(vals, kind="stable")
        ranks = np.empty(k, dtype=np.float64)
        ranks[order] = np.arange(1, k + 1, dtype=np.float64)
        sorted_vals = vals[order]
        start = 0
        for end in range(1, k + 1):
            if end == k or sorted_vals[end] != sorted_vals[start]:
                if end - start > 1:
                    ranks[order[start:end]] = np.mean(ranks[order[start:end]])
                start = end
        out[ok, j] = ranks / (k + 1.0)
    return out


def forward_path_return(
    open_: np.ndarray, close: np.ndarray, horizon: int
) -> tuple[np.ndarray, np.ndarray]:
    """Retorno log de entrar al `open` de `t+1` y salir al cierre de `t+H`.

    Devuelve `(retorno, deslistado)`. Si el símbolo deja de cotizar dentro del
    horizonte, se sale al **último cierre disponible** y la máscara
    `deslistado` lo marca.

    Esa salida no es un detalle de implementación: **descartar esos casos
    reintroduciría el sesgo de supervivencia** que todo el diseño existe para
    evitar. Lo que se muere es justamente lo que cae, y borrarlo del target
    convierte una pérdida real en un dato faltante.
    """
    if horizon < 1:
        raise ValueError("horizon debe ser >= 1")
    open_ = np.asarray(open_, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n_sym, n_dates = close.shape
    ret = np.full((n_sym, n_dates), np.nan, dtype=np.float64)
    delisted = np.zeros((n_sym, n_dates), dtype=bool)

    last_valid = n_dates - horizon - 1
    for j in range(last_valid + 1):
        entry = open_[:, j + 1]
        window = close[:, j + 1 : j + horizon + 1]
        observed = np.isfinite(window)
        has_any = observed.any(axis=1)
        # índice de la última observación del horizonte, por símbolo
        last_idx = observed.shape[1] - 1 - np.argmax(observed[:, ::-1], axis=1)
        exit_px = np.full(n_sym, np.nan)
        rows = np.flatnonzero(has_any)
        exit_px[rows] = window[rows, last_idx[rows]]
        ok = np.isfinite(entry) & np.isfinite(exit_px) & (entry > 0) & (exit_px > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ret[ok, j] = np.log(exit_px[ok] / entry[ok])
        delisted[:, j] = has_any & (last_idx < horizon - 1)
    return ret, delisted


def panel_uniqueness_weights(
    sym_idx: np.ndarray, date_idx: np.ndarray, horizon: int, n_dates: int
) -> np.ndarray:
    """Peso por unicidad (López de Prado, cap. 4) aplicado **símbolo a símbolo**.

    El label de la fecha `j` ocupa `j+1 .. j+H`, así que dos fechas
    consecutivas del mismo símbolo comparten `H−1` de sus `H` barras de
    futuro. Tratarlas como independientes le da al modelo la ilusión de tener
    H veces más datos.

    Se calcula **por símbolo y no sobre el panel entero**: la superposición
    que hay que descontar es la temporal, dentro de una serie. Contar la
    concurrencia mezclando símbolos mediría otra cosa —cuántos símbolos
    cotizan ese día— y penalizaría las fechas con sección ancha, que es
    justo donde el ranking transversal tiene más información.

    Aviso honesto sobre lo que este peso puede y no puede hacer acá: con un
    horizonte **fijo**, la concurrencia es `H` en casi todas las fechas y el
    peso sale prácticamente constante. Un peso constante no cambia el ajuste
    de un boosting. La variación real está solo en los bordes de la vida de
    cada símbolo —los primeros y últimos `H` días— así que el efecto esperado
    es chico por construcción, no por casualidad. Se mide igual porque estaba
    declarado, y el resultado se reporta tal cual salga.
    """
    from bob.models.labeling import uniqueness_weights as _uniqueness

    sym_idx = np.asarray(sym_idx, dtype=np.int64)
    date_idx = np.asarray(date_idx, dtype=np.int64)
    out = np.ones(sym_idx.shape[0], dtype=np.float64)

    for sym in np.unique(sym_idx):
        rows = np.flatnonzero(sym_idx == sym)
        if rows.size < 2:
            continue
        dates = date_idx[rows]
        # Índices locales densos: `uniqueness_weights` razona sobre posiciones
        # consecutivas de UNA serie, no sobre el eje global del panel.
        order = np.argsort(dates, kind="stable")
        local = np.empty(rows.size, dtype=np.int64)
        local[order] = np.arange(rows.size)
        # El eje local se extiende `horizon` posiciones más allá del final. Sin
        # eso, la ÚLTIMA fila de cada símbolo tiene su ventana de ocupación
        # recortada a longitud cero y sale con peso **0**: una fila legítima
        # desaparecería del entrenamiento sin que nada fallara. Recortar el
        # span a `n-1` —lo obvio— produce exactamente ese cero.
        span = local + horizon
        w = _uniqueness(span, rows.size + horizon)
        out[rows] = w[local]

    total = out.sum()
    if total > 0:
        out *= out.size / total  # media 1: no cambia la escala del problema
    return out


def relative_target(
    forward: np.ndarray,
    eligible: np.ndarray | None = None,
    *,
    cut: str = "median",
) -> tuple[np.ndarray, np.ndarray]:
    """Target binario relativo a la sección transversal de cada fecha.

    Devuelve `(y, excess)` — la etiqueta 0/1 y el exceso continuo sobre la
    mediana, que es lo que después mide el spread del par. El exceso se
    calcula siempre contra la **mediana**, sin importar el corte: es la
    magnitud que el par largo/corto realiza, y cambiarla con el corte haría
    incomparables los spreads entre variantes.

    - `cut="median"` (primaria): `y=1` si supera la mediana. Tasa base
      exactamente 50% por construcción, así que AUC y BSS quedan
      directamente comparables con los del gate de 15m.
    - `cut="tercile"` (exploratoria declarada): `y=1` en el tercil superior,
      `y=0` en el inferior, **sin etiqueta** en el medio. Descarta un tercio
      de las filas a cambio de un target menos ambiguo — la fila que cae
      justo al lado de la mediana es ruido etiquetado como señal.

    En los dos casos el factor común de la fecha se cancela: esto mide
    *orden relativo*, que es la única pregunta que el panel tiene potencia
    para responder.

    Las fechas con menos de 3 símbolos elegibles quedan sin etiqueta: una
    "mediana" de dos observaciones no define arriba y abajo de nada.
    """
    if cut not in ("median", "tercile"):
        raise ValueError(f"cut debe ser 'median' o 'tercile', no {cut!r}")
    f = np.asarray(forward, dtype=np.float64)
    if eligible is not None:
        f = np.where(eligible, f, np.nan)
    n_sym, n_dates = f.shape
    y = np.full((n_sym, n_dates), -1, dtype=np.int8)
    excess = np.full((n_sym, n_dates), np.nan, dtype=np.float64)

    counts = np.sum(np.isfinite(f), axis=0)
    usable = counts >= 3
    if not np.any(usable):
        return y, excess
    med = np.full(n_dates, np.nan)
    med[usable] = np.nanmedian(f[:, usable], axis=0)
    dev = f - med[None, :]
    ok = np.isfinite(dev) & usable[None, :]
    excess[ok] = dev[ok]

    if cut == "median":
        y[ok] = (dev[ok] > 0.0).astype(np.int8)
        return y, excess

    # Tercil: hace falta el corte propio de cada fecha, no el global.
    lo_q = np.full(n_dates, np.nan)
    hi_q = np.full(n_dates, np.nan)
    lo_q[usable] = np.nanpercentile(f[:, usable], 100.0 / 3.0, axis=0)
    hi_q[usable] = np.nanpercentile(f[:, usable], 200.0 / 3.0, axis=0)
    top = ok & (f > hi_q[None, :])
    bottom = ok & (f < lo_q[None, :])
    y[top] = 1
    y[bottom] = 0
    return y, excess
