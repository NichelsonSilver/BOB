"""HMM gaussiano de regímenes de mercado — n estados elegido por BIC.

Módulo PURO (regla 3): entra una matriz de observaciones, salen parámetros y
probabilidades. Sin I/O, sin saber qué símbolo es.

**Por qué está escrito a mano y no con `hmmlearn`.** CLAUDE.md permite la
librería, y aun así este módulo la implementa en numpy por dos razones:

1. `hmmlearn` no publica wheel para el Python 3.14 de este entorno y exige
   compilar con MSVC. Bajar el intérprete de todo el proyecto por una
   dependencia es un precio alto.
2. Más de fondo: **su inferencia no sirve para features**. `predict` es Viterbi
   sobre la secuencia completa y `predict_proba` es el posterior suavizado —
   los dos miran el futuro de cada barra. Usarlos para alimentar el KPI 1 es
   exactamente el lookahead que la regla 5 prohíbe, y el bug sería invisible:
   el backtest daría métricas hermosas e irreproducibles en vivo. El filtro
   causal (`filtered_probs`) había que escribirlo igual; lo que agregaba la
   librería era solo el Baum-Welch, que son ~80 líneas de numpy.

Distinción central del módulo, entonces:

  `filtered_probs`  — P(estado_t | observaciones hasta t). CAUSAL. Es lo único
                      que puede entrar como feature o mostrarse en vivo.
  `smoothed_probs`  — P(estado_t | TODAS las observaciones). Mira el futuro.
                      Sirve para análisis histórico y para el EM; jamás como
                      feature.

Las recursiones usan el escalado clásico de Rabiner (normalizar en cada paso)
y no espacio logarítmico: con 68k barras y 100 iteraciones de EM, un
`logsumexp` por paso multiplica el tiempo de ajuste por ~10 sin ganar
estabilidad. La única emisión que podría underflowear se protege restando su
máximo por fila, constante que se cancela y se devuelve en el log-lik.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from sklearn.cluster import KMeans

from bob.models.markov import MarketRegime
from bob.signals.numeric import log_returns, realized_vol

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

#: Piso de varianza. Sin él, un estado que colapsa sobre pocas observaciones
#: casi idénticas manda su varianza a cero y la verosimilitud a infinito: el
#: EM "gana" degenerando en vez de aprendiendo.
DEFAULT_REG_COVAR: Final = 1e-8

#: Candidatos por defecto para la búsqueda por BIC. CLAUDE.md espera 3-5 en la
#: práctica; se prueba 2-6 para que el óptimo no quede pegado al borde.
DEFAULT_N_CANDIDATES: Final = (2, 3, 4, 5, 6)

#: |media de retorno| / volatilidad del estado a partir del cual la etiqueta
#: dice "trending". Es cosmética: el modelo usa probabilidades, no etiquetas.
TREND_RATIO_THRESHOLD: Final = 0.05

#: Cuánto más volátil que la mediana de los estados para llamarlo "volatile".
VOLATILE_RATIO_THRESHOLD: Final = 1.5

#: Cuando el criterio de selección es monótono (no hay codo), se corta en el
#: primer n cuya mejora marginal cae bajo esta fracción de la primera mejora.
#: Es una regla de parsimonia declarada, no un óptimo: sirve para no dejar que
#: el borde del rango probado decida por defecto.
KNEE_IMPROVEMENT_RATIO: Final = 0.5


def regime_observations(close: FloatArray, vol_window: int = 96) -> FloatArray:
    """Observaciones del HMM: retorno log y log-volatilidad realizada.

    Las dos columnas son **adimensionales**: multiplicar todos los precios por
    10 no cambia una sola fila (mismo invariante que testea el feature engine).
    Eso es lo que permite entrenar un modelo por símbolo sin reescalar nada.

    La volatilidad va en logaritmo porque su distribución es aproximadamente
    log-normal; en niveles, un puñado de barras de pánico domina las medias
    gaussianas del HMM y se lleva un estado entero para sí.

    Las filas de warm-up salen NaN — se descartan, no se rellenan (regla 5:
    inventar el pasado es tan grave como mirar el futuro).
    """
    close = np.asarray(close, dtype=np.float64)
    if close.ndim != 1:
        raise ValueError("close debe ser un vector 1-D")
    rets = log_returns(close)
    # Por barra, igual que `labeling.target_volatility`: `realized_vol` devuelve
    # la volatilidad de la ventana completa. Si las dos partes del sistema
    # miden sigma en unidades distintas, comparar sus números es un error
    # silencioso esperando a pasar.
    per_bar = realized_vol(rets, vol_window) / np.sqrt(vol_window)
    with np.errstate(divide="ignore", invalid="ignore"):
        log_vol = np.where(per_bar > 0, np.log(np.maximum(per_bar, 1e-12)), np.nan)
    return np.column_stack([rets, log_vol])


def finite_rows(X: FloatArray) -> npt.NDArray[np.bool_]:
    """Máscara de filas usables (sin NaN ni inf) — el warm-up queda afuera."""
    return np.all(np.isfinite(np.asarray(X, dtype=np.float64)), axis=1)


def _log_gaussian_diag(X: FloatArray, means: FloatArray, covars: FloatArray) -> FloatArray:
    """log N(x | mu, diag(sigma²)) para cada observación y estado → (n, k)."""
    n_features = X.shape[1]
    # (n, k, d) sería lo directo pero explota en memoria con 70k barras:
    # se expande término a término, que da lo mismo y no asigna nada grande.
    log_det = np.sum(np.log(covars), axis=1)  # (k,)
    # (x − mu)²/sigma² expandido: x²/sigma² − 2·x·mu/sigma² + mu²/sigma².
    # Los tres términos salen de productos matriciales (n,d)·(d,k) — sin
    # materializar el tensor (n, k, d), que con 68k barras no entra en memoria.
    quad = (
        (X**2) @ (1.0 / covars).T
        - 2.0 * (X @ (means / covars).T)
        + np.sum(means**2 / covars, axis=1)[None, :]
    )
    out = -0.5 * (n_features * np.log(2.0 * np.pi) + log_det[None, :] + quad)
    return np.asarray(out, dtype=np.float64)


@dataclass
class GaussianHMM:
    """HMM gaussiano con covarianzas diagonales, ajustado por Baum-Welch.

    Diagonal y no completa a propósito: con 2 features y estados que pueden
    quedarse con pocas observaciones, una covarianza completa tiene el doble de
    parámetros y se vuelve singular apenas un estado se estrecha. La correlación
    entre retorno y volatilidad la captura la *mezcla* de estados, que es
    justamente lo que un HMM hace bien.
    """

    n_states: int = 3
    n_iter: int = 100
    tol: float = 1e-4
    reg_covar: float = DEFAULT_REG_COVAR
    seed: int = 42

    startprob_: FloatArray = field(default_factory=lambda: np.empty(0), repr=False)
    transmat_: FloatArray = field(default_factory=lambda: np.empty((0, 0)), repr=False)
    means_: FloatArray = field(default_factory=lambda: np.empty((0, 0)), repr=False)
    covars_: FloatArray = field(default_factory=lambda: np.empty((0, 0)), repr=False)
    converged_: bool = False
    n_iter_run_: int = 0
    log_likelihood_: float = float("-inf")
    log_likelihood_history_: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.n_states < 1:
            raise ValueError("n_states debe ser >= 1")

    # ------------------------------------------------------------------ #
    # Ajuste
    # ------------------------------------------------------------------ #

    def fit(self, X: FloatArray) -> GaussianHMM:
        """Baum-Welch sobre `X` (n, d). Solo filas finitas y en orden temporal."""
        X = self._validate(X, fitting=True)
        self._init_params(X)

        history: list[float] = []
        prev = float("-inf")
        for iteration in range(1, self.n_iter + 1):
            b, offset = self._scaled_emissions(X)
            alpha, scales, loglik = self._forward(b, offset)
            beta = self._backward(b, scales)

            gamma = self._posteriors(alpha, beta)
            xi = self._xi_sums(alpha, beta, b, scales)

            self._m_step(X, gamma, xi)
            history.append(loglik)
            self.n_iter_run_ = iteration
            if loglik - prev < self.tol and iteration > 1:
                self.converged_ = True
                break
            prev = loglik

        self.log_likelihood_history_ = tuple(history)
        # La verosimilitud final se mide con los parámetros finales, no con los
        # de la iteración previa: si no, `bic` compara manzanas con peras.
        self.log_likelihood_ = self.log_likelihood(X)
        return self

    def _init_params(self, X: FloatArray) -> None:
        """Inicialización por k-means: determinista y mucho mejor que al azar.

        El EM converge a un óptimo local; arrancar de centroides razonables es
        la diferencia entre estados interpretables y estados que se reparten
        el ruido.
        """
        k = self.n_states
        n_features = X.shape[1]
        if k == 1:
            labels = np.zeros(X.shape[0], dtype=np.int64)
        else:
            km = KMeans(n_clusters=k, n_init=10, random_state=self.seed)
            labels = km.fit_predict(X).astype(np.int64)

        means = np.zeros((k, n_features), dtype=np.float64)
        covars = np.ones((k, n_features), dtype=np.float64)
        global_var = np.maximum(np.var(X, axis=0), self.reg_covar)
        for state in range(k):
            rows = X[labels == state]
            if rows.shape[0] < 2:  # pragma: no cover — k-means rara vez deja vacío
                means[state] = X.mean(axis=0)
                covars[state] = global_var
                continue
            means[state] = rows.mean(axis=0)
            covars[state] = np.maximum(rows.var(axis=0), self.reg_covar)

        # Orden estable por la media de la primera columna (retorno): dos
        # corridas con la misma semilla dan los mismos estados en el mismo
        # orden, que es lo que hace reproducible al feature aguas abajo.
        order = np.argsort(means[:, 0])
        self.means_ = means[order]
        self.covars_ = covars[order]
        self.startprob_ = np.full(k, 1.0 / k)
        # Transiciones iniciales pegajosas: los regímenes de mercado persisten,
        # y arrancar de una matriz uniforme empuja al EM a estados que duran
        # una barra.
        self.transmat_ = np.full((k, k), 0.1 / max(k - 1, 1))
        np.fill_diagonal(self.transmat_, 0.9 if k > 1 else 1.0)
        self.transmat_ /= self.transmat_.sum(axis=1, keepdims=True)
        self.converged_ = False
        self.n_iter_run_ = 0

    # ------------------------------------------------------------------ #
    # Recursiones (forward-backward con escalado de Rabiner)
    # ------------------------------------------------------------------ #
    #
    # Se trabaja escalado en vez de en espacio log por costo: con 68k barras y
    # 100 iteraciones de EM, un `logsumexp` por paso multiplica el tiempo por
    # ~10 y el ajuste pasa de segundos a minutos. El escalado normaliza en cada
    # paso, así que no hay underflow, y la verosimilitud sale exacta como la
    # suma de los logaritmos de los factores de escala.
    #
    # El único punto delicado es la emisión: `exp(log_b)` sí puede underflowear
    # ante un outlier. Por eso se le resta el máximo por fila — una constante
    # que se cancela en todas las razones y se devuelve sumada al log-lik.

    def _scaled_emissions(self, X: FloatArray) -> tuple[FloatArray, FloatArray]:
        log_b = _log_gaussian_diag(X, self.means_, self.covars_)
        offset = np.max(log_b, axis=1)
        return np.exp(log_b - offset[:, None]), offset

    def _forward(self, b: FloatArray, offset: FloatArray) -> tuple[FloatArray, FloatArray, float]:
        """Devuelve (alpha normalizado = probabilidades filtradas, escalas, logL)."""
        n, k = b.shape
        alpha = np.empty((n, k), dtype=np.float64)
        scales = np.empty(n, dtype=np.float64)

        u = self.startprob_ * b[0]
        scales[0] = max(u.sum(), 1e-300)
        alpha[0] = u / scales[0]
        trans = self.transmat_
        for t in range(1, n):
            u = (alpha[t - 1] @ trans) * b[t]
            scales[t] = max(u.sum(), 1e-300)
            alpha[t] = u / scales[t]

        loglik = float(np.sum(np.log(scales)) + np.sum(offset))
        return alpha, scales, loglik

    def _backward(self, b: FloatArray, scales: FloatArray) -> FloatArray:
        n, k = b.shape
        beta = np.ones((n, k), dtype=np.float64)
        trans = self.transmat_
        for t in range(n - 2, -1, -1):
            beta[t] = (trans @ (b[t + 1] * beta[t + 1])) / scales[t + 1]
        return beta

    @staticmethod
    def _posteriors(alpha: FloatArray, beta: FloatArray) -> FloatArray:
        gamma = alpha * beta
        total = gamma.sum(axis=1, keepdims=True)
        return gamma / np.maximum(total, 1e-300)

    def _xi_sums(
        self, alpha: FloatArray, beta: FloatArray, b: FloatArray, scales: FloatArray
    ) -> FloatArray:
        """Σ_t P(estado_t = i, estado_{t+1} = j | X), acumulado sobre t.

        Sale de un solo producto matricial: acumular en un loop de Python sobre
        68k pasos costaba más que todo el resto del EM junto.
        """
        n = b.shape[0]
        if n < 2:
            return np.ones((self.n_states, self.n_states))
        rhs = b[1:] * beta[1:] / scales[1:, None]  # (n-1, k)
        return self.transmat_ * (alpha[:-1].T @ rhs)

    def _m_step(self, X: FloatArray, gamma: FloatArray, xi: FloatArray) -> None:
        self.startprob_ = gamma[0] / max(gamma[0].sum(), 1e-300)
        row_sums = xi.sum(axis=1, keepdims=True)
        self.transmat_ = np.where(row_sums > 0, xi / np.maximum(row_sums, 1e-300), self.transmat_)

        weights = gamma.sum(axis=0)  # (k,)
        safe_w = np.maximum(weights, 1e-300)[:, None]
        means = (gamma.T @ X) / safe_w
        diff2 = np.empty_like(means)
        for state in range(self.n_states):
            delta = X - means[state]
            diff2[state] = (gamma[:, state][:, None] * delta**2).sum(axis=0)
        covars = np.maximum(diff2 / safe_w, self.reg_covar)
        self.means_ = means
        self.covars_ = covars

    # ------------------------------------------------------------------ #
    # Inferencia
    # ------------------------------------------------------------------ #

    def log_likelihood(self, X: FloatArray) -> float:
        X = self._validate(X)
        b, offset = self._scaled_emissions(X)
        _, _, loglik = self._forward(b, offset)
        return loglik

    def filtered_probs(self, X: FloatArray) -> FloatArray:
        """P(estado_t | x_0..x_t) — **causal**, lo único apto como feature.

        Cada fila usa exclusivamente su pasado. Si `X` empieza en medio de la
        serie, las primeras filas arrastran la incertidumbre del arranque
        (la distribución inicial): converge en pocas barras, pero conviene
        darle warm-up antes de leer el estado como señal.
        """
        X = self._validate(X)
        b, offset = self._scaled_emissions(X)
        alpha, _, _ = self._forward(b, offset)
        # `alpha` ya viene normalizado fila a fila por el escalado: es
        # literalmente P(estado_t | x_0..x_t).
        return alpha

    def smoothed_probs(self, X: FloatArray) -> FloatArray:
        """P(estado_t | TODA la serie). **Mira el futuro** — nunca como feature.

        Es el posterior que usa el EM y el que sirve para contar, a posteriori,
        en qué régimen estuvo el mercado. Si aparece alimentando una señal, es
        un bug crítico (regla 5).
        """
        X = self._validate(X)
        b, offset = self._scaled_emissions(X)
        alpha, scales, _ = self._forward(b, offset)
        beta = self._backward(b, scales)
        return self._posteriors(alpha, beta)

    def filtered_states(self, X: FloatArray) -> IntArray:
        """Estado más probable en cada barra, usando solo el pasado."""
        return np.asarray(np.argmax(self.filtered_probs(X), axis=1), dtype=np.int64)

    # ------------------------------------------------------------------ #
    # Diagnóstico y selección
    # ------------------------------------------------------------------ #

    @property
    def n_parameters(self) -> int:
        """Parámetros libres: inicial (k−1) + transiciones k(k−1) + medias y
        varianzas (2·k·d)."""
        k = self.n_states
        d = self.means_.shape[1] if self.means_.size else 0
        return (k - 1) + k * (k - 1) + 2 * k * d

    def bic(self, X: FloatArray) -> float:
        """BIC = −2·logL + p·log(n). Menor es mejor.

        Penaliza parámetros con más dureza que AIC, que es lo que se quiere
        acá: un HMM con demasiados estados ajusta el ruido de un régimen
        particular y no sobrevive al cambio de mercado.

        **Advertencia medida, no teórica** (ver docs/PROBABILITY_MODEL.md): con
        69k barras de ETHUSDT el BIC baja de forma monótona hasta el borde de
        los candidatos. Con esa muestra la penalización (p·log n ≈ 700) es
        ruido frente a ganancias de verosimilitud de decenas de miles, y como
        la densidad real no es una mezcla de k gaussianas, agregar estados
        siempre mejora el ajuste: el HMM degenera en un cuantizador de
        volatilidad con seis estados "lateral" que solo difieren en sigma. Por
        eso `select_n_states` reporta también el ICL.
        """
        X = self._validate(X)
        n = X.shape[0]
        return -2.0 * self.log_likelihood(X) + self.n_parameters * float(np.log(n))

    def posterior_entropy(self, X: FloatArray) -> float:
        """Entropía total de los posteriores: cuánto se solapan los estados.

        Cero significa que cada barra pertenece sin ambigüedad a un estado.
        Usa el posterior suavizado a propósito — es una medida de ajuste sobre
        la ventana de entrenamiento, no un feature.
        """
        gamma = self.smoothed_probs(X)
        with np.errstate(divide="ignore", invalid="ignore"):
            terms = np.where(gamma > 0, gamma * np.log(gamma), 0.0)
        return float(-np.sum(terms))

    def icl(self, X: FloatArray) -> float:
        """ICL = BIC + 2·entropía de los posteriores. Menor es mejor.

        Biernacki, Celeux & Govaert (2000). Donde el BIC premia el ajuste, el
        ICL premia que los estados se **distingan**: un estado que solo divide
        en dos una nube ya existente sube la entropía y no paga. Es el criterio
        adecuado cuando lo que se busca no es la mejor densidad sino regímenes
        interpretables, que es exactamente el caso del KPI 3.
        """
        return self.bic(X) + 2.0 * self.posterior_entropy(X)

    def expected_durations(self) -> FloatArray:
        """E[permanencia] = 1 / (1 − p_ii), en barras. Alimenta el KPI 3.

        Es una media de una geométrica: tiene varianza enorme y por eso el KPI 3
        se muestra como rango, no como número (CLAUDE.md lo dice explícito).
        """
        self._require_fit()
        diag = np.clip(np.diag(self.transmat_), 0.0, 1.0 - 1e-12)
        return 1.0 / (1.0 - diag)

    def stationary_distribution(self) -> FloatArray:
        """Distribución de largo plazo: el autovector izquierdo de valor 1.

        Cuánto tiempo pasa el mercado en cada régimen si la matriz no cambia.
        """
        self._require_fit()
        values, vectors = np.linalg.eig(self.transmat_.T)
        idx = int(np.argmin(np.abs(values - 1.0)))
        vec = np.real(vectors[:, idx])
        vec = np.abs(vec)
        total = vec.sum()
        if total <= 0:  # pragma: no cover — no ocurre con una matriz estocástica
            return np.full(self.n_states, 1.0 / self.n_states)
        return vec / total

    def regime_labels(self) -> tuple[MarketRegime, ...]:
        """Etiqueta legible de cada estado. Es **para el humano**.

        El modelo consume probabilidades de estado, no nombres: dos estados
        pueden recibir la misma etiqueta y no pasa nada — significan "el
        mercado tiene dos formas distintas de estar lateral", que es
        información, no un error.
        """
        self._require_fit()
        mean_ret = self.means_[:, 0]
        vol = np.exp(self.means_[:, 1]) if self.means_.shape[1] > 1 else np.ones(self.n_states)
        vol_ref = float(np.median(vol))

        labels: list[MarketRegime] = []
        for state in range(self.n_states):
            v = float(vol[state])
            ratio = float(mean_ret[state]) / v if v > 0 else 0.0
            if vol_ref > 0 and v >= VOLATILE_RATIO_THRESHOLD * vol_ref:
                labels.append(MarketRegime.VOLATILE)
            elif ratio >= TREND_RATIO_THRESHOLD:
                labels.append(MarketRegime.TRENDING_UP)
            elif ratio <= -TREND_RATIO_THRESHOLD:
                labels.append(MarketRegime.TRENDING_DOWN)
            else:
                labels.append(MarketRegime.RANGING)
        return tuple(labels)

    def summary(self) -> dict[str, Any]:
        """Estado del modelo para la página Analysis y para el log de la señal."""
        self._require_fit()
        return {
            "n_states": self.n_states,
            "converged": self.converged_,
            "iterations": self.n_iter_run_,
            "log_likelihood": self.log_likelihood_,
            "labels": [str(label) for label in self.regime_labels()],
            "means": self.means_.tolist(),
            "std_devs": np.sqrt(self.covars_).tolist(),
            "transition_matrix": self.transmat_.tolist(),
            "expected_durations_bars": self.expected_durations().tolist(),
            "stationary_distribution": self.stationary_distribution().tolist(),
        }

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #

    def _require_fit(self) -> None:
        if self.means_.size == 0:
            raise RuntimeError("el HMM no está ajustado")

    def _validate(self, X: FloatArray, *, fitting: bool = False) -> FloatArray:
        arr = np.asarray(X, dtype=np.float64)
        if arr.ndim != 2:
            raise ValueError("X debe ser una matriz (n_observaciones, n_features)")
        if not np.all(np.isfinite(arr)):
            raise ValueError(
                "X tiene NaN o inf: descartar el warm-up con finite_rows() antes de ajustar"
            )
        if fitting:
            if arr.shape[0] <= self.n_states:
                raise ValueError(
                    f"{arr.shape[0]} observaciones no alcanzan para {self.n_states} estados"
                )
        else:
            self._require_fit()
            if arr.shape[1] != self.means_.shape[1]:
                raise ValueError(
                    f"X tiene {arr.shape[1]} features y el modelo se ajustó con "
                    f"{self.means_.shape[1]}"
                )
        return arr


@dataclass(frozen=True)
class StateSelection:
    """Resultado de la búsqueda de n estados. Se muestra completo en Settings.

    CLAUDE.md pide que el usuario pueda fijar n a mano **de forma informada**:
    por eso viajan los dos criterios para cada candidato, la convergencia de
    cada ajuste, y las banderas que dicen cuándo la elección automática no es
    de fiar. Un selector que devuelve un número pelado invita a creerle.
    """

    best_n: int
    criterion: str  # "bic" | "icl"
    bic_by_n: dict[int, float]
    icl_by_n: dict[int, float]
    log_likelihood_by_n: dict[int, float]
    converged_by_n: dict[int, bool]

    @property
    def scores(self) -> dict[int, float]:
        return self.bic_by_n if self.criterion == "bic" else self.icl_by_n

    @property
    def gap(self) -> float:
        """Cuánto mejor es el ganador que el segundo. Un gap chico (<2) es
        empate técnico: la elección no está sostenida por los datos."""
        ordered = sorted(self.scores.values())
        return float(ordered[1] - ordered[0]) if len(ordered) > 1 else float("inf")

    @property
    def at_boundary(self) -> bool:
        """El ganador es el candidato más grande (o el más chico) probado.

        Cuando pasa, el criterio no encontró un óptimo interior: lo más
        probable es que quiera seguir creciendo y que el rango probado sea el
        que está decidiendo, no los datos.
        """
        keys = sorted(self.scores)
        return self.best_n in (keys[0], keys[-1]) and len(keys) > 1

    @property
    def monotone_in_n(self) -> bool:
        """El criterio baja (mejora) en todo el rango: no hay codo que elegir."""
        values = [self.scores[n] for n in sorted(self.scores)]
        return len(values) > 2 and all(b < a for a, b in zip(values, values[1:]))

    @property
    def knee_n(self) -> int:
        """n donde la mejora marginal se desploma — la elección por parsimonia.

        Cuando el criterio baja en todo el rango (que es lo que pasa con 69k
        barras de ETHUSDT), el "mejor" automático es simplemente el candidato
        más grande que se probó. Acá se busca el primer n cuya mejora al pasar
        al siguiente ya vale menos que `KNEE_IMPROVEMENT_RATIO` de la primera:
        agregar estados sigue ajustando, pero cada vez explica menos.
        """
        keys = sorted(self.scores)
        if len(keys) < 3:
            return self.best_n
        gains = [self.scores[a] - self.scores[b] for a, b in zip(keys, keys[1:])]
        first = gains[0]
        if first <= 0:
            return keys[0]
        for n, gain in zip(keys[1:], gains[1:]):
            if gain < KNEE_IMPROVEMENT_RATIO * first:
                return n
        return keys[-1]

    @property
    def warnings(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.monotone_in_n:
            out.append(
                f"el {self.criterion.upper()} mejora en todo el rango probado: no hay óptimo "
                f"interior y el automático es el borde — por parsimonia, n={self.knee_n}"
            )
        elif self.at_boundary:
            out.append(
                f"el ganador (n={self.best_n}) está en el borde del rango: ampliar candidatos "
                "antes de creerle"
            )
        if self.gap < 2.0:
            out.append(f"empate técnico con el segundo (gap {self.gap:.1f} < 2)")
        no_conv = [n for n, ok in self.converged_by_n.items() if not ok]
        if no_conv:
            out.append(f"sin converger en n={no_conv}: su verosimilitud no es comparable")
        return tuple(out)

    def as_dict(self) -> dict[str, Any]:
        return {
            "best_n": self.best_n,
            "criterion": self.criterion,
            "bic_by_n": {str(k): v for k, v in self.bic_by_n.items()},
            "icl_by_n": {str(k): v for k, v in self.icl_by_n.items()},
            "log_likelihood_by_n": {str(k): v for k, v in self.log_likelihood_by_n.items()},
            "converged_by_n": {str(k): v for k, v in self.converged_by_n.items()},
            "gap": self.gap,
            "knee_n": self.knee_n,
            "at_boundary": self.at_boundary,
            "monotone_in_n": self.monotone_in_n,
            "warnings": list(self.warnings),
        }


def select_n_states(
    X: FloatArray,
    candidates: Sequence[int] = DEFAULT_N_CANDIDATES,
    *,
    criterion: str = "bic",
    n_iter: int = 100,
    tol: float = 1e-4,
    reg_covar: float = DEFAULT_REG_COVAR,
    seed: int = 42,
) -> tuple[GaussianHMM, StateSelection]:
    """Ajusta un HMM por cada candidato y devuelve el mejor según `criterion`.

    Se entrena **solo sobre la ventana de entrenamiento** que reciba: elegir n
    mirando también el test es una fuga de información tan real como usar el
    precio futuro, aunque parezca inocente porque "es solo un hiperparámetro".

    Los ajustes que no convergieron quedan fuera de la competencia —su
    verosimilitud es la de un EM interrumpido, no la del modelo— salvo que
    ninguno converja, en cuyo caso se elige entre ellos y la advertencia queda
    registrada en el resultado.

    `criterion="icl"` es el recomendado cuando lo que se busca son regímenes
    interpretables sobre muestras grandes; el default es "bic" porque es lo que
    CLAUDE.md especifica y lo que el dashboard muestra por defecto.
    """
    if criterion not in ("bic", "icl"):
        raise ValueError("criterion debe ser 'bic' o 'icl'")
    if not candidates:
        raise ValueError("hace falta al menos un candidato de n_states")

    models: dict[int, GaussianHMM] = {}
    bic_by_n: dict[int, float] = {}
    icl_by_n: dict[int, float] = {}
    ll_by_n: dict[int, float] = {}
    conv_by_n: dict[int, bool] = {}

    for n in candidates:
        model = GaussianHMM(n_states=n, n_iter=n_iter, tol=tol, reg_covar=reg_covar, seed=seed)
        model.fit(X)
        models[n] = model
        bic_by_n[n] = model.bic(X)
        icl_by_n[n] = model.icl(X)
        ll_by_n[n] = model.log_likelihood_
        conv_by_n[n] = model.converged_

    scores = bic_by_n if criterion == "bic" else icl_by_n
    elegibles = [n for n in scores if conv_by_n[n]] or list(scores)
    best_n = min(elegibles, key=lambda n: scores[n])

    return models[best_n], StateSelection(
        best_n=best_n,
        criterion=criterion,
        bic_by_n=bic_by_n,
        icl_by_n=icl_by_n,
        log_likelihood_by_n=ll_by_n,
        converged_by_n=conv_by_n,
    )
