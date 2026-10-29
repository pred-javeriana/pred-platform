"""Catálogo único de errores en español (G6): todo código del contrato tiene su texto."""

import json
import re
from pathlib import Path

import pytest

from pred_platform import mensajes
from pred_platform.contract import models

# Los códigos de la sección 11 del contrato frontend-motor, más el grupo eligibility.* (G7).
CODIGOS_DEL_CONTRATO = {
    "ingest.file_not_found",
    "ingest.not_csv",
    "ingest.header_rejected",
    "ingest.llm_provider",
    "ingest.schema_barrier",
    "ingest.temporal_continuity",
    "ingest.raw_write_blocked",
    "ingest.raw_conflict",
    "topology.math",
    "topology.routing",
    "topology.contract",
    "handoff.contract",
    "handoff.preservation",
    "handoff.precondition",
    "selection.contract",
    "selection.no_viable_trial",
    "selection.unregistered_family",
    "selection.unknown_class",
    "selection.router_config",
    "model.series_too_short",
    "model.fit_failed",
    "model.degenerate_forecast",
    "model.foundation_unavailable",
    "hpo.invalid_space",
    "hpo.study_failed",
    "run.incompatible_config",
    "run.failed_terminal",
    "run.invalid_transition",
    "run.manifest_missing",
    "run.manifest_corrupt",
    "run.manifest_version",
    "evaluation.failed",
    "pipeline.stage_failed",
    "pipeline.stage_unavailable",
    "synthetic.worm_overwrite",
    "synthetic.conformance",
    "synthetic.divergence_exhausted",
    "synthetic.physical_constraint",
    "platform.contract_invalid",
    "platform.schema_version_unsupported",
    "platform.artifact_missing",
    "platform.engine_unavailable",
    "platform.run_stopped",
    "eligibility.no_admissible_history",
    "eligibility.calendar_gaps",
    "eligibility.no_reserved_observations",
    "eligibility.non_finite_demand",
    "eligibility.insufficient_history",
}
EJEMPLOS = Path(models.__file__).parent / "examples" / "v1"


def test_todo_codigo_del_contrato_tiene_titulo_y_accion() -> None:
    assert set(mensajes.CATALOGO) >= CODIGOS_DEL_CONTRATO
    for codigo in CODIGOS_DEL_CONTRATO:
        entrada = mensajes.CATALOGO[codigo]
        assert entrada.titulo.strip() and entrada.titulo.endswith("."), codigo
        assert entrada.accion and entrada.accion.strip(), codigo


def test_el_catalogo_no_inventa_codigos_fuera_del_patron_del_contrato() -> None:
    patron = re.compile(r"^[a-z_]+\.[a-z_]+$")
    assert all(patron.match(codigo) for codigo in mensajes.CATALOGO)


def test_todo_codigo_de_los_ejemplos_del_contrato_esta_en_el_catalogo() -> None:
    codigos: set[str] = set()

    def recorrer(valor: object) -> None:
        if isinstance(valor, dict):
            codigo = valor.get("code")
            if isinstance(codigo, str) and "." in codigo:
                codigos.add(codigo)
            for hijo in valor.values():
                recorrer(hijo)
        elif isinstance(valor, list):
            for hijo in valor:
                recorrer(hijo)

    for ejemplo in EJEMPLOS.glob("*.json"):
        recorrer(json.loads(ejemplo.read_text(encoding="utf-8")))
    assert codigos and codigos <= set(mensajes.CATALOGO)


def test_los_marcadores_se_rellenan_con_los_datos_del_error() -> None:
    assert (
        mensajes.titulo("ingest.schema_barrier", row_index=41, column="lead_time_days")
        == "La fila 43 tiene un valor inválido en lead_time_days."
    )
    assert mensajes.titulo("pipeline.stage_failed", stage="L3") == "La etapa L3 falló."


def test_sin_los_datos_se_usa_el_titulo_generico_y_nunca_un_marcador() -> None:
    assert mensajes.titulo("ingest.schema_barrier") == "El archivo tiene un valor inválido."
    assert mensajes.titulo("ingest.schema_barrier", row_index=3) == (
        "El archivo tiene un valor inválido."
    )
    assert mensajes.titulo("pipeline.stage_failed") == "Una etapa de la corrida falló."
    assert all("{" not in mensajes.titulo(codigo) for codigo in mensajes.CATALOGO)


def test_un_codigo_desconocido_se_muestra_tal_cual_y_sin_accion() -> None:
    assert mensajes.titulo("otro.codigo") == "otro.codigo"
    assert mensajes.accion("otro.codigo") is None
    assert mensajes.reintentable("otro.codigo") is False


def test_describir_acepta_el_modelo_del_contrato_y_un_diccionario() -> None:
    info = models.ErrorInfo(
        code="ingest.schema_barrier",
        stage="L1",
        row_index=41,
        column="lead_time_days",
    )
    desde_modelo = mensajes.describir(info)
    desde_dict = mensajes.describir(info.model_dump())

    assert desde_modelo == desde_dict
    assert desde_modelo.titulo == "La fila 43 tiene un valor inválido en lead_time_days."
    assert desde_modelo.accion == "Corríjalo en el archivo y vuelva a cargarlo."
    # El título ya nombra la fila y la columna: no se repiten como ubicación aparte.
    assert desde_modelo.ubicacion is None


def test_la_ubicacion_se_muestra_cuando_el_titulo_no_la_nombra() -> None:
    descripcion = mensajes.describir(
        {"code": "ingest.header_rejected", "stage": "L1", "row_index": 2, "column": "qty"}
    )
    assert descripcion.ubicacion == "fila 4, columna qty"


def test_la_fila_es_la_del_archivo_con_la_cabecera_como_fila_uno() -> None:
    # El motor cuenta desde 0 sobre las filas de datos: la primera está en la fila 2.
    assert mensajes.fila_en_archivo(0) == 2
    assert mensajes.fila_en_archivo(1) == 3
    assert "La fila 3 " in mensajes.titulo("ingest.schema_barrier", row_index=1, column="x")


@pytest.mark.parametrize(
    "codigo",
    ["model.fit_failed", "pipeline.stage_failed", "platform.engine_unavailable"],
)
def test_los_errores_transitorios_se_pueden_reintentar(codigo: str) -> None:
    assert mensajes.reintentable(codigo) is True


def test_un_pronostico_degenerado_tiene_su_mensaje_y_no_se_reintenta() -> None:
    descripcion = mensajes.describir({"code": "model.degenerate_forecast", "stage": "L2"})
    assert descripcion.titulo == (
        "El pronóstico final del modelo fue degenerado y no se entregó para la comparación."
    )
    assert descripcion.accion
    assert descripcion.reintentable is False


def test_los_errores_de_datos_no_se_reintentan_sin_cambiar_algo() -> None:
    assert mensajes.reintentable("ingest.schema_barrier") is False
    assert mensajes.reintentable("eligibility.calendar_gaps") is False
