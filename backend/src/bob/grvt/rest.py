"""REST wrapper over GrvtCcxtPro for order operations.

Responsibilities:
    - place_order (LIMIT POST_ONLY by default — grid orders must be maker)
    - cancel_order / cancel_all
    - fetch_open_orders
    - Map our string client_order_id ({bot}-{level}-{side}-{bucket}) to the
      uint64 id that GRVT requires, keeping a bidirectional dict so fill
      messages from WS can be matched back to the internal cloid.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal

from pysdk.grvt_ccxt_pro import GrvtCcxtPro

logger = logging.getLogger(__name__)


# GRVT wants client_order_id as a decimal string of a uint64.
# Per SDK docs: UI uses [0, 2^63 - 1]; machine-generated clients should
# use [2^63, 2^64 - 1] to avoid collisions.
_MACHINE_ID_OFFSET = 1 << 63
_MACHINE_ID_SPAN = (1 << 64) - _MACHINE_ID_OFFSET  # 2^63


def cloid_str_to_int(cloid: str) -> int:
    """Deterministically map a string cloid to a uint64 in the machine range.

    Uses BLAKE2b-64 to avoid collisions across bots/levels/buckets.
    """
    digest = hashlib.blake2b(cloid.encode("utf-8"), digest_size=8).digest()
    n = int.from_bytes(digest, "big")
    # Fold into [2^63, 2^64 - 1]
    return _MACHINE_ID_OFFSET + (n % _MACHINE_ID_SPAN)


@dataclass(frozen=True)
class PlacedOrder:
    internal_cloid: str  # our grid-level cloid (string)
    exchange_cloid: str  # decimal uint64 as string
    order_id: str | None  # assigned by exchange if order was accepted
    raw: dict


class GrvtRestClient:
    """High-level wrapper — call these from GridBot / BotManager.

    Holds a bidirectional mapping of internal string cloid <-> exchange uint64
    string, populated on place_order and consulted on fill events.
    """

    def __init__(self, client: GrvtCcxtPro) -> None:
        self._client = client
        self._int_to_str: dict[str, str] = {}
        self._str_to_int: dict[str, str] = {}

    def register_cloid(self, internal_cloid: str) -> str:
        """Return (and cache) the exchange cloid for a given internal cloid."""
        cached = self._str_to_int.get(internal_cloid)
        if cached is not None:
            return cached
        exchange_cloid = str(cloid_str_to_int(internal_cloid))
        self._str_to_int[internal_cloid] = exchange_cloid
        self._int_to_str[exchange_cloid] = internal_cloid
        return exchange_cloid

    def resolve_cloid(self, exchange_cloid: str) -> str | None:
        """Map exchange cloid back to our internal string cloid.

        Returns None if unknown — caller should treat the event as external.
        """
        return self._int_to_str.get(str(exchange_cloid))

    async def place_order(
        self,
        symbol: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
        internal_cloid: str,
        post_only: bool = True,
    ) -> PlacedOrder:
        """Place a LIMIT order (POST_ONLY by default).

        Raises on validation errors from the SDK; returns a PlacedOrder with
        an empty order_id + empty raw dict if the exchange didn't ack.
        """
        exchange_cloid = self.register_cloid(internal_cloid)

        params: dict = {
            "client_order_id": exchange_cloid,
            "post_only": post_only,
            "time_in_force": "GOOD_TILL_TIME",
        }

        logger.info(
            "place_order symbol=%s side=%s price=%s qty=%s cloid=%s (ex=%s)",
            symbol,
            side,
            price,
            quantity,
            internal_cloid,
            exchange_cloid,
        )
        raw = await self._client.create_order(
            symbol=symbol,
            order_type="limit",
            side=side,
            amount=str(quantity),
            price=str(price),
            params=params,
        )

        order_id = None
        if isinstance(raw, dict):
            meta = raw.get("metadata") or {}
            order_id = meta.get("order_id") or raw.get("order_id")

        return PlacedOrder(
            internal_cloid=internal_cloid,
            exchange_cloid=exchange_cloid,
            order_id=order_id,
            raw=raw or {},
        )

    async def cancel_by_internal_cloid(self, internal_cloid: str) -> bool:
        """Cancel an order identified by its internal string cloid."""
        exchange_cloid = self._str_to_int.get(internal_cloid)
        if exchange_cloid is None:
            logger.warning("cancel: no mapping for internal cloid %s", internal_cloid)
            return False
        return await self._client.cancel_order(
            id=None, params={"client_order_id": exchange_cloid}
        )

    async def cancel_all(self, kind: str = "PERPETUAL") -> bool:
        """Cancel all open orders on the sub-account."""
        return await self._client.cancel_all_orders(params={"kind": kind})

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        return await self._client.fetch_open_orders(symbol=symbol)
