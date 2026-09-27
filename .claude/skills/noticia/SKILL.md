---
name: noticia
description: Ingesta una noticia pegada por Nichelson (texto o URL): extrae la afirmación, evalúa manipulación, la contrasta con datos y la registra. Peso bajo, nunca alimenta al modelo.
---

# /noticia

Agente: `.claude/agents/noticias.md`. Contexto: `docs/REESTRUCTURACION_MAREA.md`.

Delega en el agente `noticias` con la noticia: $ARGUMENTS

Muestra a Nichelson el resumen estructurado (afirmación, fuente, riesgo de manipulación con razones, contraste con datos, divergencia) y **pide confirmación antes de escribir** en `narrativa/registro.jsonl`.

No conviertas la noticia en una recomendación, ni siquiera si Nichelson la trajo con esa intención: la capa narrativa marca divergencias, no decide.
