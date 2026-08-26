"""Tests del comparador de variantes del gate.

Lo que se verifica no es el formato de la tabla sino que el veredicto salga de
los mismos umbrales que `ExperimentResult` — un comparador que use su propio
criterio diría "habilitado" donde el gate dice que no, y esa contradicción es
exactamente la que este proyecto no puede permitirse.
"""

from __future__ import annotations

import json

import pytest

from bob.backtest.compare import (
    VARIANT_ORDER,
    RunSummary,
    _variant_from_config,
    latest_by_variant,
    load_run,
    render_comparison,
)
from bob.models.experiment import (
    FEATURE_SETS,
    GATE_MAX_CALIBRATION_ERROR_PP,
    GATE_MIN_AUC,
    GATE_MIN_BSS,
)


def _direccion(auc: float, bss: float, calib_pp: float) -> dict:
    return {
        "direction": "long",
        "n_samples": 44293,
        "breakeven_prob": 0.52,
        "model": {
            "auc": auc,
            "brier": 0.247,
            "brier_skill_score": bss,
            "mean_calibration_error_pp": calib_pp,
        },
        "diebold_mariano": {"p_value": 0.008, "statistic": -2.65},
    }


def _artefacto(
    tmp_path,
    variante: str,
    *,
    auc: float = 0.52,
    bss: float = -0.003,
    calib_pp: float = 4.0,
    n_features: int = 55,
    familias: dict | None = None,
    ts: str = "20260825120000",
    vol_kind: str = "gbm",
    con_config: bool = True,
):
    use_deriv, use_book, use_near = FEATURE_SETS[variante]
    data = {
        "symbol": "ETHUSDT",
        "timeframe": "15m",
        "n_bars": 69119,
        "n_features": n_features,
        "date_from": "2024-08-31",
        "date_to": "2026-08-21",
        "directions": {
            "long": _direccion(auc, bss, calib_pp),
            "short": _direccion(auc, bss, calib_pp),
        },
        "family_importance": familias or {"momentum": 0.001},
        "importance_top": [["rsi_14", 0.002], ["atr_pct", 0.001]],
    }
    if con_config:
        data["config"] = {
            "use_derivatives": use_deriv,
            "use_book": use_book,
            "use_book_near": use_near,
            "vol_kind": vol_kind,
        }
    path = tmp_path / f"ETHUSDT-15m-{variante}-{vol_kind}-{ts}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Identificación de la variante
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("variante", VARIANT_ORDER)
def test_la_variante_sale_de_la_config(variante, tmp_path):
    run = load_run(_artefacto(tmp_path, variante))
    assert run.variant == variante


@pytest.mark.parametrize("variante", VARIANT_ORDER)
def test_el_estimador_de_volatilidad_en_el_nombre_no_confunde_la_variante(
    variante, tmp_path
):
    """La regresión que introdujo `--vol-model`.

    El nombre pasó de `...-price-2026...` a `...-price-xgb-2026...`, y el
    parseo por posición leía `xgb` como variante. Leerla de la config
    inmuniza al comparador contra el formato del nombre.
    """
    run = load_run(_artefacto(tmp_path, variante, vol_kind="xgb"))
    assert run.variant == variante


def test_los_runs_viejos_sin_config_caen_al_nombre():
    """Respaldo para artefactos de un esquema anterior."""
    assert _variant_from_config({}, "ETHUSDT-15m-full-20260825120000") == "full"


def test_los_runs_viejos_sin_etiqueta_no_rompen():
    """El run del 2026-08-24 no lleva variante: se agrupa aparte, no se pierde."""
    assert _variant_from_config({}, "ETHUSDT-15m-20260824143247") == "sin-etiqueta"


# --------------------------------------------------------------------------- #
# Veredicto: tiene que coincidir con el gate, no inventar el suyo
# --------------------------------------------------------------------------- #


def test_el_veredicto_usa_los_mismos_umbrales_que_el_gate(tmp_path):
    run = load_run(_artefacto(tmp_path, "price", auc=0.52, bss=-0.003, calib_pp=4.0))

    assert GATE_MAX_CALIBRATION_ERROR_PP == 10.0
    assert GATE_MIN_AUC == 0.55
    assert GATE_MIN_BSS == 0.0
    assert run.calibra  # 4.0 < 10
    assert not run.discrimina  # AUC 0.52 < 0.55 y BSS < 0
    assert not run.habilitado


def test_calibrar_sin_discriminar_no_habilita(tmp_path):
    """El caso que el gate existe para atajar: calibrado por construcción."""
    run = load_run(_artefacto(tmp_path, "price", auc=0.50, bss=0.0, calib_pp=1.0))
    assert run.calibra
    assert not run.discrimina
    assert not run.habilitado


def test_discriminar_sin_calibrar_tampoco_habilita(tmp_path):
    run = load_run(_artefacto(tmp_path, "full", auc=0.70, bss=0.05, calib_pp=15.0))
    assert not run.calibra
    assert run.discrimina
    assert not run.habilitado


def test_los_dos_criterios_habilitan(tmp_path):
    run = load_run(_artefacto(tmp_path, "full", auc=0.60, bss=0.02, calib_pp=5.0))
    assert run.habilitado


def test_una_direccion_mala_arruina_el_veredicto(tmp_path):
    """El dashboard ofrece las dos: con una sola calibrada no se habilita nada."""
    path = _artefacto(tmp_path, "full", auc=0.60, bss=0.02, calib_pp=5.0)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["directions"]["short"] = _direccion(0.51, -0.01, 4.0)
    path.write_text(json.dumps(data), encoding="utf-8")

    run = load_run(path)
    assert run.calibra
    assert not run.discrimina
    assert not run.habilitado


def test_el_bss_se_lee_del_artefacto_no_se_recomputa(tmp_path):
    """Recomputarlo arriesga comparar contra una definición distinta a la del gate."""
    run = load_run(_artefacto(tmp_path, "price", bss=-0.0028))
    assert run.directions["long"]["bss"] == pytest.approx(-0.0028)


# --------------------------------------------------------------------------- #
# Selección y render
# --------------------------------------------------------------------------- #


def test_toma_el_run_mas_reciente_de_cada_variante(tmp_path):
    _artefacto(tmp_path, "full", auc=0.51, ts="20260825090000")
    _artefacto(tmp_path, "full", auc=0.59, ts="20260825180000")

    runs = latest_by_variant(tmp_path)
    assert set(runs) == {"full"}
    assert runs["full"].directions["long"]["auc"] == pytest.approx(0.59)


def test_ignora_artefactos_de_otro_esquema(tmp_path):
    (tmp_path / "ETHUSDT-15m-full-20260825120000.json").write_text("{}", encoding="utf-8")
    (tmp_path / "roto.json").write_text("no soy json", encoding="utf-8")
    _artefacto(tmp_path, "price")

    runs = latest_by_variant(tmp_path)
    assert set(runs) == {"price"}


def test_sin_runs_lo_dice_en_vez_de_reventar():
    assert "No hay runs" in render_comparison({})


def test_la_tabla_muestra_las_variantes_y_el_veredicto(tmp_path):
    _artefacto(tmp_path, "price", auc=0.519, bss=-0.0028, n_features=55)
    _artefacto(
        tmp_path,
        "full",
        auc=0.560,
        bss=0.004,
        n_features=96,
        familias={"momentum": 0.001, "derivados": 0.003},
    )

    texto = render_comparison(latest_by_variant(tmp_path))

    assert "price" in texto and "full" in texto
    assert "derivados" in texto  # familia nueva en la tabla de importancia
    assert "HABILITADO" in texto
    # La diferencia contra el baseline es lo que se lee, no el valor suelto.
    assert "+0.041" in texto  # 0.560 - 0.519


def test_la_tabla_marca_el_empate_con_el_baseline(tmp_path):
    _artefacto(tmp_path, "price", auc=0.519)
    _artefacto(tmp_path, "full", auc=0.519)

    texto = render_comparison(latest_by_variant(tmp_path))
    assert "=" in texto


def test_render_sin_baseline_no_revienta(tmp_path):
    """Comparar solo variantes con features nuevas tiene que seguir funcionando."""
    _artefacto(tmp_path, "full", auc=0.56)
    _artefacto(tmp_path, "full+near", auc=0.57)

    texto = render_comparison(latest_by_variant(tmp_path))
    assert "full+near" in texto


def test_run_summary_es_inmutable():
    """Un resumen que se puede mutar deja de ser evidencia de lo que corrió."""
    run = RunSummary(
        variant="price",
        run_id="x",
        n_features=55,
        n_bars=1000,
        date_from="a",
        date_to="b",
        directions={},
        family_importance={},
        top_features=[],
    )
    with pytest.raises(AttributeError):
        run.variant = "full"  # type: ignore[misc]
