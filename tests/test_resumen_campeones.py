"""Distribución de campeones por familia en «Resumen» (RF-CON-08, tarea A4 de la sección 4.5).

La vista cuenta lo que M3 publicó en ``m3_campeones``: no elige campeones ni recalcula conteos. El
porcentaje es presentación de esas cifras sobre los SKU con veredicto del embudo, y si no cuadran
con el embudo, se dice.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from typing import Any

import pytest

from pred_platform.app.charts import champion_share_svg
from pred_platform.app.resultados_m3 import (
    CHAMPION_FAMILIES,
    champion_distribution,
    percent_text,
)
from pred_platform.contract import models_v2
from pred_platform.contract.errors import ArtifactMissing
from tests.m3_publicada import TWO_FAMILIES_CAUSE
from tests.test_consulta import M3_RUN, _client
from tests.test_resultados import _publish_for_the_run, _text
from tests.test_seleccion import _run
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

LABELS = {
    "classical": "Clásica",
    "ml": "Aprendizaje automático",
    "dl": "Aprendizaje profundo",
    "foundation": "Fundacional",
    "seasonal_naive": "Línea base estacional",
}
VERDICTS = {
    "FIL-0001": ("smooth", "VALIDADO"),
    "FIL-0002": ("intermittent", "EXPLORATORIO"),
    "FIL-0004": ("lumpy", "EVIDENCIA_INSUFICIENTE"),
    "FIL-0005": ("erratic", "VALIDADO"),
    "FIL-0006": ("smooth", "FALLO_TECNICO"),
    "FIL-0007": ("smooth", "NO_EVALUABLE"),
}
# El helper de publicación fija la familia clásica; estos SKU cambian de campeona para que la
# distribución tenga más de una. Sin candidato: lo permite el contrato y no contradice al modelo.
OTHER_CHAMPIONS = {"FIL-0002": "ml", "FIL-0004": "seasonal_naive"}


def _publish(env: Env, monkeypatch: pytest.MonkeyPatch, **options: Any) -> None:
    _run(env, monkeypatch)
    options.setdefault("late", ("FIL-0003",))
    options.setdefault("eligibility", (("FIL-0008", "lumpy", TWO_FAMILIES_CAUSE),))
    _publish_for_the_run(env, verdicts=VERDICTS, **options)
    with closing(sqlite3.connect(env.db)) as conn:
        for sku, family in OTHER_CHAMPIONS.items():
            conn.execute(
                "UPDATE m3_veredictos SET familia_campeona = ?, candidato_campeon = NULL "
                "WHERE sku = ?",
                (family, sku),
            )
        conn.commit()


def _page(env: Env, repo: Any = None, query: str = "vista=resumen") -> str:
    response = _client(env, repo).get(f"/resultados?{query}")
    assert response.status_code == 200
    return response.text


def _section(html: str, name: str) -> str:
    """El HTML de una sección del resumen, hasta la siguiente."""
    start = html.index(f'id="{name}"')
    end = html.find("<section", start)
    return html[start:] if end == -1 else html[start:end]


def _portfolio(html: str) -> dict[str, tuple[int, int, int, str]]:
    """familia -> (evaluados, no evaluados, SKU, porcentaje) de la tabla de cartera."""
    section = _section(html, "campeones-por-familia")
    found: dict[str, tuple[int, int, int, str]] = {}
    for family, label in LABELS.items():
        match = re.search(
            rf'<th scope="row" class="pred-nowrap"[^>]*>{label}</th>\s*'
            r'<td class="num">(\d+)</td>\s*<td class="num">(\d+)</td>\s*'
            r'<td class="num">(\d+)</td>\s*<td class="num">([^<]+)</td>',
            section,
        )
        assert match is not None, label
        found[family] = (int(match[1]), int(match[2]), int(match[3]), match[4].strip())
    return found


# ------------------------------------------------------------------------------ la tabla
def test_the_portfolio_table_is_what_m3_published_with_each_share(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(env, monkeypatch)
    html = _page(env)
    published = {
        row.familia: row
        for row in env.repo().get_m3_champions(M3_RUN).items
        if row.sku_class is None
    }

    table = _portfolio(html)
    for family, (evaluated, not_evaluated, skus, share) in table.items():
        row = published.get(family)
        assert (evaluated, not_evaluated, skus) == (
            (row.n_evaluados, row.n_no_evaluados, row.n_skus) if row else (0, 0, 0)
        ), family
        assert share == (percent_text(skus / 6) if skus else "0,0 %"), family
    assert table["classical"][:3] == (2, 2, 4)
    assert table["ml"][:3] == (1, 0, 1) and table["seasonal_naive"][:3] == (0, 1, 1)
    # Una familia sin SKU sale con ceros, para que la tabla sea comparable entre corridas.
    assert table["dl"][:3] == table["foundation"][:3] == (0, 0, 0)


def test_the_denominator_is_the_skus_with_a_verdict_and_the_funnel_keeps_the_rest(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(env, monkeypatch)
    html = _page(env)
    text = _text(html)
    section = _text(_section(html, "campeones-por-familia"))

    # 8 productos = 1 lanzado después del corte + 1 excluido por M2 + 6 con veredicto.
    assert "Productos de la ejecución 8" in text and "Con veredicto 6" in text
    assert "de los 6 SKU con veredicto" in section
    assert "Con veredicto 3 3 6 100,0 %" in section
    assert "Es evidencia sobre el historial reservado" in section
    # Todo el portafolio: los fallos técnicos y no evaluables cuentan como no evaluados.
    assert _portfolio(html)["classical"][1] == 2


def test_the_class_matrix_sums_to_each_family_of_the_portfolio(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(env, monkeypatch)
    html = _page(env)
    section = _section(html, "campeones-por-clase")

    rows = re.findall(
        r'<th scope="row" class="pred-nowrap"[^>]*>([^<]+)</th>'
        r'((?:\s*<td class="num(?: is-zero)?">\d+</td>){6})',
        section,
    )
    assert [name for name, _ in rows] == ["Suave", "Errática", "Intermitente", "Grumosa"]
    matrix = [[int(n) for n in re.findall(r"(\d+)</td>", cells)] for _, cells in rows]
    for index, family in enumerate(CHAMPION_FAMILIES):
        assert sum(row[index] for row in matrix) == _portfolio(html)[family][2], family
    # Cada fila cierra con los SKU de su clase.
    assert [row[-1] for row in matrix] == [3, 1, 1, 1]
    assert all(sum(row[:-1]) == row[-1] for row in matrix)


def test_the_chart_says_what_the_table_says(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _publish(env, monkeypatch)
    html = _page(env)
    start = html.index('<svg role="img"')
    svg = html[start : html.index("</svg>", start) + 6]

    assert 'role="img"' in svg and "<title>Distribución de campeones por familia</title>" in svg
    values = re.findall(r'class="pred-champion__value">([^<]+)</text>', svg)
    table = _portfolio(html)
    assert values == [f"{table[f][2]} SKU · {table[f][3]}" for f in CHAMPION_FAMILIES]
    # Una barra por familia con SKU; las de cero no se dibujan pero sí se rotulan.
    assert svg.count("<path") == sum(1 for f in CHAMPION_FAMILIES if table[f][2])
    for label in LABELS.values():
        assert f'class="pred-champion__label">{label}</text>' in svg


# ------------------------------------------------------------------------- coherencia y fallos
def test_figures_that_do_not_match_the_funnel_are_said_not_adjusted(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(env, monkeypatch)
    repo = env.repo()
    original = repo.get_m3_champions

    def inflated(run_id: str, **kwargs: Any) -> models_v2.M3Champions:
        found = original(run_id, **kwargs)
        items = list(found.items)
        index = next(i for i, row in enumerate(items) if row.sku_class is None)
        items[index] = items[index].model_copy(
            update={
                "n_skus": items[index].n_skus + 1,
                "n_evaluados": items[index].n_evaluados + 1,
            }
        )
        return found.model_copy(update={"items": items})

    monkeypatch.setattr(repo, "get_m3_champions", inflated)
    html = _page(env, repo)
    text = _text(html)

    assert "Las cifras no cuadran." in text
    assert "Las familias suman 7 productos y el embudo publicó 6 con veredicto." in text
    # La cifra publicada se muestra tal cual, sin corregirla.
    assert _portfolio(html)["classical"][2] == 5
    assert _text(html).count("Las cifras no cuadran.") == 1


def test_consistent_figures_carry_no_warning(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _publish(env, monkeypatch)
    assert "Las cifras no cuadran" not in _text(_page(env))


def test_a_failed_champions_read_leaves_the_rest_of_the_summary(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(env, monkeypatch)
    repo = env.repo()

    def broken(run_id: str, **kwargs: Any) -> models_v2.M3Champions:
        raise ArtifactMissing("la vista m3_campeones no existe")

    monkeypatch.setattr(repo, "get_m3_champions", broken)
    html = _page(env, repo)
    text = _text(html)

    assert "No fue posible leer la evaluación." in text and "m3_campeones" in text
    assert "Distribución de campeones por familia" not in text
    # El embudo y la prueba DM no dependen de esa lectura.
    assert "Embudo de la ejecución" in text and "Prueba Diebold-Mariano" in text
    assert "pred-champion__svg" not in html


def test_an_incomplete_champions_page_is_a_visible_problem(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(env, monkeypatch)
    repo = env.repo()
    original = repo.get_m3_champions

    def short(run_id: str, **kwargs: Any) -> models_v2.M3Champions:
        found = original(run_id, **kwargs)
        return found.model_copy(update={"items": found.items[:-1]})

    monkeypatch.setattr(repo, "get_m3_champions", short)
    text = _text(_page(env, repo))

    assert "La distribución de campeones llegó incompleta." in text
    assert "Distribución de campeones por familia" not in text


def test_without_published_evidence_no_figures_are_invented(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=VERDICTS, estado="EVALUADA")
    html = _page(env)
    assert "campeones-por-familia" not in html and "pred-champion__svg" not in html
    assert "Esta ejecución todavía no tiene una evaluación publicada." in _text(html)


def test_a_publication_without_its_metadata_shows_no_distribution(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=VERDICTS, publish_m4=False)
    html = _page(env)
    assert "campeones-por-familia" not in html
    assert "La evaluación publicada no trae la lista de productos excluidos." in _text(html)


# --------------------------------------------------------------------------- el enlace
def test_the_selection_tab_links_to_the_distribution_only_with_evidence(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(env, monkeypatch)
    html = _page(env, query="vista=sku&lista=familias&sku_class=smooth&page=2")

    link = re.search(r'href="(/resultados\?[^"]*)">Ver distribución de campeones', html)
    assert link is not None
    # Va al resumen de la misma ejecución y corrida M3, y no arrastra filtros de la lista.
    assert link.group(1) == "/resultados?vista=resumen&amp;ejecucion=1&amp;m3=m3-real-1"
    # Con evidencia publicada, Resultados abre en el resumen: primero la vista general.
    assert 'aria-current="page">Resumen</a>' in _page(env, query="")


def test_the_link_is_absent_without_published_evidence(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env, monkeypatch)
    _publish_for_the_run(env, verdicts=VERDICTS, estado="EVALUADA")
    assert "Ver distribución de campeones" not in _page(env, query="vista=sku")


# ------------------------------------------------------------------------------ unitarias
def _count(sku_class: str | None, familia: str, evaluated: int, not_evaluated: int) -> Any:
    return models_v2.M3ChampionCount.model_construct(
        run_id="r",
        ingesta_sha256="a" * 64,
        sku=None,
        sku_class=sku_class,
        familia=familia,
        n_skus=evaluated + not_evaluated,
        n_evaluados=evaluated,
        n_no_evaluados=not_evaluated,
    )


def test_the_distribution_orders_families_and_fills_the_missing_ones_with_zero() -> None:
    items = [
        _count("lumpy", "classical", 1, 0),
        _count("smooth", "ml", 2, 1),
        _count(None, "ml", 2, 1),
        _count(None, "classical", 1, 0),
    ]
    result = champion_distribution(items, 4)

    assert [row.familia for row in result.portfolio] == list(CHAMPION_FAMILIES)
    assert [row.n_skus for row in result.portfolio] == [1, 3, 0, 0, 0]
    assert result.portfolio[1].share == 0.75 and result.portfolio[2].share == 0.0
    assert (result.counted_total, result.published_total, result.consistent) == (4, 4, True)
    # Las clases salen en el orden de la interfaz y solo las que M3 publicó.
    assert [c.sku_class for c in result.classes] == ["smooth", "lumpy"]
    assert result.classes[0].counts == {
        "classical": 0,
        "ml": 3,
        "dl": 0,
        "foundation": 0,
        "seasonal_naive": 0,
    }


def test_with_no_skus_there_is_no_share_and_a_mismatch_is_not_consistent() -> None:
    empty = champion_distribution([], 0)
    assert all(row.share is None for row in empty.portfolio) and empty.consistent
    off = champion_distribution([_count(None, "classical", 1, 0)], 3)
    assert not off.consistent and off.counted_total == 1 and off.published_total == 3


def test_the_chart_draws_what_it_is_given() -> None:
    assert champion_share_svg([], 0) == ""
    svg = champion_share_svg(
        [
            ("Clásica", 1.0, "6 SKU · 100,0 %"),
            ("ML", 0.001, "1 SKU · 0,1 %"),
            ("DL", None, "0 SKU"),
        ],
        6,
    )
    # El 100 % ocupa toda la escala; un valor minúsculo sigue siendo visible; el cero no se dibuja.
    assert svg.count("<path") == 2
    assert "H386.0" in svg  # 180 + 210 de escala completa, menos 4 px de radio
    assert svg.count('class="pred-champion__value"') == 3
    assert "Fracción de los 6 SKU" in svg


def test_labels_are_escaped_in_the_chart() -> None:
    svg = champion_share_svg([('<b>"x"', 0.5, "1 SKU · 50,0 %")], 2)
    assert "<b>" not in svg and "&lt;b&gt;" in svg
