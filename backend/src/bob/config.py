from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GRVT credentials
    grvt_private_key: SecretStr
    grvt_api_key: SecretStr
    grvt_trading_account_id: str
    grvt_env: str = "testnet"

    # Backend
    bob_host: str = "0.0.0.0"
    bob_port: int = 8000
    bob_log_level: str = "INFO"

    # Global limits
    max_total_capital: int = 1000
    max_concurrent_bots: int = 5
    max_leverage: int = 10


settings = Settings()  # type: ignore[call-arg]
