"""Backtesting engine — event-driven, PURO, sin lookahead. EL GATE de Fase 4.

Fase 4 (pendiente):
  engine.py      — replay barra a barra de klines persistidas → señales → fills
  walkforward.py — train/test rolling estricto (el modelo solo ve el pasado)
  metrics.py     — win rate por bucket, profit factor, max DD, error de calibración

Criterio de salida de Fase 4: error de calibración < 10pp por bucket sobre
>= 12 meses de ETHUSDT 15m. Sin eso, NO se avanza a señales en vivo.
"""
