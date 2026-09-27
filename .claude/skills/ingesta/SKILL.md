---
name: ingesta
description: Baja o actualiza series de MAREA y reporta cobertura y huecos. Usar cuando Nichelson pida actualizar datos, revisar huecos o verificar qué está ingestado.
---

# /ingesta

Agente: `.claude/agents/ingesta.md`. Contexto: `docs/REESTRUCTURACION_MAREA.md`.

Delega en el agente `ingesta` con el alcance que pidió Nichelson (argumentos: $ARGUMENTS; sin argumentos = estado de todas las series).

Antes de lanzar cualquier descarga del panel cripto, revisa la hora UTC: la ingesta diaria del panel la hace la tarea programada `BOB_panel_forward_diario` (regla 12). Si la hora cae dentro de 00:00–02:00 UTC y la tarea no corrió, avisa en vez de correrla por tu cuenta.

Devuelve la tabla de cobertura del agente sin resumirla: los huecos son el dato.
