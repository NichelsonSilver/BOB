"""Registro de narrativa — capa 4 de MAREA, fase M4.

    uv run python -m marea.narrative add < noticia.json   # valida y agrega
    uv run python -m marea.narrative list --days 7

Una noticia **nunca es feature** (reglas 2 y 5): no tiene timestamp causal
auditable y es la capa más manipulable del sistema, también cuando la trae
Nichelson. Su función es marcar **divergencias** entre lo que se afirma y lo que
muestran los datos (`docs/REESTRUCTURACION_MAREA.md` §3.1).

El registro es `narrativa/registro.jsonl` en la raíz del repo, versionado:
una línea por noticia, **solo se agrega**, nunca se reescribe una entrada —
corregir una lectura vieja se hace con una entrada nueva que la cite
(`supersedes`). Guarda la afirmación resumida y la URL, nunca el texto del
artículo: el repo no redistribuye contenido ajeno.

El esquema es el de `.claude/agents/noticias.md`. Se valida acá y no en el
agente porque un registro que acepta cualquier cosa deja de ser consultable a
la tercera semana.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

REGISTRY = Path(__file__).resolve().parent.parent.parent.parent / "narrativa" / "registro.jsonl"

Kind = Literal["hecho", "cifra", "opinion", "prediccion"]
Risk = Literal["bajo", "medio", "alto"]


class DataCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series: str = Field(min_length=1)
    #: Lo que dice el dato, con su fecha, o "pendiente_datos". Nunca de memoria.
    reading: str = Field(min_length=1)
    #: Comando o artefacto del que sale `reading`. Una cifra sin origen es un bug.
    source_command: str | None = None


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    ingested_at_utc: str = ""
    published_at: str = Field(min_length=1, description="ISO o 'desconocida'")
    source: str = Field(min_length=1)
    url: str | None = None
    primary_source: bool
    claim: str = Field(min_length=10, max_length=600)
    kind: Kind
    assets: list[str] = Field(min_length=1)
    claimed_direction: str = Field(min_length=1)
    manipulation_risk: Risk
    risk_reasons: list[str] = Field(min_length=1)
    data_check: DataCheck
    #: null = no se pudo contrastar (pendiente_datos).
    divergence: bool | None
    supersedes: str | None = None

    @field_validator("divergence")
    @classmethod
    def _null_only_if_pending(cls, v: bool | None, info: Any) -> bool | None:
        dc = info.data.get("data_check")
        if v is None and dc is not None and dc.reading != "pendiente_datos":
            raise ValueError("divergence=null solo si data_check.reading es 'pendiente_datos'")
        if v is not None and dc is not None and dc.reading == "pendiente_datos":
            raise ValueError("sin datos no hay divergencia que afirmar: usar null")
        return v


def read(path: Path = REGISTRY) -> list[Entry]:
    if not path.exists():
        return []
    out: list[Entry] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                out.append(Entry.model_validate_json(line))
            except ValidationError as exc:
                raise ValueError(f"{path.name}:{n} no cumple el esquema: {exc}") from exc
    return out


def add(raw: dict[str, Any], path: Path = REGISTRY, now: datetime | None = None) -> Entry:
    """Valida y agrega. Asigna `id` e `ingested_at_utc`; no acepta que vengan dados."""
    if raw.get("id") or raw.get("ingested_at_utc"):
        raise ValueError("id e ingested_at_utc los asigna el registro, no el agente")
    existing = read(path)
    ts = (now or datetime.now(UTC)).replace(microsecond=0)
    entry = Entry.model_validate(raw)
    if entry.supersedes and entry.supersedes not in {e.id for e in existing}:
        raise ValueError(f"supersedes={entry.supersedes} no existe en el registro")
    entry.id = f"N-{ts:%Y%m%d}-{len(existing) + 1:04d}"
    entry.ingested_at_utc = ts.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry.model_dump_json() + "\n")
    return entry


def recent(entries: Iterable[Entry], days: int, now: datetime | None = None) -> list[Entry]:
    cut = (now or datetime.now(UTC)) - timedelta(days=days)
    return [e for e in entries if datetime.fromisoformat(e.ingested_at_utc) >= cut]


def render(entries: list[Entry]) -> str:
    if not entries:
        return "sin noticias registradas en el período"
    lines = []
    for e in entries:
        div = {True: "DIVERGE", False: "coincide", None: "sin contrastar"}[e.divergence]
        lines.append(
            f"{e.id}  [{e.manipulation_risk}]  {div:14}  {', '.join(e.assets)}: {e.claim}\n"
            f"    dato: {e.data_check.series} -> {e.data_check.reading}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description="registro de narrativa de MAREA")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="lee UNA entrada JSON de stdin o --file")
    a.add_argument("--file", type=Path)
    ls = sub.add_parser("list")
    ls.add_argument("--days", type=int, default=7)
    args = ap.parse_args(argv)

    if args.cmd == "add":
        text = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
        try:
            entry = add(json.loads(text))
        except (ValueError, ValidationError) as exc:
            print(f"rechazada: {exc}", file=sys.stderr)
            return 2
        print(f"registrada {entry.id}")
        return 0
    print(render(recent(read(), args.days)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
