"""Loop de análisis en vivo (Fase 5, pendiente).

  analyst.py — data -> features -> modelo -> señal. Latencia objetivo <1s
  del tick de Binance al dashboard. Fuentes lentas (sentimiento) NUNCA en
  el hot path: se leen de cache alimentado por APScheduler.
"""
