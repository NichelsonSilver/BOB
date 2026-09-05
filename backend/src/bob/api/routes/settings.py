"""Settings page — espejo de lo que el backend está corriendo. Read-only.

**Es un espejo y no un formulario, y eso es una decisión fechada.** Mientras
corre la validación forward, cambiar `feature_set` o `vol_kind` en caliente
mete dos `model_version` en la misma muestra y la cobertura medida deja de
describir a ninguno de los dos. Es el mismo motivo por el que XGBoost se
agregó ANTES de arrancar la acumulación y no después: con 2 registros el
cambio era gratis, con 1.150 cuesta la corrida.

Lo que el usuario sí mueve —leverage del slider, nivel del cono que mira— no
cambia ningún modelo y vive en el navegador, no acá.

Lo que este endpoint NO devuelve nunca: el token de Telegram, las credenciales
de Outliers Club, ni nada envuelto en `SecretStr`. Solo si están configuradas.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from bob.config import settings as cfg
from bob.data.binance_rest import INTERVAL_MS
from bob.models.experiment import (
    FEATURE_SETS,
    GATE_MAX_CALIBRATION_ERROR_PP,
    GATE_MIN_AUC,
    GATE_MIN_BSS,
    MODEL_VERSION,
    ExperimentConfig,
)
from bob.models.projection import DEFAULT_MMR, DEFAULT_SAFETY_BUFFER
from bob.venues import DEFAULT_VENUE, VENUES

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def read_settings() -> dict[str, Any]:
    """Config efectiva del backend, el setup que define las barreras y el venue."""
    experiment = ExperimentConfig()
    return {
        "editable": False,
        "editable_reason": (
            "durante la validación forward los settings del modelo son de solo "
            "lectura: cambiarlos mezcla dos model_version en la misma muestra"
        ),
        "runtime": {
            "watchlist": cfg.watchlist,
            "default_timeframe": cfg.bob_default_timeframe,
            "known_timeframes": sorted(INTERVAL_MS),
            "signal_threshold": cfg.bob_signal_threshold,
            "live_data": cfg.bob_live_data,
            "live_analyst": cfg.bob_live_analyst,
            "feed_mode": cfg.bob_feed_mode,
            "live_features": cfg.bob_live_features,
            "feature_sets": sorted(FEATURE_SETS),
            "refit_every_bars": cfg.bob_refit_every_bars,
            "default_leverage": cfg.bob_default_leverage,
            "tracker_interval_min": cfg.bob_tracker_interval_min,
            "snapshot_period": cfg.bob_snapshot_period,
            "snapshot_interval_min": cfg.bob_snapshot_interval_min,
        },
        "model": {
            "model_version": MODEL_VERSION,
            "model_kind": experiment.model_kind,
            "vol_kind": experiment.vol_kind,
            "directions": list(experiment.directions),
            "conformal_alphas": list(experiment.conformal_alphas),
            "n_splits": experiment.n_splits,
            "embargo_frac": experiment.embargo_frac,
            "seed": experiment.seed,
        },
        "barrier": asdict(experiment.barrier),
        "gate": {
            "min_auc": GATE_MIN_AUC,
            "min_bss": GATE_MIN_BSS,
            "max_calibration_error_pp": GATE_MAX_CALIBRATION_ERROR_PP,
        },
        "leverage": {
            "default_mmr": DEFAULT_MMR,
            "safety_buffer": DEFAULT_SAFETY_BUFFER,
        },
        "venues": {
            key: {
                "key": profile.key,
                "name": profile.name,
                "maker_fee": profile.maker_fee,
                "taker_fee": profile.taker_fee,
                "slippage_pct": profile.slippage_pct,
                "funding_interval_hours": profile.funding_interval_hours,
                "max_leverage": profile.max_leverage,
                "notes": profile.notes,
                "margin_tiers": [asdict(t) for t in profile.margin_tiers],
            }
            for key, profile in VENUES.items()
        },
        "default_venue": DEFAULT_VENUE,
        "integrations": {
            # Solo el hecho de estar configuradas. El valor no sale de acá ni
            # aunque el frontend lo pida: son credenciales de `.env` local.
            "telegram_configured": bool(
                cfg.telegram_bot_token.get_secret_value() and cfg.telegram_chat_id
            ),
            "outliers_configured": bool(
                cfg.outliners_email and cfg.outliners_password.get_secret_value()
            ),
        },
    }
