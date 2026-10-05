---
name: liquidez
description: Lee el régimen de liquidez global (Fed neta, M2, dólar, tasas reales, crédito, COT, Chile) y su cambio reciente. Monitoreo, sin pronóstico.
---

# /liquidez

Agente: `.claude/agents/liquidez.md`. Contexto: `docs/REESTRUCTURACION_MAREA.md`.

Delega en el agente `liquidez` (argumentos: $ARGUMENTS).

Las cifras salen de `uv run python -m marea.monitor` (en `backend/`), después de `marea.data.ingest todo` si los datos tienen más de una semana. Nunca de memoria. Presenta la tabla del agente con fecha del dato y fecha de publicación separadas.
