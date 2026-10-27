"""Gráfico ADI–CV²: accesible, con umbrales del contrato y sin reclasificar."""

from xml.etree import ElementTree

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


def test_scatter_sin_umbrales_no_dibuja_lineas_ni_las_anuncia() -> None:
    report = FixtureRepository("normal").get_topology_report(ACCEPTED)
    report.thresholds = None
    svg = topology_scatter_svg(report)
    root = ElementTree.fromstring(svg)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert root.findall('.//svg:line[@class="pred-chart__threshold"]', ns) == []
    assert root.findall('.//svg:text[@class="pred-chart__threshold-label"]', ns) == []
    assert "Las líneas de puntos" not in svg
    assert "FIL-0001" in svg


def test_scatter_does_not_invent_sku_outside_the_report_page() -> None:
    report = FixtureRepository("normal").get_topology_report(ACCEPTED, sku_class="smooth")
    svg = topology_scatter_svg(report)
    assert "FIL-0001" in svg
    assert "FIL-0002" not in svg


def test_legend_separates_canonical_labels_into_two_rows_below_the_axis() -> None:
    report = FixtureRepository("normal").get_topology_report(ACCEPTED)
    root = ElementTree.fromstring(topology_scatter_svg(report))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    labels = root.findall('.//svg:g[@class="pred-chart__legend"]/svg:text', ns)
    assert [label.text for label in labels] == [
        "Smooth (Suave)",
        "Erratic (Errática)",
        "Intermittent (Intermitente)",
        "Lumpy (Grumosa)",
    ]
    rows = sorted({float(label.attrib["y"]) for label in labels})
    assert len(rows) == 2 and rows[1] - rows[0] >= 20
    for y in rows:
        x = sorted(float(label.attrib["x"]) for label in labels if float(label.attrib["y"]) == y)
        assert len(x) == 2 and x[1] - x[0] >= 290
    axis = next(e for e in root.findall("svg:text", ns) if e.text == "ADI")
    assert rows[0] - float(axis.attrib["y"]) >= 18
