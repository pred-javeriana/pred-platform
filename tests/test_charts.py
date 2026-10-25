"""Gráfico ADI–CV²: accesible, con umbrales del contrato y sin reclasificar."""

from pred_platform.app.charts import topology_scatter_svg
from pred_platform.data.fixtures import FixtureRepository

ACCEPTED = "b1831d4585b72d7c7bcb98ada7bae70e7b229449edf74a8a7a7f39e6552df56e"


def test_scatter_includes_accessible_title_desc_and_thresholds() -> None:
    report = FixtureRepository("normal").get_topology_report(ACCEPTED)
    svg = topology_scatter_svg(report)
    assert 'role="img"' in svg
    assert "<title>" in svg
    assert "<desc>" in svg
    assert "1,32" in svg
    assert "0,49" in svg
    assert "FIL-0001" in svg
    for label in (
        "Smooth (Suave)",
        "Erratic (Errática)",
        "Intermittent (Intermitente)",
        "Lumpy (Grumosa)",
    ):
        assert label in svg
    assert "circle" in svg
    assert "polygon" in svg or "rect" in svg


def test_scatter_does_not_invent_sku_outside_the_report_page() -> None:
    report = FixtureRepository("normal").get_topology_report(ACCEPTED, sku_class="smooth")
    svg = topology_scatter_svg(report)
    assert "FIL-0001" in svg
    assert "FIL-0002" not in svg
