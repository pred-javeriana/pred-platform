"""Exportación a CSV de los resultados de M3 (RF-CON-06, tarea A2 de la sección 4.5).

Dos descargas sobre ``DataRepository``: los veredictos de la consulta (``/resultados/exportar.csv``)
y las exclusiones de la corrida (``/resultados/exclusiones.csv``). La consulta es la de la pestaña
«Consulta» sin el límite de página, y la corrida es la M3 exacta que resolvió esa pestaña. Aquí solo
se resuelve y se da formato: no se recalcula nada. Un error se responde con su código HTTP y su
causa en texto, nunca con un archivo parcial.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response

from pred_platform.app.consulta import ConsultaQuery, parse_consulta_query
from pred_platform.app.exportar import exclusions_csv, filename, verdicts_csv
from pred_platform.app.resultados_m3 import export_evidence
from pred_platform.contract.errors import DataLayerError, InvalidQuery
from pred_platform.data.deps import get_repository
from pred_platform.data.repository import DataRepository

router = APIRouter()

# El código del problema que dio el repositorio -> el estado HTTP. Lo demás (no publicada, sin
# metadatos, cambió durante la lectura) es un conflicto con el estado actual de la corrida.
_STATUS = {"invalid": 400, "no_data": 404}


def _error(title: str, detail: str, status: int) -> PlainTextResponse:
    return PlainTextResponse(f"{title} {detail}\n", status_code=status)


def _m2_run_id(repository: DataRepository, reference: str) -> str | None:
    """La ejecución M2 de la consulta; ``None`` si no existe o no se pudo leer."""
    try:
        status = repository.get_run_status(reference, size=1)
    except (DataLayerError, InvalidQuery):
        return None
    return status.ejecucion.id if status.ejecucion is not None else None


def _export(request: Request, repository: DataRepository, *, rows: bool) -> Response:
    try:
        query = parse_consulta_query(dict(request.query_params))
    except InvalidQuery as exc:
        return _error("La consulta no es válida.", str(exc), 400)
    m2_run_id = _m2_run_id(repository, query.ejecucion)
    if m2_run_id is None:
        return _error(
            "No hay una ejecución con resultados para exportar.",
            "Revise el parámetro «ejecucion» o ejecute una selección primero.",
            404,
        )
    if not rows:
        # Las exclusiones son las de toda la corrida: los filtros de veredicto no las alcanzan.
        query = ConsultaQuery(ejecucion=query.ejecucion, m3=query.m3)
    exported = export_evidence(repository, m2_run_id, query, rows=rows)
    if exported.problem is not None or exported.run is None:
        problem = exported.problem
        assert problem is not None
        return _error(problem.title, problem.detail, _STATUS.get(problem.code, 409))
    run = exported.run
    if rows:
        body, kind = verdicts_csv(run, exported.items), "resultados"
    else:
        body = exclusions_csv(run, exported.late_launches, exported.eligibility_exclusions)
        kind = "exclusiones"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename(kind, run.run_id)}"'},
    )


@router.get("/resultados/exportar.csv")
def exportar_resultados(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> Response:
    return _export(request, repository, rows=True)


@router.get("/resultados/exclusiones.csv")
def exportar_exclusiones(
    request: Request,
    repository: DataRepository = Depends(get_repository),  # noqa: B008
) -> Response:
    return _export(request, repository, rows=False)
