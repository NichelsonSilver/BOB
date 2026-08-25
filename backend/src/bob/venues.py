"""Perfiles de venue — dónde y cómo opera el usuario. PURO, declarativo.

**Por qué existe.** Binance cumple hoy dos papeles distintos en BOB y conviene
no confundirlos:

1. **Fuente de datos de mercado.** El perp de Binance es el más líquido del
   par, así que su precio y su flujo son la mejor referencia disponible
   *independientemente de dónde opere el usuario*. Esta pata NO es opcional: el
   modelo se entrena sobre esta serie, y cambiar la fuente obliga a reentrenar.
2. **Venue de ejecución.** Esta sí es opcional, y es lo único que este módulo
   describe. Un venue aporta parámetros —fees, funding, tiers de maintenance
   margin— y nada más. El motor probabilístico no lo mira.

De ahí la promesa de CLAUDE.md llevada un paso más allá: el modelo es agnóstico
del símbolo *y* del venue de ejecución. Lo que no es —y decirlo importa— es
agnóstico de la fuente de datos con la que se entrenó.

**Los números de acá son referenciales.** Cada venue publica su schedule y lo
cambia; los tiers de MMR además dependen del notional de la posición. Antes de
poner capital real hay que leer el bracket vigente, y el dashboard debe decir
qué perfil está usando. Un MMR subestimado pone la liquidación más lejos de lo
que realmente está: el error más caro que este proyecto puede cometer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from bob.models.labeling import BarrierConfig
from bob.models.projection import LeverageProfile


@dataclass(frozen=True)
class MarginTier:
    """Un escalón del bracket de margen de mantenimiento.

    `notional_cap` es el techo del escalón en USDT. `maintenance_amount` es el
    término constante que corrige el salto entre escalones (el `cumB` de
    Binance): sin él, el precio de liquidación da un brinco irreal justo en la
    frontera del tier.
    """

    notional_cap: float
    maintenance_margin_rate: float
    maintenance_amount: float = 0.0


@dataclass(frozen=True)
class VenueProfile:
    """Cómo cobra y cómo liquida el lugar donde el usuario entra.

    Todos los porcentajes son fracciones (0.0005 = 0,05%), igual que en
    `labeling.py` y `projection.py`.
    """

    key: str
    name: str
    #: Fee de una pata. El round-trip se arma según cómo entre y salga el
    #: usuario: taker/taker es el caso conservador y es el default.
    maker_fee: float
    taker_fee: float
    #: Slippage de referencia por pata, para pares líquidos en tamaño chico.
    slippage_pct: float
    #: Cada cuántas horas se cobra funding en este venue.
    funding_interval_hours: float
    #: Escalones de margen, ordenados por `notional_cap` ascendente.
    margin_tiers: tuple[MarginTier, ...]
    #: Máximo leverage que el venue ofrece. No es una recomendación: BOB calcula
    #: su propio `max_safe_leverage` y suele quedar muy por debajo.
    max_leverage: float
    notes: str = ""

    def roundtrip_fee(self, *, maker_entry: bool = False, maker_exit: bool = False) -> float:
        """Fee de ida y vuelta según cómo entre y salga el usuario."""
        entry = self.maker_fee if maker_entry else self.taker_fee
        exit_ = self.maker_fee if maker_exit else self.taker_fee
        return entry + exit_

    def tier_for(self, notional: float) -> MarginTier:
        """Escalón de margen que aplica a un notional dado.

        Por encima del último tier se devuelve el último: es el más exigente, y
        equivocarse hacia el lado exigente acerca la liquidación en vez de
        alejarla.
        """
        for tier in self.margin_tiers:
            if notional <= tier.notional_cap:
                return tier
        return self.margin_tiers[-1]

    def barrier_config(
        self,
        base: BarrierConfig | None = None,
        *,
        funding_pct_per_8h: float | None = None,
        maker_entry: bool = False,
        maker_exit: bool = False,
    ) -> BarrierConfig:
        """Aplica los costos del venue a una configuración de barreras.

        Los costos entran en el **etiquetado**, no como un descuento posterior:
        un TP que no cubre la fricción del venue no es un trade ganador, y el
        label tiene que decirlo desde el principio.
        """
        config = base or BarrierConfig()
        funding = (
            config.funding_pct_per_8h
            if funding_pct_per_8h is None
            else funding_pct_per_8h * (8.0 / self.funding_interval_hours)
        )
        return replace(
            config,
            fee_roundtrip_pct=self.roundtrip_fee(maker_entry=maker_entry, maker_exit=maker_exit),
            slippage_pct=self.slippage_pct * 2.0,  # una pata de slippage por lado
            funding_pct_per_8h=funding,
        )

    def leverage_profile(
        self, leverage: float, notional: float, quantity: float
    ) -> LeverageProfile:
        """Perfil de leverage con el tier de margen que corresponde al tamaño.

        `quantity` es el tamaño en moneda base: el `maintenance_amount` del
        bracket es un monto absoluto y `projection.py` lo consume por unidad.
        """
        tier = self.tier_for(notional)
        per_unit = tier.maintenance_amount / quantity if quantity > 0 else 0.0
        return LeverageProfile(
            leverage=leverage,
            maintenance_margin_rate=tier.maintenance_margin_rate,
            maintenance_amount_per_unit=per_unit,
        )


#: Binance USDⓈ-M Futures. Fees del tier VIP 0 sin descuento por BNB; los tres
#: primeros brackets de ETHUSDT (los que aplican a tamaño minorista).
BINANCE_USDM: Final = VenueProfile(
    key="binance_usdm",
    name="Binance USDⓈ-M Futures",
    maker_fee=0.0002,
    taker_fee=0.0005,
    slippage_pct=0.0002,
    funding_interval_hours=8.0,
    margin_tiers=(
        MarginTier(notional_cap=50_000.0, maintenance_margin_rate=0.005, maintenance_amount=0.0),
        MarginTier(notional_cap=500_000.0, maintenance_margin_rate=0.01, maintenance_amount=250.0),
        MarginTier(
            notional_cap=1_000_000.0, maintenance_margin_rate=0.02, maintenance_amount=5_250.0
        ),
    ),
    max_leverage=125.0,
    notes="Es también la fuente de datos del modelo. Funding cada 8h.",
)

#: Bybit USDT Perpetual. Fees y brackets de referencia; el funding también es
#: de 8h en los pares principales, pero Bybit lo ajusta por par.
BYBIT_LINEAR: Final = VenueProfile(
    key="bybit_linear",
    name="Bybit USDT Perpetual",
    maker_fee=0.0002,
    taker_fee=0.00055,
    slippage_pct=0.00025,
    funding_interval_hours=8.0,
    margin_tiers=(
        MarginTier(notional_cap=100_000.0, maintenance_margin_rate=0.005),
        MarginTier(notional_cap=500_000.0, maintenance_margin_rate=0.01, maintenance_amount=500.0),
    ),
    max_leverage=100.0,
    notes="Fuente de datos sigue siendo Binance: el precio puede diferir en basis.",
)

#: OKX Perpetual Swap. OKX cobra funding cada 8h en la mayoría de los pares.
OKX_SWAP: Final = VenueProfile(
    key="okx_swap",
    name="OKX Perpetual Swap",
    maker_fee=0.0002,
    taker_fee=0.0005,
    slippage_pct=0.0003,
    funding_interval_hours=8.0,
    margin_tiers=(
        MarginTier(notional_cap=50_000.0, maintenance_margin_rate=0.005),
        MarginTier(notional_cap=250_000.0, maintenance_margin_rate=0.01, maintenance_amount=250.0),
    ),
    max_leverage=100.0,
    notes="Fuente de datos sigue siendo Binance.",
)

VENUES: Final[dict[str, VenueProfile]] = {
    profile.key: profile for profile in (BINANCE_USDM, BYBIT_LINEAR, OKX_SWAP)
}

DEFAULT_VENUE: Final = BINANCE_USDM.key


def get_venue(key: str | None = None) -> VenueProfile:
    """Perfil por clave. Sin clave devuelve el default."""
    resolved = key or DEFAULT_VENUE
    try:
        return VENUES[resolved]
    except KeyError:
        raise ValueError(
            f"venue desconocido: {resolved!r}. Disponibles: {sorted(VENUES)}"
        ) from None
