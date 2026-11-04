"""Exportación a CSV de los resultados de M3 (RF-CON-06, tarea A2 de la sección 4.5).

El CSV es la consulta de la pestaña «Consulta» sin el límite de página: mismas filas, mismo orden,
con la corrida M3 y la ingesta de cada una. No recalcula nada y un error nunca deja un archivo
parcial.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

import pytest

from pred_platform.app.exportar import (
    EXCLUSION_COLUMNS,
    VERDICT_COLUMNS,
    _cell,
    _csv,
    filename,
)
from tests.m3_publicada import TWO_FAMILIES, TWO_FAMILIES_CAUSE
from tests.test_consulta import M3_RUN, VERDICTS, _client, _published
from tests.test_resultados import _publish_for_the_run, _text
from tests.test_seleccion import _run
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

EXPORT = "/resultados/exportar.csv"
EXCLUSIONS = "/resultados/exclusiones.csv"


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


# ------------------------------------------------------------------------------ el formato
def test_the_headers_are_the_published_columns(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _published(env, monkeypatch, late=("FIL-0003",))
    client = _client(env)

    verdicts = client.get(EXPORT)
    assert verdicts.status_code == 200
    assert verdicts.text.splitlines()[0].split(",") == list(VERDICT_COLUMNS)
    assert client.get(EXCLUSIONS).text.splitlines()[0].split(",") == list(EXCLUSION_COLUMNS)
    # Sin columnas de sMAPE ni la lista de modelos: lo que el contrato v2 no consume.
    assert "smape" not in VERDICT_COLUMNS and "modelos_evaluados" not in VERDICT_COLUMNS


def test_a_download_has_the_csv_type_and_a_name_with_the_resolved_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, late=("FIL-0003",))
    client = _client(env)

    verdicts, exclusions = client.get(EXPORT), client.get(EXCLUSIONS)
    assert verdicts.headers["content-type"] == "text/csv; charset=utf-8"
    assert verdicts.headers["content-disposition"] == (
        f'attachment; filename="pred-resultados-{M3_RUN}.csv"'
    )
    assert exclusions.headers["content-disposition"] == (
        f'attachment; filename="pred-exclusiones-{M3_RUN}.csv"'
    )
    assert not verdicts.content.startswith(b"\xef\xbb\xbf")  # UTF-8 sin BOM


def test_a_run_id_cannot_break_the_header() -> None:
    assert filename("resultados", 'a/b"c d\r\n') == "pred-resultados-a_b_c_d__.csv"
    assert filename("exclusiones", "m3-real-1") == "pred-exclusiones-m3-real-1.csv"


def test_cells_keep_nulls_booleans_numbers_and_awkward_text_readable() -> None:
    assert [_cell(v) for v in (None, True, False, 0.08, 1.0, 40, "VALIDADO")] == [
        "",
        "true",
        "false",
        "0.08",
        "1.0",
        "40",
        "VALIDADO",
    ]
    awkward = ["con, coma", 'con "comillas"', "salto\nde línea"]
    assert list(csv.reader(io.StringIO(_csv(("a", "b", "c"), [awkward]))))[1] == awkward


# ------------------------------------------------------------------- lo mismo que la vista
def test_the_csv_is_the_query_without_the_page_limit(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, late=("FIL-0003",))
    client = _client(env)
    query = "sku_class=intermittent&familia_campeona=classical&sort=razon_sn&descending=1"

    exported = _rows(client.get(f"{EXPORT}?{query}").text)
    shown = env.repo().get_m3_evidence(
        M3_RUN,
        sku_class="intermittent",
        familia_campeona="classical",
        sort="razon_sn",
        descending=True,
    )
    assert [r["sku"] for r in exported] == [item.sku for item in shown.items] == ["FIL-0002"]
    assert [r["veredicto"] for r in exported] == [item.veredicto for item in shown.items]
    # La página muestra el mismo total que el archivo tiene filas.
    page = _text(client.get(f"/resultados?vista=consulta&{query}").text)
    assert f"{len(exported)} SKU coinciden con la consulta." in page


def test_every_row_keeps_its_run_ingestion_and_sku(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    rows = _rows(_client(env).get(EXPORT).text)

    assert [r["sku"] for r in rows] == list(VERDICTS)
    for row in rows:
        assert row["run_id"] == M3_RUN and row["run_id_m2"]
        assert row["ingesta_sha256"] == env.ingest_id
        assert re.fullmatch(r"[0-9a-f]{64}", row["ingesta_sha256"])


def test_the_five_labels_and_the_significance_columns_stay_apart(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(
        env,
        verdicts={
            "FIL-0001": ("smooth", "VALIDADO"),
            "FIL-0002": ("intermittent", "EXPLORATORIO"),
            "FIL-0004": ("lumpy", "EVIDENCIA_INSUFICIENTE"),
            "FIL-0005": ("erratic", "NO_EVALUABLE"),
            "FIL-0006": ("smooth", "FALLO_TECNICO"),
        },
    )
    by_sku = {r["sku"]: r for r in _rows(_client(env).get(EXPORT).text)}

    assert {s: r["veredicto"] for s, r in by_sku.items()} == {
        "FIL-0001": "VALIDADO",
        "FIL-0002": "EXPLORATORIO",
        "FIL-0004": "EVIDENCIA_INSUFICIENTE",
        "FIL-0005": "NO_EVALUABLE",
        "FIL-0006": "FALLO_TECNICO",
    }
    # VALIDADO es descriptivo: se exporta con su prueba y sin llamarlo significativo.
    validated = by_sku["FIL-0001"]
    assert (validated["dm_significativa"], validated["dm_p_ajustado"]) == ("false", "0.08")
    assert validated["dm_causa"] == "" and validated["razon_sn"] == "0.8"
    assert (validated["datos_sinteticos"], validated["origen_declarado"]) == ("false", "true")
    # Sin prueba: los resultados inferenciales vacíos y la causa literal, no un p-valor inventado.
    untested = by_sku["FIL-0004"]
    assert untested["dm_causa"] == "veredicto:EVIDENCIA_INSUFICIENTE"
    for column in ("dm_estadistico", "dm_p_valor", "dm_p_ajustado", "dm_alfa", "dm_significativa"):
        assert untested[column] == ""
    # Un fallo técnico no tiene candidato campeón: celda vacía, no un texto inventado.
    assert by_sku["FIL-0006"]["candidato_campeon"] == ""


def test_more_than_one_page_of_skus_is_exported_whole(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    many = {f"FIL-{n:04d}": ("smooth", "VALIDADO") for n in range(1, 621)}
    _publish_for_the_run(env, verdicts=many, late=())
    client = _client(env)

    rows = _rows(client.get(EXPORT).text)
    skus = [r["sku"] for r in rows]
    assert len(rows) == 620 and skus == sorted(set(skus))
    # La vista pagina de a 50; el archivo no.
    assert "620 SKU coinciden con la consulta." in _text(
        client.get("/resultados?vista=consulta").text
    )


def test_a_total_that_changes_between_pages_gives_no_partial_file(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    many = {f"FIL-{n:04d}": ("smooth", "VALIDADO") for n in range(1, 621)}
    _publish_for_the_run(env, verdicts=many, late=())
    repo = env.repo()
    original = repo.get_m3_evidence

    def shifting(run_id: str, **kwargs: Any) -> Any:
        found = original(run_id, **kwargs)
        if kwargs.get("page") == 2:
            return found.model_copy(
                update={"page": found.page.model_copy(update={"total": found.page.total + 1})}
            )
        return found

    monkeypatch.setattr(repo, "get_m3_evidence", shifting)
    response = _client(env, repo).get(EXPORT)

    assert response.status_code == 409
    assert "La evidencia cambió durante la exportación." in response.text
    assert "run_id" not in response.text and not response.headers["content-type"].startswith(
        "text/csv"
    )


# ----------------------------------------------------------------------------- exclusiones
def test_the_exclusions_csv_lists_both_kinds_with_the_cut_class_code_and_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(
        env,
        verdicts={"FIL-0001": ("smooth", "VALIDADO")},
        late=("FIL-0003",),
        eligibility=(("FIL-0004", "lumpy", TWO_FAMILIES_CAUSE),),
    )
    # Los filtros de veredicto no alcanzan a las exclusiones: son las de toda la corrida.
    rows = _rows(_client(env).get(f"{EXCLUSIONS}?veredicto=EXPLORATORIO&q=zzz").text)

    by_sku = {r["sku"]: r for r in rows}
    assert set(by_sku) == {"FIL-0003", "FIL-0004"}
    late, eligibility = by_sku["FIL-0003"], by_sku["FIL-0004"]
    assert (late["tipo"], late["sku_class"], late["codigo"]) == ("lanzamiento_tardio", "", "")
    assert late["causa"] == "lanzado despues del corte"
    assert (eligibility["tipo"], eligibility["sku_class"]) == ("elegibilidad", "lumpy")
    assert (eligibility["codigo"], eligibility["causa"]) == (TWO_FAMILIES, TWO_FAMILIES_CAUSE)
    assert late["t_star"] == eligibility["t_star"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", late["t_star"])
    for row in rows:
        assert (row["run_id"], row["ingesta_sha256"]) == (M3_RUN, env.ingest_id)


def test_a_run_without_exclusions_gives_the_header_alone(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, late=())
    response = _client(env).get(EXCLUSIONS)
    assert response.status_code == 200
    assert response.text.splitlines() == [",".join(EXCLUSION_COLUMNS)]


# -------------------------------------------------------------------------------- errores
@pytest.mark.parametrize("path", [EXPORT, EXCLUSIONS])
def test_an_unknown_filter_or_page_is_refused_not_ignored(
    env: Env, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    _published(env, monkeypatch)
    client = _client(env)

    bad_filter = client.get(f"{path}?veredicto=mantiene" if path == EXPORT else f"{path}?page=dos")
    assert bad_filter.status_code == 400
    assert "La consulta" in bad_filter.text
    assert not bad_filter.headers["content-type"].startswith("text/csv")


def test_an_unknown_filter_value_gets_the_repository_reason(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    response = _client(env).get(f"{EXPORT}?veredicto=mantiene")
    assert response.status_code == 400 and "mantiene" in response.text


def test_a_missing_execution_or_run_is_a_404(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _published(env, monkeypatch)
    client = _client(env)

    assert client.get(f"{EXPORT}?ejecucion=99").status_code == 404
    other = client.get(f"{EXPORT}?m3=m3-ajena")
    assert other.status_code == 404
    assert "La corrida M3 pedida no corresponde a esta ejecución." in other.text


def test_without_an_m3_evaluation_there_is_nothing_to_export(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    response = _client(env).get(EXPORT)
    assert response.status_code == 404
    assert "Esta ejecución todavía no tiene evaluación M3." in response.text


def test_a_run_that_is_not_published_is_a_conflict(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, estado="EVALUADA")
    response = _client(env).get(EXPORT)

    assert response.status_code == 409
    assert "Esta ejecución todavía no tiene una corrida M3 PUBLICADA." in response.text
    assert "run_id" not in response.text


def test_a_publication_without_its_metadata_is_a_conflict(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, publish_m4=False)
    for path in (EXPORT, EXCLUSIONS):
        response = _client(env).get(path)
        assert response.status_code == 409
        assert "La publicación M3 no tiene sus metadatos de exclusión." in response.text


# -------------------------------------------------------------------------- desde la pestaña
def test_the_query_tab_links_the_downloads_with_its_filters_and_the_resolved_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, late=("FIL-0003",))
    client = _client(env)
    html = client.get("/resultados?vista=sku&sku_class=intermittent&size=1&page=1").text

    export = "/resultados/exportar.csv?ejecucion=1&amp;m3=m3-real-1&amp;sku_class=intermittent"
    assert f'href="{export}"' in html
    assert 'href="/resultados/exclusiones.csv?ejecucion=1&amp;m3=m3-real-1"' in html
    # El enlace trae el conjunto de la consulta, no la página de una fila.
    followed = _rows(
        client.get("/resultados/exportar.csv?m3=m3-real-1&sku_class=intermittent").text
    )
    assert [r["sku"] for r in followed] == ["FIL-0002"]


def test_the_downloads_are_not_offered_when_there_is_nothing_to_export(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, estado="EVALUADA")
    html = _client(env).get("/resultados?vista=consulta").text
    assert "exportar.csv" not in html and "exclusiones.csv" not in html
