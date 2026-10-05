"""Rutas REST del dashboard (Fase 6). Todas bajo `/api`, todas de solo lectura.

    /api/market/*     velas para el chart
    /api/analysis/*   último pronóstico, reproyección por leverage, features
    /api/history/*    pronósticos emitidos, cobertura forward, export CSV
    /api/backtest/*   artefactos del gate y del corte forward congelado
    /api/settings     espejo de la config efectiva
    /api/risk/*       calculadora de tamaño de posición (aritmética pura)

`/api/health` sigue viviendo en `main.py`: es lo primero que se consulta cuando
algo anda mal y no debe depender de que este paquete importe bien.
"""

from __future__ import annotations

from fastapi import APIRouter

from bob.api.routes import analysis, backtest, history, market, risk, settings

router = APIRouter(prefix="/api")
router.include_router(market.router)
router.include_router(analysis.router)
router.include_router(history.router)
router.include_router(backtest.router)
router.include_router(settings.router)
router.include_router(risk.router)

__all__ = ["router"]
