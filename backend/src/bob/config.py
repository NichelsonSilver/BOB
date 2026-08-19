from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Settings del asistente. Sin credenciales obligatorias: el market data
    de Binance es público y BOB nunca ejecuta órdenes, así que el backend
    debe bootear sin `.env`."""

    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Backend
    bob_host: str = "0.0.0.0"
    bob_port: int = 8000
    bob_log_level: str = "INFO"

    # Watchlist — símbolos de Binance Futures, separados por coma en .env
    bob_watchlist: str = "ETHUSDT"
    bob_default_timeframe: str = "15m"

    # Señales — umbral mínimo del KPI Seguridad para emitir señal
    bob_signal_threshold: float = 0.70

    # Telegram (Fase 7) — vacíos = alertas Telegram deshabilitadas
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    # Outliers Club (Fase 8) — solo .env local, nunca versionar
    outliners_email: str = ""
    outliners_password: SecretStr = SecretStr("")

    @property
    def watchlist(self) -> list[str]:
        return [s.strip().upper() for s in self.bob_watchlist.split(",") if s.strip()]


settings = Settings()
