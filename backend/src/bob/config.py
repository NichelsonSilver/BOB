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

    # Datos en vivo (Fase 1) — el WS de Binance y los snapshots de derivados
    # arrancan con el backend. Apagarlos (BOB_LIVE_DATA=false) deja el backend
    # 100% offline: útil para trabajar el backtest sin tocar la red.
    bob_live_data: bool = True
    #: "auto" | "ws" | "rest". En "auto" arranca por WebSocket y, según qué
    #: streams entreguen, cae entero a polling REST o rellena por REST solo lo
    #: mudo dejando el WS con el resto (ver data/binance_poll.py). "ws" fuerza
    #: el camino de baja latencia; "rest", el de polling.
    bob_feed_mode: str = "auto"
    #: Granularidad de los snapshots de OI / ratios (ventana ~30 días).
    # Debe coincidir con METRICS_PERIOD de data/download_vision.py: es la
    # grilla en la que el archivo histórico publica los mismos campos.
    bob_snapshot_period: str = "5m"
    #: Cadencia del snapshot. Cada request trae ~5 días, así que el solape
    #: cubre cualquier rato que el proceso haya estado caído.
    bob_snapshot_interval_min: int = 30

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
