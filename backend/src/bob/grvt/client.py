from __future__ import annotations

import logging

from pysdk.grvt_ccxt_env import GrvtEnv
from pysdk.grvt_ccxt_pro import GrvtCcxtPro

from bob.config import settings

logger = logging.getLogger(__name__)

# Map our env string to SDK enum
_ENV_MAP = {
    "testnet": GrvtEnv.TESTNET,
    "prod": GrvtEnv.PROD,
    "mainnet": GrvtEnv.PROD,
    "dev": GrvtEnv.DEV,
    "staging": GrvtEnv.STAGING,
}


def _get_grvt_env() -> GrvtEnv:
    env_str = settings.grvt_env.lower()
    grvt_env = _ENV_MAP.get(env_str)
    if grvt_env is None:
        raise ValueError(f"Invalid GRVT_ENV: {env_str!r}. Use: {list(_ENV_MAP.keys())}")
    return grvt_env


def create_grvt_client() -> GrvtCcxtPro:
    """Create and return an authenticated GrvtCcxtPro client."""
    return GrvtCcxtPro(
        env=_get_grvt_env(),
        logger=logger,
        parameters={
            "trading_account_id": settings.grvt_trading_account_id,
            "private_key": settings.grvt_private_key.get_secret_value(),
            "api_key": settings.grvt_api_key.get_secret_value(),
        },
    )


async def check_grvt_connection(client: GrvtCcxtPro) -> dict:
    """Check GRVT connectivity by fetching account summary.

    Returns a dict with connection status info.
    """
    try:
        account = await client.get_account_summary(type="sub-account")
        return {
            "status": "ok",
            "environment": settings.grvt_env,
            "authenticated": True,
            "trading_account_id": settings.grvt_trading_account_id,
            "account_summary": bool(account),
        }
    except Exception as e:
        return {
            "status": "error",
            "environment": settings.grvt_env,
            "authenticated": False,
            "error": str(e),
        }
