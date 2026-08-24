"""Capa en vivo — lo que corre mientras el mercado se mueve.

Fase 1:
  feed.py     — puente entre el feed de Binance y el broadcast del dashboard,
                más el ciclo de snapshots de derivados.
Fase 5 (pendiente):
  analyst.py  — data -> features -> modelo -> señal. Latencia objetivo <1s
                del tick de Binance al dashboard. Fuentes lentas (sentimiento)
                NUNCA en el hot path: se leen de cache alimentado por
                APScheduler.
"""
