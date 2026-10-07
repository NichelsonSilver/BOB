"""Páginas vistas de Wikipedia (API REST oficial de Wikimedia, sin key).

Es una medida de **atención del público**, no de flujo: sirve para contrastar
la narrativa (capa 4) —"todo el mundo habla de X"— contra un dato. Nunca es
feature de un modelo.

Medido el 2026-10-07: historia diaria desde 2015-07-01; el día D se publica
completo al día siguiente; no se revisa. `agent=user` excluye bots
declarados (no los no declarados: sesgo conocido de la fuente).

**La política de Wikimedia exige un contacto en el User-Agent** (email o
URL). Medido el 2026-10-07: un User-Agent genérico recibe 403 ("Please respect
our robot policy"). El contacto se lee de `WIKI_CONTACT` en `backend/.env`,
que nunca se versiona; sin él, la ingesta se salta con un mensaje claro y el
resto de MAREA sigue igual.

Solo I/O.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote

import httpx

BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
MIN_INTERVAL_S = 0.5
EARLIEST = date(2015, 7, 1)


class WikimediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Article:
    project: str  # "en.wikipedia"
    title: str

    @property
    def key(self) -> str:
        return f"{self.project}:{self.title}"


#: Artículos en inglés: es donde se concentra el tráfico global.
ARTICLES: tuple[Article, ...] = (
    Article("en.wikipedia", "Bitcoin"),
    Article("en.wikipedia", "Cryptocurrency"),
    Article("en.wikipedia", "Ethereum"),
)


def user_agent(contact: str) -> str:
    return f"MAREA/0.1 (analisis personal de liquidez; {contact}) python-httpx"


class WikimediaClient:
    def __init__(
        self,
        contact: str,
        http: httpx.Client | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not contact:
            raise WikimediaError(
                "falta WIKI_CONTACT en backend/.env: Wikimedia exige un contacto en el User-Agent"
            )
        self._http = http or httpx.Client(
            base_url=BASE_URL, timeout=60.0, headers={"User-Agent": user_agent(contact)}
        )
        self._min_interval = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_call = 0.0
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def daily_views(self, article: Article, start: date, end: date) -> list[tuple[date, int]]:
        """Vistas diarias de `start` a `end` inclusive (días completos)."""
        path = (
            f"/{article.project}/all-access/user/{quote(article.title, safe='')}/daily/"
            f"{start:%Y%m%d}/{end:%Y%m%d}"
        )
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            try:
                r = self._http.get(path)
            except httpx.TransportError:
                if attempt == self._max_retries:
                    raise
                self._sleep(2.0**attempt)
                continue
            if (r.status_code == 429 or r.status_code >= 500) and attempt < self._max_retries:
                self._sleep(2.0**attempt)
                continue
            if r.status_code == 404:
                return []  # rango sin datos todavía: no es error
            if r.status_code != 200:
                raise WikimediaError(f"{article.key}: HTTP {r.status_code} {r.text[:200]}")
            return [
                (datetime.strptime(it["timestamp"][:8], "%Y%m%d").date(), int(it["views"]))
                for it in r.json().get("items", [])
            ]
        raise AssertionError("inalcanzable")  # pragma: no cover
