"""Settings de MAREA. Credenciales de fuentes gratuitas, todas opcionales.

Se leen de `backend/.env` y de `.env.local` en la raíz del repo (ese gana), los
dos ignorados por git. Ninguna es obligatoria para importar el paquete: cada
conector falla con un mensaje claro recién cuando se lo usa sin su key.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_REPO_DIR = _BACKEND_DIR.parent

#: Base de MAREA, separada de `bob.db` a propósito: no toca el esquema ni la
#: metadata de SQLModel del módulo cripto (regla 12).
DB_PATH = _BACKEND_DIR / "marea.db"


class MareaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_BACKEND_DIR / ".env"), str(_REPO_DIR / ".env.local")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_fred: SecretStr | None = Field(default=None, alias="API_FRED")
    api_tiingo: SecretStr | None = Field(default=None, alias="API_TIINGO")
    bcch_user: str | None = Field(default=None, alias="BCCH_USER")
    bcch_pass: SecretStr | None = Field(default=None, alias="BCCH_PASS")
