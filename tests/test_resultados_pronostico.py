"""Visualización 4.4: alcance de lectura, huecos, bloques explícitos y accesibilidad."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from pred_platform.app.charts import champion_forecast_svg
from pred_platform.app.resultados_m3 import M3Decision, decision
from pred_platform.app.resultados_pronostico import champion_forecast
from pred_platform.contract import models, models_v2
from pred_platform.contract.errors import ArtifactMissing
from pred_platform.data.dal_m3 import M3DalSource
from pred_platform.data.fixtures import FixtureRepository
from pred_platform.data.repository import M3DateRange, M3ReserveBlocks
from tests.test_resultados import _client

RUN = "m3-real-02"
SKU = "SKU-VALIDADO"
BLOCKS = M3ReserveBlocks(
    M3DateRange("2026-06-29", "2026-06-29", 1),
    M3DateRange("2026-06-30", "2026-06-30", 1),
    M3DateRange("2026-07-01", "2026-07-02", 2),
    M3DateRange("2026-06-30", "2026-07-01", 2),
    M3DateRange("2026-07-02", "2026-07-04", 3),
)

NS = {"svg": "http://www.w3.org/2000/svg"}


class SeriesRepository(FixtureRepository):
    """Publicación controlada con páginas pequeñas y valores ausentes independientes."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.blocks: M3ReserveBlocks | None = BLOCKS
        self.family = "classical"
        self.candidate: str | None = f"{SKU}/classical/sarima"
        self.observed_values: list[float | None] = [10, None, 0, 14]
        self.forecast_values: list[float | None] = [11, 9, None, 13]
        self.issue: str | None = None

    def get_sku_selection(
        self, run_id: str, sku_id: str, *, include_windows: bool = False
    ) -> models.SkuSelection:
        base = super().get_sku_selection("1", "FIL-0001", include_windows=include_windows)
        return base.model_copy(update={"run_id": "m2-ejemplo", "sku_id": sku_id})

    def get_m3_evidence(self, run_id: str, **kwargs: Any) -> models_v2.M3Evidence:
        doc = super().get_m3_evidence(RUN if run_id == "m3-real-01" else run_id, **kwargs)
        items = []
        for row in doc.items:
            # Otro candidato tiene menor r: no debe reemplazar al campeón publicado.
            other = row.modelos_evaluados[0].model_copy(
                update={"candidato_id": "otro/candidato", "razon_sn": 0.01}
            )
            items.append(
                row.model_copy(
                    update={
                        "candidato_campeon": self.candidate,
                        "familia_campeona": self.family,
                        "modelos_evaluados": [other, *row.modelos_evaluados],
                    }
                )
            )
        return doc.model_copy(update={"items": items})

    def get_m3_observations(self, run_id: str, **kwargs: Any) -> models_v2.M3Observations:
        self.calls.append(("observations", run_id, kwargs))
        if self.issue == "observations_error":
            raise ArtifactMissing("Sin observaciones")
        doc = super().get_m3_observations(RUN, sku=SKU)
        rows = [
            doc.items[0].model_copy(update={"fecha": f"2026-07-0{i}", "valor": value})
            for i, value in enumerate(self.observed_values, 1)
        ]
        page = kwargs.get("page", 1)
        availability = doc.availability
        if self.issue == "observations_unavailable" or (
            self.issue == "observations_later" and page == 2
        ):
            availability = availability.model_copy(update={"status": "unavailable"})
        return doc.model_copy(
            update={
                "items": rows[page - 1 : page],
                "page": models_v2.Page(number=page, size=1, total=len(rows)),
                "availability": availability,
            }
        )

    def get_m3_forecasts(self, run_id: str, **kwargs: Any) -> models_v2.M3Forecasts:
        self.calls.append(("forecasts", run_id, kwargs))
        if self.issue == "forecasts_error":
            raise ArtifactMissing("Sin pronósticos")
        doc = super().get_m3_forecasts(RUN, sku=SKU)
        rows = [
            doc.items[0].model_copy(
                update={
                    "fecha": f"2026-07-0{i}",
                    "valor": value,
                    "candidato_id": self.candidate,
                    "origen": f"2026-06-{26 + i}",
                }
            )
            for i, value in enumerate(self.forecast_values, 1)
        ]
        page = kwargs.get("page", 1)
        availability = doc.availability
        if self.issue == "forecasts_unavailable" or (self.issue == "forecasts_later" and page == 2):
            availability = availability.model_copy(update={"status": "unavailable"})
        return doc.model_copy(
            update={
                "items": rows[page - 1 : page],
                "page": models_v2.Page(number=page, size=1, total=len(rows)),
                "availability": availability,
            }
        )

    def get_m3_reserve_blocks(self, run_id: str) -> M3ReserveBlocks | None:
        self.calls.append(("blocks", run_id, {}))
        if self.issue == "blocks_error":
            raise ArtifactMissing("Sin límites")
        return self.blocks

    def get_m3_metrics(self, run_id: str, **kwargs: Any) -> models_v2.M3Metrics:
        raise AssertionError("El gráfico no necesita métricas ni clasificaciones")


def _series(repo: SeriesRepository):
    return champion_forecast(repo, decision(repo, "m2-ejemplo", [SKU], RUN), SKU)


def _svg(html: str) -> ET.Element:
    found = re.search(r'<svg role="img".*?</svg>', html, re.S)
    assert found
    return ET.fromstring(found.group())


def test_consulta_corrida_sku_candidato_publicado_h1_y_todas_las_paginas() -> None:
    repo = SeriesRepository()
    series = _series(repo)
    assert series.problem is None and series.candidate == repo.candidate
    for operation in ("observations", "forecasts"):
        calls = [(run, args) for op, run, args in repo.calls if op == operation]
        assert [args["page"] for _, args in calls] == [1, 2, 3, 4]
        for run, args in calls:
            assert run == RUN and args["sku"] == SKU and args["size"] == 500
            if operation == "forecasts":
                assert args["candidato_id"] == repo.candidate and args["h"] == 1
    assert [call for call in repo.calls if call[0] == "blocks"] == [("blocks", RUN, {})]
    assert [p.valor for p in series.observed] == [10, None, 0, 14]
    assert [p.valor for p in series.forecast] == [11, 9, None, 13]


def test_la_corrida_anterior_pedida_en_la_pagina_no_cambia_por_la_mas_reciente() -> None:
    repo = SeriesRepository()
    client, _ = _client(repo=repo)
    page = client.get(f"/resultados/sku/{SKU}?m3=m3-real-01")
    assert page.status_code == 200 and '<svg role="img"' in page.text
    assert repo.calls and all(run == "m3-real-01" for _, run, _ in repo.calls)


def test_no_agrega_pronosticos_de_distintos_origenes_con_la_misma_fecha() -> None:
    class Overlap(SeriesRepository):
        def get_m3_forecasts(self, run_id: str, **kwargs: Any) -> models_v2.M3Forecasts:
            doc = super().get_m3_forecasts(run_id, **kwargs)
            return doc.model_copy(
                update={"items": [r.model_copy(update={"fecha": "2026-07-01"}) for r in doc.items]}
            )

    repo = Overlap()
    series = _series(repo)
    assert [p.valor for p in series.forecast] == repo.forecast_values
    assert [p.fecha for p in series.forecast] == ["2026-07-01"] * 4


def test_ceros_en_ambas_series_y_un_solo_punto_siguen_disponibles() -> None:
    repo = SeriesRepository()
    repo.observed_values = repo.forecast_values = [0.0]
    series = _series(repo)
    assert series.problem is None
    root = ET.fromstring(champion_forecast_svg(series))
    assert len(root.findall(".//svg:circle", NS)) == 1
    assert len(root.findall(".//svg:g/svg:rect", NS)) == 1


@pytest.mark.parametrize("family", ["classical", "ml", "dl", "foundation", "seasonal_naive"])
def test_la_pagina_respeta_cualquier_familia_y_no_elige_el_menor_r(family: str) -> None:
    repo = SeriesRepository()
    repo.family, repo.candidate = family, f"{SKU}/{family}/modelo-publicado"
    client, _ = _client(repo=repo)
    response = client.get(f"/resultados/sku/{SKU}?m3={RUN}")
    assert response.status_code == 200
    root = _svg(response.text)
    assert repo.candidate in (root.findtext("svg:desc", namespaces=NS) or "")
    assert "h=1" in response.text
    assert "otro/candidato" not in ET.tostring(root, encoding="unicode")
    assert (
        response.text.index('id="pronostico-campeon"')
        < response.text.index("Modelos evaluados en la reserva")
        < response.text.index('id="familias"')
    )


def test_los_nulos_cortan_ambas_lineas_y_el_cero_conserva_su_marca() -> None:
    root = ET.fromstring(champion_forecast_svg(_series(SeriesRepository())))
    for kind in ("observed", "forecast"):
        group = root.find(f"svg:g[@class='pred-forecast__{kind}']", NS)
        assert group is not None
        path = group.find("svg:path", NS)
        assert path is not None and path.attrib["d"].count("M") == 2
        assert path.attrib["d"].count("L") == 1
    observed = root.findall(".//svg:circle", NS)
    forecast = root.findall(".//svg:g[@class='pred-forecast__forecast']/svg:rect", NS)
    assert len(observed) == len(forecast) == 3
    zero = next(
        p for p in observed if p.findtext("svg:title", namespaces=NS) == "2026-07-03: 0,000"
    )
    assert float(zero.attrib["cy"]) == 286


@pytest.mark.parametrize(
    "issue,copy",
    [
        ("observations_error", "demanda observada"),
        ("observations_unavailable", "demanda observada"),
        ("observations_later", "demanda observada"),
        ("forecasts_error", "pronóstico publicado"),
        ("forecasts_unavailable", "pronóstico publicado"),
        ("forecasts_later", "pronóstico publicado"),
    ],
)
def test_evidencia_ausente_incluso_en_otra_pagina_no_da_500(issue: str, copy: str) -> None:
    repo = SeriesRepository()
    repo.issue = issue
    client, _ = _client(repo=repo)
    response = client.get(f"/resultados/sku/{SKU}?m3={RUN}")
    assert response.status_code == 200 and copy in response.text
    assert '<svg role="img"' not in response.text


@pytest.mark.parametrize("values", [[], [None, None]])
def test_sin_h1_utilizable_no_sustituye_horizonte(values: list[float | None]) -> None:
    repo = SeriesRepository()
    repo.forecast_values = values
    series = _series(repo)
    assert series.problem and "horizonte h=1" in series.problem
    assert champion_forecast_svg(series) == ""
    assert all(args["h"] == 1 for op, _, args in repo.calls if op == "forecasts")


def test_sin_observaciones_utilizables_no_dibuja() -> None:
    repo = SeriesRepository()
    repo.observed_values = [None]
    assert "No hay valores" in (_series(repo).problem or "")


def test_sin_veredicto_sin_candidato_y_excluidos_no_cargan_series() -> None:
    repo = SeriesRepository()
    assert champion_forecast(repo, M3Decision(), SKU).problem
    published = decision(repo, "m2-ejemplo", [SKU])
    assert champion_forecast(repo, replace(published, excluded={SKU: "excluido"}), SKU).problem
    repo.candidate = None
    client, _ = _client(repo=repo)
    page = client.get(f"/resultados/sku/{SKU}")
    assert page.status_code == 200 and "no publicó un candidato campeón" in page.text
    assert '<svg role="img"' not in page.text and all(op == "blocks" for op, _, _ in repo.calls)


@pytest.mark.parametrize("absent", [False, True])
def test_sin_metadatos_de_bloques_o_error_no_infiere_cortes(absent: bool) -> None:
    repo = SeriesRepository()
    if absent:
        repo.blocks = None
    else:
        repo.issue = "blocks_error"
    series = _series(repo)
    assert series.blocks is None and series.problem is None
    svg = champion_forecast_svg(series)
    assert 'class="pred-forecast__test"' not in svg
    client, _ = _client(repo=repo)
    page = client.get(f"/resultados/sku/{SKU}")
    assert "sin inferir sus límites" in page.text and '<svg role="img"' in page.text


def test_bloques_solo_dependen_del_valor_resuelto_no_de_reserva_h_o_t_star() -> None:
    repo = SeriesRepository()
    published = decision(repo, "m2-ejemplo", [SKU])
    # Los mismos límites se conservan ante reservas de otra longitud y otros datos metodológicos.
    for count, horizon, cut in ((1, 2, "2020-01-01"), (4, 99, "2030-12-31")):
        repo.observed_values = [10.0] * count
        verdict = published.verdicts[SKU].model_copy(
            update={
                "dm_horizonte": horizon,
                "n_ventanas": 333,
                "razon_sn": 0.4,
            }
        )
        changed = replace(published, t_star=cut, verdicts={SKU: verdict})
        series = champion_forecast(repo, changed, SKU)
        assert series.blocks is BLOCKS
    repo.blocks = None
    assert champion_forecast(repo, decision(repo, "m2-ejemplo", [SKU]), SKU).blocks is None
    root = ET.fromstring(champion_forecast_svg(_series(SeriesRepository())))
    test = root.find("svg:rect[@class='pred-forecast__test']/svg:title", NS)
    assert test is not None and test.text == "Prueba final: 2026-07-02 a 2026-07-04"


def test_fixture_separa_origenes_y_objetivos_y_dal_no_deduce_limites() -> None:
    repo = FixtureRepository()
    blocks = repo.get_m3_reserve_blocks(RUN)
    assert blocks is not None
    assert blocks.embargo_origins == M3DateRange("2026-06-30", "2026-06-30", 1)
    series = champion_forecast(repo, decision(repo, "m2-ejemplo", [SKU]), SKU)
    svg = champion_forecast_svg(series)
    assert "Selección: 2026-06-30 a 2026-07-01" in svg
    assert "Prueba final: 2026-07-02 a 2026-07-03" in svg
    assert 'class="pred-forecast__embargo"' not in svg
    assert repo.get_m3_reserve_blocks("desconocida") is None
    assert M3DalSource().get_m3_reserve_blocks(RUN) is None


def test_metadatos_compartidos_por_todos_los_sku_de_la_decision() -> None:
    repo = SeriesRepository()
    resolved = decision(repo, "m2-ejemplo", [SKU, "SKU-EXPLORATORIO"])
    champion_forecast(repo, resolved, SKU)
    champion_forecast(repo, resolved, "SKU-EXPLORATORIO")
    assert [call for call in repo.calls if call[0] == "blocks"] == [("blocks", RUN, {})]


def test_origenes_sobreviven_y_el_embargo_solo_se_informa_en_texto() -> None:
    repo = SeriesRepository()
    series = _series(repo)
    assert [point.origen for point in series.forecast] == [f"2026-06-{27 + i}" for i in range(4)]
    client, _ = _client(repo=repo)
    html = client.get(f"/resultados/sku/{SKU}").text
    assert "Orígenes de embargo" in html and "2026-06-30 a 2026-06-30" in html
    assert "Fecha objetivo" in html and "Origen" in html and "2026-06-27" in html
    assert 'class="pred-forecast__embargo"' not in html
    assert "ajuste BH dentro de cada categoría de demanda" in html
    assert "ajuste BH de toda la corrida" not in html


def test_svg_accesible_y_escape_seguro_de_identificadores() -> None:
    series = _series(SeriesRepository())
    special = '<script>alert("x")</script>&'
    series = replace(series, sku=special, candidate=special)
    svg = champion_forecast_svg(series)
    root = ET.fromstring(svg)
    assert root.attrib["role"] == "img"
    assert special in (root.findtext("svg:title", namespaces=NS) or "")
    desc = root.findtext("svg:desc", namespaces=NS) or ""
    for text in (
        special,
        "Demanda observada",
        "h=1",
        "Selección",
        "orígenes de embargo",
        "Prueba final",
    ):
        assert text in desc
    assert "<script>" not in svg and "tabindex" not in svg
    assert root.findall(".//svg:circle", NS) and root.findall(".//svg:g/svg:rect", NS)
    client, _ = _client(repo=SeriesRepository())
    page = client.get(f"/resultados/sku/{SKU}").text
    assert "2026-07-01 al 2026-07-04" in page
    assert "continua" in desc and "discontinua" in desc
    assert "Los huecos indican valores ausentes" in page


def test_origenes_no_mueven_bandas_objetivo() -> None:
    series = _series(SeriesRepository())
    other = replace(
        series,
        blocks=replace(
            BLOCKS,
            embargo_origins=M3DateRange("1900-01-01", "2099-12-31", 777),
            selection_origins=M3DateRange(None, None, 0),
        ),
    )
    assert champion_forecast_svg(other) == champion_forecast_svg(series)


def test_bandas_svg_conservan_fechas_objetivo_ante_cambios_metodologicos() -> None:
    repo = SeriesRepository()
    resolved = decision(repo, "m2-ejemplo", [SKU])

    def bands(svg: str) -> list[tuple[dict[str, str], str | None]]:
        root = ET.fromstring(svg)
        return [
            (dict(rect.attrib), rect.findtext("svg:title", namespaces=NS))
            for rect in root.findall("svg:rect", NS)
        ]

    expected = bands(champion_forecast_svg(champion_forecast(repo, resolved, SKU)))
    assert [title for _, title in expected] == [
        "Selección: 2026-06-30 a 2026-07-01",
        "Prueba final: 2026-07-02 a 2026-07-04",
    ]
    for length, horizon, cut, ratio in (
        (1, 2, "2000-01-01", 0.4),
        (4, 99, "2099-12-31", 0.99),
    ):
        repo.observed_values = [10.0] * length
        verdict = resolved.verdicts[SKU].model_copy(
            update={
                "dm_horizonte": horizon,
                "n_ventanas": length,
                "razon_sn": ratio,
            }
        )
        changed = replace(
            resolved,
            t_star=cut,
            verdicts={SKU: verdict},
            blocks=replace(
                BLOCKS,
                selection_origins=M3DateRange("1999-01-01", "1999-02-01", 32),
                embargo_origins=M3DateRange("2000-01-01", "2000-01-02", 2),
                test_origins=M3DateRange("2099-01-01", "2099-01-01", 1),
            ),
        )
        svg = champion_forecast_svg(champion_forecast(repo, changed, SKU))
        assert bands(svg) == expected
        assert 'class="pred-forecast__embargo"' not in svg
