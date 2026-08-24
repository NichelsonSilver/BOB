"""Conectores de datos de mercado — el ÚNICO paquete con I/O de mercado.

Fase 1:
  binance_rest.py  — histórico klines, OI, funding, ratios; token bucket
  store.py         — persistencia de klines/derivados en SQLite
  download.py      — CLI de descarga de histórico (idempotente)
  binance_ws.py    — WS multiplexado (klines, aggTrade, markPrice) + hub
  snapshots.py     — snapshot periódico de OI y ratios (ventana ~30 días)
Fase 7-8:
  sentiment.py     — Fear&Greed (alternative.me) + CoinGecko, cache por scheduler
  outliers.py      — Outliers Club (credenciales solo en .env local)

Ver docs/DATA_SOURCES.md para endpoints, rate limits y trampas conocidas.
"""
