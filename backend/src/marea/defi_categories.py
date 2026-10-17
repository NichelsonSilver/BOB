"""TVL de DeFi por categoría, agregado desde el listado de protocolos (M7). Puro.

DefiLlama cobra por `/categories` desde 2026. El listado gratuito de
protocolos (`lite/protocols2`) trae, por protocolo, la categoría y el TVL de
ahora y de hace 1, 7 y 30 días. Esto lo suma por categoría.

Definiciones, todas declaradas porque cada una es una elección:

- **TVL de un protocolo**: el `tvl` por defecto de DefiLlama, sin `staking`,
  `pool2` ni `borrowed`.
- **Fuera**: los protocolos sin categoría y los `CEX`. Un exchange
  centralizado no es DeFi, y DefiLlama tampoco lo suma a su total.
- **Crecimiento, mismos protocolos**: se compara el TVL de hoy contra el de
  hace N días **solo en los protocolos que tienen los dos valores**. Un
  protocolo que DefiLlama empezó a listar hace una semana no tiene valor de
  hace 30 días; sumarlo con 0 contaría un alta del listado como dinero nuevo.
  Su TVL se informa aparte (`tvl_unmatched_month`).
- **Doble conteo**: un token de liquid staking depositado en un protocolo de
  lending cuenta en las dos categorías. Cada categoría es correcta por sí
  sola; la suma de todas no es el TVL total de DeFi. Medido el 2026-10-17:
  270,6 mil millones de suma contra 89,4 del total por cadena de la M5.7. Esa
  diferencia **no se pudo conciliar**: restando puentes, liquid staking y lo
  marcado `doublecounted` quedan 116,0 (+30%). Por eso la ingesta no se
  controla contra ese total (`marea.data.ingest_industries`).
- **No es flujo neto**: el TVL se mide en USD, así que sube si sube ETH aunque
  nadie deposite un dólar. Es valor depositado, no dinero que entró.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

EXCLUDED_CATEGORIES: frozenset[str] = frozenset({"CEX"})

#: Ventanas del listado: (sufijo, campo de DefiLlama, días).
WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("day", "tvlPrevDay", 1),
    ("week", "tvlPrevWeek", 7),
    ("month", "tvlPrevMonth", 30),
)


@dataclass(frozen=True)
class CategoryTvl:
    category: str
    tvl: float
    protocols: int
    #: Por ventana: TVL de hoy y de antes, solo de los protocolos con los dos.
    now_day: float
    prev_day: float
    now_week: float
    prev_week: float
    now_month: float
    prev_month: float
    #: TVL de hoy de los protocolos sin valor de hace 30 días (altas del listado).
    tvl_unmatched_month: float

    def growth(self, window: str) -> float | None:
        now: float = getattr(self, f"now_{window}")
        prev: float = getattr(self, f"prev_{window}")
        return now / prev - 1.0 if prev > 0 else None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and f >= 0 else None


def aggregate(protocols: Iterable[Mapping[str, Any]]) -> list[CategoryTvl]:
    """Suma por categoría, ordenado de mayor a menor TVL."""
    acc: dict[str, dict[str, float]] = {}
    for p in protocols:
        cat = p.get("category")
        tvl = _num(p.get("tvl"))
        if not isinstance(cat, str) or not cat or cat in EXCLUDED_CATEGORIES or tvl is None:
            continue
        a = acc.setdefault(cat, {"tvl": 0.0, "protocols": 0.0, "tvl_unmatched_month": 0.0})
        a["tvl"] += tvl
        a["protocols"] += 1
        for suffix, field, _ in WINDOWS:
            prev = _num(p.get(field))
            a.setdefault(f"now_{suffix}", 0.0)
            a.setdefault(f"prev_{suffix}", 0.0)
            if prev is not None and prev > 0:
                a[f"now_{suffix}"] += tvl
                a[f"prev_{suffix}"] += prev
            elif suffix == "month":
                a["tvl_unmatched_month"] += tvl
    out = [
        CategoryTvl(
            category=cat,
            tvl=a["tvl"],
            protocols=int(a["protocols"]),
            now_day=a["now_day"],
            prev_day=a["prev_day"],
            now_week=a["now_week"],
            prev_week=a["prev_week"],
            now_month=a["now_month"],
            prev_month=a["prev_month"],
            tvl_unmatched_month=a["tvl_unmatched_month"],
        )
        for cat, a in acc.items()
    ]
    return sorted(out, key=lambda c: -c.tvl)
