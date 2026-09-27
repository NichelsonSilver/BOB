"""Point-in-time. Los casos salen de respuestas reales de FRED (2026-09-27)."""

from __future__ import annotations

from datetime import date

from marea.pit import OPEN_END, AsOfPoint, Period, as_of, first_release_lag_days, stitch

D = date.fromisoformat


def per(obs: str, start: str, end: str, value: float | None) -> Period:
    return Period(D(obs), D(start), D(end), value)


# M2SL, observación 2019-10-01, tal como la devolvió FRED pidiendo la ventana
# de tiempo real 2020-01-01..2020-06-30 (recortada en los dos bordes).
M2_OCT = [
    per("2019-10-01", "2020-01-01", "2020-01-08", 15191.6),
    per("2019-10-01", "2020-01-09", "2020-01-22", 15191.5),
    per("2019-10-01", "2020-01-23", "2020-04-15", 15154.2),
    per("2019-10-01", "2020-04-16", "2020-06-30", 15147.1),
]


class TestAsOf:
    def test_devuelve_el_valor_vigente_en_la_fecha_no_el_actual(self) -> None:
        pts = as_of(M2_OCT, D("2020-01-15"), D("2020-01-01"), 30)
        assert pts == [AsOfPoint(D("2019-10-01"), 15191.5, exact=True)]

    def test_bordes_inclusive(self) -> None:
        assert as_of(M2_OCT, D("2020-01-22"), None, None)[0].value == 15191.5
        assert as_of(M2_OCT, D("2020-01-23"), None, None)[0].value == 15154.2

    def test_lo_no_publicado_no_aparece(self) -> None:
        # M2 de diciembre de 2019 se publicó recién el 2020-01-16.
        dec = [per("2019-12-01", "2020-01-16", OPEN_END.isoformat(), 15401.8)]
        assert as_of(dec, D("2020-01-15"), D("2019-01-01"), 50) == []
        assert as_of(dec, D("2020-01-16"), D("2019-01-01"), 50)[0].value == 15401.8

    def test_antes_de_la_primera_vintage_usa_el_rezago_y_marca_no_exacto(self) -> None:
        # Petróleo: ALFRED empieza el 2011-04-06; un dato de 2008 viene con
        # realtime_start pegado a esa fecha y no se sabe cuándo se publicó.
        oil = [per("2008-07-11", "2011-04-06", OPEN_END.isoformat(), 145.31)]
        first = D("2011-04-06")
        assert as_of(oil, D("2008-07-12"), first, fallback_lag_days=7) == []
        pts = as_of(oil, D("2008-07-18"), first, fallback_lag_days=7)
        assert pts == [AsOfPoint(D("2008-07-11"), 145.31, exact=False)]

    def test_sin_rezago_medido_no_inventa(self) -> None:
        oil = [per("2008-07-11", "2011-04-06", OPEN_END.isoformat(), 145.31)]
        assert as_of(oil, D("2009-01-01"), D("2011-04-06"), None) == []

    def test_dato_nuevo_posterior_a_la_primera_vintage_no_usa_el_respaldo(self) -> None:
        # Una observación que aparece después de la 1ª vintage tiene fecha de
        # publicación real; el respaldo no debe adelantarla.
        late = [per("2015-03-01", "2015-04-10", OPEN_END.isoformat(), 1.0)]
        assert as_of(late, D("2015-04-01"), D("2011-04-06"), 5) == []

    def test_faltante_se_devuelve_como_none(self) -> None:
        gap = [per("2020-01-01", "2020-01-02", OPEN_END.isoformat(), None)]
        assert as_of(gap, D("2020-02-01"), None, None)[0].value is None


class TestStitch:
    def test_une_el_periodo_cortado_por_el_borde_de_la_ventana(self) -> None:
        a = [per("2019-10-01", "2020-01-23", "2020-03-31", 15154.2)]
        b = [
            per("2019-10-01", "2020-04-01", "2020-04-15", 15154.2),
            per("2019-10-01", "2020-04-16", "2020-06-30", 15147.1),
        ]
        out = stitch(a, b, boundary=D("2020-04-01"))
        assert out == [
            per("2019-10-01", "2020-01-23", "2020-04-15", 15154.2),
            per("2019-10-01", "2020-04-16", "2020-06-30", 15147.1),
        ]

    def test_revision_justo_en_el_borde_quedan_dos_periodos(self) -> None:
        a = [per("2019-10-01", "2020-01-23", "2020-04-15", 15154.2)]
        b = [per("2019-10-01", "2020-04-16", "2020-06-30", 15147.1)]
        out = stitch(a, b, boundary=D("2020-04-16"))
        assert [p.value for p in out] == [15154.2, 15147.1]
        assert out[0].realtime_end == D("2020-04-15")

    def test_incremental_contra_la_db_cierra_el_periodo_abierto(self) -> None:
        # En la DB el período vigente tiene fin abierto; la corrida nueva lo
        # trae recortado en `since` con el mismo valor y luego una revisión.
        db = [per("2026-07-01", "2026-08-25", OPEN_END.isoformat(), 23218.0)]
        new = [
            per("2026-07-01", "2026-09-15", "2026-09-21", 23218.0),
            per("2026-07-01", "2026-09-22", OPEN_END.isoformat(), 23220.5),
        ]
        out = stitch(db, new, boundary=D("2026-09-15"))
        assert out == [
            per("2026-07-01", "2026-08-25", "2026-09-21", 23218.0),
            per("2026-07-01", "2026-09-22", OPEN_END.isoformat(), 23220.5),
        ]

    def test_faltantes_iguales_se_unen(self) -> None:
        a = [per("2020-01-01", "2020-01-01", "2020-01-31", None)]
        b = [per("2020-01-01", "2020-02-01", OPEN_END.isoformat(), None)]
        assert len(stitch(a, b, boundary=D("2020-02-01"))) == 1

    def test_observacion_nueva_solo_en_el_tramo_posterior(self) -> None:
        b = [per("2020-05-01", "2020-06-10", OPEN_END.isoformat(), 1.0)]
        assert stitch([], b, boundary=D("2020-06-01")) == b


class TestFirstReleaseLag:
    def test_mide_solo_lo_publicado_despues_de_la_primera_vintage(self) -> None:
        first = D("2011-04-06")
        periods = [
            per("2008-07-11", "2011-04-06", OPEN_END.isoformat(), 1.0),  # no informa
            per("2026-09-15", "2026-09-21", OPEN_END.isoformat(), 1.0),  # 6 días
            per("2026-09-18", "2026-09-21", OPEN_END.isoformat(), 1.0),  # 3 días
        ]
        assert first_release_lag_days(periods, first) == 6

    def test_sin_publicaciones_posteriores_no_hay_rezago(self) -> None:
        first = D("2011-04-06")
        assert (
            first_release_lag_days(
                [per("2008-07-11", "2011-04-06", OPEN_END.isoformat(), 1.0)], first
            )
            is None
        )

    def test_usa_la_primera_aparicion_no_las_revisiones(self) -> None:
        first = D("2019-01-01")
        periods = [
            per("2019-11-01", "2019-12-17", OPEN_END.isoformat(), 15326.6),
            per("2019-12-01", "2020-01-16", "2020-01-22", 15401.8),
            per("2019-12-01", "2020-01-23", OPEN_END.isoformat(), 15317.8),
        ]
        assert first_release_lag_days(periods, first) == 46

    def test_un_relleno_de_historia_no_es_una_publicacion(self) -> None:
        # Caso real: DGS10 extendio su historia hacia 1962 en una vintage
        # posterior. Medido como publicacion daba 16.058 dias de "rezago".
        first = D("2005-06-28")
        periods = [
            per("2005-06-24", "2005-06-28", OPEN_END.isoformat(), 4.0),
            per("2005-06-27", "2005-06-29", OPEN_END.isoformat(), 4.0),  # 2 d
            per("1962-01-02", "2006-03-01", OPEN_END.isoformat(), 4.06),  # relleno
            per("2006-02-27", "2006-03-01", OPEN_END.isoformat(), 4.6),  # 2 d
        ]
        assert first_release_lag_days(periods, first) == 2
