"""The published RMSE beside r, and the summary table of each demand type, through the pages.

r is a ratio: it says how much better the champion is than the baseline, not how large its error
is. The pages show the RMSE that M3 published for the same test block, in demand units: the class
median of each family (``m3_cartera``) and each product's aggregated value (``m3_metricas``).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pred_platform.contract.errors import ContractInvalid
from pred_platform.data.dal import DalRepository
from tests.test_corridas import _dal_client
from tests.test_resultados import _table_rows, _text
from tests.test_results_chapters import _publish
from tests.test_worker import Env
from tests.test_worker import env as env  # noqa: F401  (the fixture)

RUN = "m3-ui-0"


def _set_rmse(env: Env) -> None:
    """Distinct published RMSE values, so each one on a page can only come from its own row."""
    with closing(sqlite3.connect(env.db)) as conn, conn:
        for sku_class, champion, baseline in (
            ("smooth", 12.25, 17.5),
            ("erratic", 30.125, 28.0),
        ):
            for familia, value in (("classical", champion), ("seasonal_naive", baseline)):
                conn.execute(
                    "UPDATE m3_cartera SET mediana = ? WHERE run_id = ? AND sku_class = ? "
                    "AND familia = ? AND metrica = 'rmse'",
                    (value, RUN, sku_class, familia),
                )
        conn.execute(
            "INSERT INTO m3_cartera VALUES (?, 'smooth', 'ml', 'rmse', 3, 3, 3, 0, "
            "14.0, 13.875, 12.0, 15.0)",
            (RUN,),
        )
        for sku, champion, baseline in (("FIL-0001", 6.021, 7.784), ("FIL-0002", 4.5, 9.0)):
            for candidate, value in (
                (f"{sku}/classical/sarima", champion),
                (f"{sku}/seasonal_naive/seasonal_naive", baseline),
            ):
                conn.execute(
                    "UPDATE m3_metricas SET valor = ? WHERE run_id = ? AND sku = ? "
                    "AND candidato_id = ? AND ventana = 'agregada' AND metrica = 'rmse'",
                    (value, RUN, sku, candidate),
                )
        # Erratic: the champion is not confirmed, and the page says why.
        conn.execute(
            "INSERT INTO m3_confirmaciones VALUES "
            "(?, 'erratic', 'classical', 'no_confirmado', 'p_holm>0.05', NULL, 0.4, 0.05, "
            "1, 0.9, 0.82, 1.04, NULL, 'ml')",
            (RUN,),
        )


@pytest.fixture
def published(env: Env) -> tuple[TestClient, str]:
    client = _dal_client(env)
    number = _publish(env, client, 0)
    _set_rmse(env)
    return client, number


def test_the_summary_table_gives_winner_margin_error_and_significance_for_each_type(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/resumen?m3={RUN}").text

    rows = _table_rows(html, "Respuesta por tipo de demanda")
    assert [row[0].split()[0] for row in rows] == ["Suave", "Errática", "Grumosa"]
    assert [row[1:] for row in rows] == [
        [
            "Clásica",
            "20,0 % menos r = 0,800 · IC [0,750; 0,850]",
            "12,250 línea base: 17,500",
            "Sí Wilcoxon con IUT y Holm: p Holm = 0,030 ≤ α = 0,05, y el intervalo de la "
            "mediana queda bajo 1 frente a cada competidora",
            "33,3 % 1 de 3 productos · 1 peor",
        ],
        [
            "Clásica",
            "10,0 % menos r = 0,900 · IC [0,820; 1,040]",
            "30,125 línea base: 28,000",
            "No Wilcoxon con IUT y Holm: p Holm > 0,05",
            "0,0 % 0 de 1 producto",
        ],
        [
            "Clásica",
            "Sin efecto publicado",
            "1,500 línea base: 1,500",
            "Sin confirmación publicada",
            "Sin prueba 0 de 0 productos",
        ],
    ]
    text = _text(html)
    assert "RMSE mediano: el error típico de un producto del tipo, en unidades de demanda" in text
    # The exact stored value stays one hover away.
    assert 'title="12,25">12,250</strong>' in html


def test_by_class_shows_the_rmse_of_the_champion_the_baseline_and_each_competitor(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/p1?m3={RUN}").text
    text = _text(html)

    assert "RMSE mediano 12,250; línea base 17,500" in text
    assert "RMSE mediano 30,125; línea base 28,000" in text
    rows = _table_rows(html, "Confirmación del campeón por clase")
    assert [row[0] for row in rows] == ["Suave", "Errática", "Grumosa"]
    assert rows[0][6:] == ["12,250 línea base: 17,500", "3"]
    assert rows[1][6:] == ["30,125 línea base: 28,000", "1"]
    competitors = _table_rows(html, "Campeona de Suave frente a cada competidora")
    assert [row[:3] for row in competitors] == [
        ["Aprendizaje automático RMSE mediano 13,875", "3", "0,900 [0,850; 0,950]"]
    ]


def test_the_product_list_shows_each_product_rmse_beside_r_from_one_read(
    published: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, number = published
    calls: list[dict[str, Any]] = []
    original = DalRepository.get_m3_metrics

    def counted(self: DalRepository, run_id: str, **kwargs: Any):
        calls.append(kwargs)
        return original(self, run_id, **kwargs)

    monkeypatch.setattr(DalRepository, "get_m3_metrics", counted)
    html = client.get(f"/corridas/{number}/resultados/productos?m3={RUN}").text

    rows = {row[0]: row for row in _table_rows(html, "Veredictos publicados")}
    assert rows["FIL-0001"][6] == "6,021 línea base: 7,784"
    assert rows["FIL-0002"][6] == "4,500 línea base: 9,000"
    assert rows["FIL-0003"][6] == "1,500 línea base: 1,500"
    # The whole page is one paginated read of the aggregated RMSE, not one read per product.
    assert len(calls) == 1
    assert calls[0]["ventana"] == "agregada" and calls[0]["metrica"] == "rmse"
    assert sorted(calls[0]["skus"]) == sorted(rows)


def test_the_product_page_shows_the_rmse_of_the_champion_the_baseline_and_every_model(
    published: tuple[TestClient, str],
) -> None:
    client, number = published
    html = client.get(f"/corridas/{number}/resultados/producto/FIL-0001?m3={RUN}").text

    assert "RMSE 6,021; línea base 7,784" in _text(html)
    models = _table_rows(html, "Modelos evaluados de FIL-0001")
    assert [(row[0], row[3], row[4]) for row in models] == [
        ("Clásica", "6,021", "Campeón"),
        ("Línea base estacional", "7,784", "Línea base"),
    ]


def test_an_unreadable_rmse_says_so_instead_of_showing_a_number(
    published: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, number = published

    def broken(self: DalRepository, run_id: str, **kwargs: Any):
        raise ContractInvalid("M3Portfolio: fila inválida", field="mediana")

    monkeypatch.setattr(DalRepository, "get_m3_portfolio", broken)
    html = client.get(f"/corridas/{number}/resultados/resumen?m3={RUN}").text

    rows = _table_rows(html, "Respuesta por tipo de demanda")
    assert [row[3] for row in rows] == ["No se pudo leer"] * 3
    # The rest of the table still reads: r and the significance do not depend on the RMSE.
    assert rows[0][2] == "20,0 % menos r = 0,800 · IC [0,750; 0,850]"


def test_a_database_without_the_portfolio_says_the_rmse_was_not_published(
    env: Env, published: tuple[TestClient, str]
) -> None:
    client, number = published
    with closing(sqlite3.connect(env.db)) as conn, conn:
        conn.execute("DELETE FROM m3_cartera")

    html = client.get(f"/corridas/{number}/resultados/p1?m3={RUN}").text

    assert _text(html).count("Sin RMSE publicado") == 3
