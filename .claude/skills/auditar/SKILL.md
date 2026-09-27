---
name: auditar
description: Auditoría metodológica de un resultado, pre-registro, feature, commit o memo de MAREA: lookahead, potencia, baseline, pre-registro, supervivencia, regla 2 y regla 12.
---

# /auditar

Agente: `.claude/agents/auditor.md`. Contexto: `docs/REESTRUCTURACION_MAREA.md`.

Delega en el agente `auditor` sobre: $ARGUMENTS (sin argumentos = el diff sin commitear más el último commit).

Devuelve los hallazgos ordenados por severidad, tal como los entrega el agente. Si no hay hallazgos, incluye qué se buscó y cómo.
