"""Pestaña «Consulta» de /resultados (RF-CON-05): filtros combinados sobre la evidencia de M3.

La lista sale de ``get_m3_evidence``: la clase, la familia campeona y el veredicto se delegan en el
repositorio, que filtra, ordena y pagina. La vista no los aplica ni los recalcula, y un valor
desconocido se muestra como aviso en vez de ignorarse.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from pred_platform.app.consulta import ConsultaQuery, parse_consulta_query
from pred_platform.app.main import create_app
from pred_platform.config import Settings
from pred_platform.contract.errors import InvalidQuery
from pred_platform.data.query import DEFAULT_SIZE
from tests.test_resultados import _notice, _publish_for_the_run, _text
from tests.test_seleccion import _run
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

M3_RUN = "m3-real-1"
# FIL-0003 se lanza después del corte: no tiene veredicto, solo cuenta en el embudo de la corrida.
VERDICTS = {
    "FIL-0001": ("smooth", "VALIDADO"),
    "FIL-0002": ("intermittent", "EXPLORATORIO"),
    "FIL-0004": ("lumpy", "EVIDENCIA_INSUFICIENTE"),
}


def _client(env: Env, repo: Any = None) -> TestClient:
    app = create_app(
        Settings(data_root=env.settings.data_root, db_path=env.db, data_source="dal", worker="off")
    )
    app.state.repository = repo or env.repo()
    return TestClient(app)


def _published(env: Env, monkeypatch: pytest.MonkeyPatch, **options: Any) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=VERDICTS, **options)


def _row(html: str, sku: str) -> str:
    """El texto de la fila de un SKU, como lo lee quien mira la tabla."""
    found = re.search(rf"<tr>\s*<th[^>]*>\s*<a[^>]*>{sku}</a>.*?</tr>", html, re.S)
    assert found is not None, f"{sku} no está en la tabla"
    return _text(found.group(0))


# ------------------------------------------------------------------------ la consulta HTTP
def test_the_query_string_keeps_filters_and_leaves_out_the_defaults() -> None:
    assert ConsultaQuery().query_string() == "vista=sku"
    query = ConsultaQuery(
        m3="m3-1",
        sku_class="lumpy",
        familia_campeona="classical",
        veredicto="VALIDADO",
        q="FIL",
        sort="razon_sn",
        descending=True,
        page=3,
        size=10,
    )
    assert query.query_string() == (
        "vista=sku&m3=m3-1&sku_class=lumpy&familia_campeona=classical&veredicto=VALIDADO"
        "&q=FIL&sort=razon_sn&descending=1&page=3&size=10"
    )
    # Cambiar un filtro no pierde la corrida M3 resuelta.
    assert "m3=m3-1" in query.query_string(page="", veredicto="")


def test_the_http_query_is_read_without_judging_its_values() -> None:
    query = parse_consulta_query(
        {"sku_class": "lumpy", "veredicto": "mantiene", "descending": "on", "page": "2"}
    )
    # «mantiene» no existe: lo rechaza el repositorio, no el analizador.
    assert (query.sku_class, query.veredicto, query.descending, query.page) == (
        "lumpy",
        "mantiene",
        True,
        2,
    )
    assert (query.sort, query.size, query.ejecucion) == ("sku", DEFAULT_SIZE, "latest")
    with pytest.raises(InvalidQuery):
        parse_consulta_query({"page": "dos"})


# ------------------------------------------------------------------------------ la pestaña
def test_the_tab_lists_the_published_verdicts_and_keeps_the_run_totals(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, late=("FIL-0003",))
    response = _client(env).get("/resultados?vista=sku")
    html, text = response.text, _text(response.text)

    assert response.status_code == 200
    assert 'aria-current="page">Veredictos<' in html
    for sku in VERDICTS:
        assert f'href="/resultados/sku/{sku}?' in html
    # FIL-0003 no tiene veredicto: no es una fila, solo cuenta en el embudo de la corrida.
    assert 'href="/resultados/sku/FIL-0003' not in html
    assert "3 SKU coinciden con la consulta." in text
    assert (
        "En toda la ejecución, 3 de 4 productos tienen veredicto; ese conteo no cambia con los "
        "filtros." in text
    )
    for label in ("Validado", "Exploratorio", "Evidencia insuficiente"):
        assert f'pred-status__label">{label}' in html
    # Each verdict carries its one-line explanation, and a legend explains all five once.
    assert (
        'title="Se equivoca menos que la línea base, con datos reales y evidencia suficiente."'
        in html
    )
    assert 'class="pred-legend"' in html
    assert "Evaluación final Publicada ·" in text


def test_the_three_filters_sort_and_page_reach_the_repository_together(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, late=("FIL-0003",))
    repo = env.repo()
    calls: list[tuple[str, dict[str, Any]]] = []
    original = repo.get_m3_evidence

    def spy(run_id: str, **kwargs: Any) -> Any:
        calls.append((run_id, kwargs))
        return original(run_id, **kwargs)

    monkeypatch.setattr(repo, "get_m3_evidence", spy)
    response = _client(env, repo).get(
        "/resultados?vista=sku&sku_class=lumpy&familia_campeona=classical"
        "&veredicto=EVIDENCIA_INSUFICIENTE&q=FIL&sort=razon_sn&descending=1"
    )
    text = _text(response.text)

    # Una sola lectura, con todo delegado: la vista no filtra ni ordena por su cuenta.
    assert calls == [
        (
            M3_RUN,
            {
                "q": "FIL",
                "sku_class": "lumpy",
                "veredicto": "EVIDENCIA_INSUFICIENTE",
                "familia_campeona": "classical",
                "sort": "razon_sn",
                "descending": True,
                "page": 1,
                "size": DEFAULT_SIZE,
            },
        )
    ]
    assert "1 SKU coinciden con la consulta." in text
    assert 'href="/resultados/sku/FIL-0004?' in response.text
    assert 'href="/resultados/sku/FIL-0001?' not in response.text
    # El embudo sigue siendo el de toda la corrida, aunque la consulta traiga un solo SKU.
    assert (
        "En toda la ejecución, 3 de 4 productos tienen veredicto; ese conteo no cambia con los "
        "filtros." in text
    )
    # Los controles conservan lo elegido.
    assert '<option value="lumpy" selected>' in response.text
    assert '<option value="classical" selected>' in response.text
    assert '<option value="EVIDENCIA_INSUFICIENTE" selected>' in response.text


@pytest.mark.parametrize(
    ("filters", "skus"),
    [
        ("sku_class=smooth&veredicto=VALIDADO&familia_campeona=classical", ["FIL-0001"]),
        ("sku_class=intermittent&familia_campeona=classical", ["FIL-0002"]),
        ("veredicto=EXPLORATORIO", ["FIL-0002"]),
        ("sku_class=smooth&veredicto=EXPLORATORIO", []),
        ("familia_campeona=ml", []),
    ],
)
def test_the_filters_combine_over_the_real_dal(
    env: Env, monkeypatch: pytest.MonkeyPatch, filters: str, skus: list[str]
) -> None:
    _published(env, monkeypatch)
    html = _client(env).get(f"/resultados?vista=sku&{filters}").text

    assert re.findall(r'href="/resultados/sku/(FIL-\d+)\?', html) == skus
    assert f"{len(skus)} SKU coinciden con la consulta." in _text(html)
    if not skus:
        assert "Sin coincidencias" in html


def test_an_unknown_filter_value_is_shown_not_ignored(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    response = _client(env).get("/resultados?vista=sku&veredicto=mantiene")
    html, text = response.text, _text(response.text)

    assert response.status_code == 200
    assert "La consulta no es válida." in text and "mantiene" in text
    # No se muestra una lista que parezca filtrada ni un conteo inventado.
    assert 'href="/resultados/sku/' not in html and "coinciden con la consulta" not in text
    # La corrida y el formulario siguen: quien se equivocó puede corregir.
    assert 'id="filtros-consulta"' in html and "Evaluación final Publicada ·" in text


def test_a_page_that_is_not_a_number_is_a_warning(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    html = _client(env).get("/resultados?vista=sku&page=dos").text
    assert "El parámetro página debe ser un número entero; llegó «dos»." in _notice(html)


def test_links_and_pages_keep_the_resolved_m3_run_not_the_newest(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    html = _client(env).get("/resultados?vista=sku&sku_class=intermittent&size=1").text

    assert '<input type="hidden" name="m3" value="m3-real-1">' in html
    # El detalle vuelve a la misma consulta: pestaña, ejecución, corrida M3 y filtros.
    assert (
        'href="/resultados/sku/FIL-0002?ejecucion=1&amp;m3=m3-real-1&amp;volver='
        "vista%3Dsku%26ejecucion%3D1%26m3%3Dm3-real-1%26sku_class%3Dintermittent%26size%3D1"
    ) in html

    paged = _client(env).get("/resultados?vista=sku&size=1").text
    assert "Página 1 de 3" in _text(paged)
    paged_link = (
        'href="/resultados?vista=sku&amp;ejecucion=1&amp;m3=m3-real-1&amp;page=2&amp;size=1"'
    )
    assert paged_link in paged
    assert "Anterior" not in paged


def test_the_detail_opened_from_the_query_goes_back_to_it(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    client = _client(env)
    listing = client.get("/resultados?vista=sku&sku_class=intermittent").text
    link = re.search(r'href="(/resultados/sku/FIL-0002\?[^"]+)"', listing)
    assert link is not None

    detail = client.get(link.group(1).replace("&amp;", "&"))
    # La ruta de migas: «SKU» vuelve a la lista con sus filtros; «Resultados», al resumen.
    back = re.search(r'<li><a href="([^"]*)">SKU</a></li>', detail.text)
    assert detail.status_code == 200 and back is not None
    assert unquote(back.group(1)).replace("&amp;", "&") == (
        "/resultados?vista=sku&ejecucion=1&m3=m3-real-1&sku_class=intermittent"
    )
    assert '<li><a href="/resultados?ejecucion=1&amp;m3=m3-real-1">Resultados</a></li>' in (
        detail.text
    )


def test_a_verdict_is_not_called_significant_and_a_missing_test_shows_its_cause(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    html = _client(env).get("/resultados?vista=sku").text

    # Validado es descriptivo: puede no ser significativo, y se dice por separado.
    validated = _row(html, "FIL-0001")
    assert "Validado" in validated and "p ajustado 0,080" in validated
    assert "No significativa" in validated
    # Sin prueba, la causa de M3 en palabras, con el código publicado aparte; nunca un p-valor.
    missing = _row(html, "FIL-0004")
    assert "Sin prueba: el veredicto es «Evidencia insuficiente», que no lleva prueba" in missing
    assert 'title="Causa publicada: veredicto:EVIDENCIA_INSUFICIENTE"' in html
    assert "p ajustado" not in missing.split("Ver prueba")[0]


def test_a_validated_verdict_whose_test_rejects_after_bh_is_called_significant(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, significant=("FIL-0001",))
    row = _row(_client(env).get("/resultados?vista=sku").text, "FIL-0001")

    # Significativa y a favor de la campeona: el estadístico HLN-DM es negativo.
    assert "Validado" in row and "Significativa, a favor de la campeona p ajustado 0,020" in row
    assert "No significativa" not in row.split("Ver prueba")[0]


def test_an_unpublished_run_explains_why_there_is_nothing_to_query(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, estado="EVALUADA")
    html = _client(env).get("/resultados?vista=sku&lista=veredictos").text
    text = _text(html)

    assert "Esta ejecución todavía no tiene una evaluación publicada." in text
    assert "La evaluación más reciente está evaluada, sin publicar." in text
    assert 'href="/resultados/sku/' not in html and 'id="filtros-consulta"' not in html
    # Without a published run, the SKU tab opens on what M2 left, with the same explanation.
    default = _client(env).get("/resultados?vista=sku").text
    assert 'aria-current="page">Resultados por familia</a>' in default
    assert "La evaluación más reciente está evaluada, sin publicar." in _text(default)


def test_an_m3_run_of_another_execution_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    _publish_for_the_run(env, run_id="m3-ajena", run_id_m2="r-otra")
    html = _client(env).get("/resultados?vista=sku&m3=m3-ajena").text

    assert "La evaluación pedida no corresponde a esta ejecución." in _text(html)
    assert 'href="/resultados/sku/' not in html


def test_a_publication_without_its_metadata_gives_no_rows(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch, publish_m4=False)
    html = _client(env).get("/resultados?vista=sku").text

    # No significa cero exclusiones: la corrida se explica y no se inventa una lista.
    assert "La evaluación publicada no trae la lista de productos excluidos." in _text(html)
    assert 'href="/resultados/sku/' not in html


def test_without_an_m3_evaluation_the_tab_says_so(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    text = _text(_client(env).get("/resultados?vista=sku").text)
    assert "Esta ejecución todavía no tiene evaluación final." in text


def test_the_selection_tab_is_untouched_by_the_query_tab(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    html = _client(env).get("/resultados?vista=sku&lista=familias").text

    assert 'aria-current="page">Resultados por familia<' in html
    # «Resultados por familia» sigue siendo la selección de M2: sus filtros no cambian.
    assert 'name="estado"' in html and 'name="family"' in html
    assert 'name="familia_campeona"' not in html and 'name="veredicto"' not in html
    assert 'href="/resultados?vista=sku&amp;ejecucion=1&amp;m3=m3-real-1"' in html


def test_the_old_consulta_link_still_opens_the_verdict_list(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(env, monkeypatch)
    html = _client(env).get("/resultados?vista=consulta").text

    assert 'id="filtros-consulta"' in html and 'aria-current="page">Veredictos<' in html
