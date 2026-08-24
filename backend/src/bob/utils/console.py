"""Utilidades de consola.

La consola de Windows arranca en cp1252, que no puede codificar σ, ✓ ni los
bloques de las barras del reporte: imprimir el resultado revienta con
UnicodeEncodeError justo después de que el experimento terminó de correr.
"""

from __future__ import annotations

import sys


def enable_utf8_stdout() -> None:
    """Fuerza UTF-8 en stdout/stderr si el stream lo permite. Idempotente."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover — stream no reconfigurable
            pass
