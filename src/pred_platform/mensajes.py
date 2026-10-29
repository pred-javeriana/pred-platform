"""Catálogo único de errores en español: código -> título, acción y si se puede reintentar.

Los códigos son los de la sección 11 del contrato frontend-motor (G6 y G7). El motor entrega el
código; este módulo es el único lugar donde se traduce a lo que ve la persona (RNF-USA-02), con la
acción correctiva. Los textos son la copia final de la plataforma; el contrato solo los sugiere.

Un título puede llevar ``{row_index}``, ``{column}`` o ``{stage}``. Si el error no trae esos datos
se usa el título ``generico``, de modo que nunca se muestra un marcador sin rellenar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol


@dataclass(frozen=True, slots=True)
class Mensaje:
    titulo: str
    accion: str | None = None
    # Título sin marcadores, para cuando el error no trae fila, columna o etapa.
    generico: str | None = None
    reintentable: bool = False


@dataclass(frozen=True, slots=True)
class Descripcion:
    """Lo que la interfaz muestra de un error: título ya rellenado, acción y ubicación."""

    codigo: str
    titulo: str
    accion: str | None
    reintentable: bool
    ubicacion: str | None


class _ConUbicacion(Protocol):
    @property
    def code(self) -> str: ...

    @property
    def stage(self) -> str: ...

    @property
    def row_index(self) -> int | None: ...

    @property
    def column(self) -> str | None: ...


def fila_en_archivo(row_index: int) -> int:
    """La fila que la persona ve en el CSV (cabecera = fila 1) para el ``row_index`` del motor.

    El motor cuenta desde 0 sobre las filas de datos, sin la cabecera: la primera fila de datos
    es el ``row_index`` 0 y está en la fila 2 del archivo.
    """
    return row_index + 2


_REVISAR_REGISTRO: Final = "Revise el registro del motor (motor.log) y lance de nuevo."

CATALOGO: Final[Mapping[str, Mensaje]] = {
    # --- L1: ingesta
    "ingest.file_not_found": Mensaje(
        "No se encontró el archivo.",
        "Verifique la ruta o cárguelo de nuevo.",
    ),
    "ingest.not_csv": Mensaje(
        "El archivo debe ser CSV y estar en la carpeta de datos crudos.",
        "Cargue un archivo con extensión .csv desde la pantalla de datos.",
    ),
    "ingest.header_rejected": Mensaje(
        "Las cabeceras no coinciden con el formato esperado.",
        "Revise cada observación del diagnóstico y renombre las columnas en el archivo.",
    ),
    "ingest.llm_provider": Mensaje(
        "No se pudo consultar al proveedor de alineación semántica.",
        "Use las cabeceras canónicas (sku_id, timestamp, demand_qty) o intente más tarde.",
        reintentable=True,
    ),
    "ingest.schema_barrier": Mensaje(
        "La fila {row_index} tiene un valor inválido en {column}.",
        "Corríjalo en el archivo y vuelva a cargarlo.",
        generico="El archivo tiene un valor inválido.",
    ),
    "ingest.temporal_continuity": Mensaje(
        "Las fechas no forman una serie diaria válida.",
        "Corrija los huecos de fechas del SKU en el CSV y vuelva a cargarlo.",
    ),
    "ingest.raw_write_blocked": Mensaje(
        "La carpeta de datos crudos es de solo lectura.",
        "Revise los permisos de la carpeta de datos del motor.",
    ),
    "ingest.raw_conflict": Mensaje(
        "Ya existe un archivo con ese nombre y contenido distinto.",
        "Confirme para conservar ambos archivos o cambie el nombre del nuevo.",
    ),
    "topology.math": Mensaje(
        "No se pudo clasificar la demanda de uno o más SKU.",
        "Revise que cada SKU tenga demanda positiva y vuelva a cargar el archivo.",
    ),
    "topology.routing": Mensaje(
        "No se pudo clasificar la demanda de uno o más SKU.",
        "Revise que cada SKU tenga demanda positiva y vuelva a cargar el archivo.",
    ),
    "topology.contract": Mensaje(
        "No se pudo clasificar la demanda de uno o más SKU.",
        "Revise que cada SKU tenga demanda positiva y vuelva a cargar el archivo.",
    ),
    "handoff.contract": Mensaje(
        "El resultado no cumple el formato de salida.",
        _REVISAR_REGISTRO,
    ),
    "handoff.preservation": Mensaje(
        "La clasificación alteró filas del panel.",
        _REVISAR_REGISTRO,
    ),
    "handoff.precondition": Mensaje(
        "Hay SKU sin demanda positiva; no se pueden clasificar.",
        "Quite esos SKU del archivo o complete su demanda y vuelva a cargarlo.",
    ),
    # --- L2: selección, reanudación y elegibilidad
    "selection.contract": Mensaje(
        "No se pudo seleccionar un modelo para el SKU.",
        "Revise la serie del SKU; las demás familias no se afectan.",
    ),
    "selection.no_viable_trial": Mensaje(
        "Ninguna configuración terminó la búsqueda para este SKU.",
        "Aumente los ensayos o revise la serie del SKU.",
    ),
    "selection.unregistered_family": Mensaje(
        "La familia solicitada no está registrada.",
        "Quite esa familia de la configuración de la corrida.",
    ),
    "selection.unknown_class": Mensaje(
        "La clase de demanda del SKU no es válida.",
        "Vuelva a cargar el archivo para clasificar de nuevo la demanda.",
    ),
    "selection.router_config": Mensaje(
        "La configuración del enrutador no es válida.",
        _REVISAR_REGISTRO,
    ),
    "run.incompatible_config": Mensaje(
        "La configuración no coincide con la corrida guardada.",
        "Lance una corrida nueva con la configuración actual.",
    ),
    "run.failed_terminal": Mensaje(
        "La corrida anterior falló y no puede reanudarse.",
        "Lance una corrida nueva.",
    ),
    "run.invalid_transition": Mensaje(
        "La corrida no admite esa operación en su estado actual.",
        "Actualice la pantalla y revise el estado de la corrida.",
    ),
    "run.manifest_missing": Mensaje(
        "No se pudo leer el manifiesto de la corrida.",
        _REVISAR_REGISTRO,
    ),
    "run.manifest_corrupt": Mensaje(
        "No se pudo leer el manifiesto de la corrida.",
        _REVISAR_REGISTRO,
    ),
    "run.manifest_version": Mensaje(
        "No se pudo leer el manifiesto de la corrida.",
        "Lance una corrida nueva con esta versión del motor.",
    ),
    "eligibility.no_admissible_history": Mensaje(
        "La serie no tiene historia antes del corte.",
        "Cargue la historia anterior del SKU o quítelo del archivo.",
    ),
    "eligibility.calendar_gaps": Mensaje(
        "La serie tiene fechas faltantes.",
        "Complete el calendario diario del SKU en el CSV y vuelva a cargarlo.",
    ),
    "eligibility.no_reserved_observations": Mensaje(
        "La serie no llega a la reserva de evaluación.",
        "Cargue las observaciones más recientes del SKU para que cubra el corte.",
    ),
    "eligibility.non_finite_demand": Mensaje(
        "Hay valores de demanda no numéricos en la historia.",
        "Corrija o elimine esos valores en el CSV y vuelva a cargarlo.",
    ),
    "eligibility.insufficient_history": Mensaje(
        "La historia es demasiado corta para entrenar y evaluar.",
        "Cargue más historia de este SKU o reduzca el mínimo de entrenamiento (min_train).",
    ),
    # --- L2/L3: modelos, búsqueda y evaluación
    "model.series_too_short": Mensaje(
        "La serie es demasiado corta para esta familia.",
        "Cargue más historia de este SKU o reduzca el mínimo de entrenamiento (min_train).",
    ),
    "model.fit_failed": Mensaje(
        "El modelo no pudo ajustarse.",
        "Pruebe con más ensayos o con otra familia; las demás familias del SKU no se afectan.",
        reintentable=True,
    ),
    "model.degenerate_forecast": Mensaje(
        "El pronóstico final del modelo fue degenerado y no se entregó para la comparación.",
        "Pruebe con otra familia; las demás familias del SKU no se afectan.",
    ),
    "model.foundation_unavailable": Mensaje(
        "El modelo fundacional no está disponible en este equipo.",
        "Instale las dependencias de la familia fundacional o quítela de la configuración.",
    ),
    "hpo.invalid_space": Mensaje(
        "La búsqueda de configuraciones no pudo completarse.",
        "Revise el espacio de búsqueda en la configuración de la corrida.",
    ),
    "hpo.study_failed": Mensaje(
        "La búsqueda de configuraciones no pudo completarse.",
        "Reanude la ejecución: se retoman los estudios guardados.",
        reintentable=True,
    ),
    "evaluation.failed": Mensaje(
        "La evaluación Walk-Forward no pudo completarse.",
        "Reduzca el horizonte o el paso de validación, o pruebe otra familia.",
    ),
    # --- pipeline
    "pipeline.stage_failed": Mensaje(
        "La etapa {stage} falló.",
        _REVISAR_REGISTRO,
        generico="Una etapa de la corrida falló.",
        reintentable=True,
    ),
    "pipeline.stage_unavailable": Mensaje(
        "Esta etapa aún no está disponible.",
        "Esta función llegará en una versión posterior.",
    ),
    # --- L0: datos sintéticos
    "synthetic.worm_overwrite": Mensaje(
        "El archivo sintético ya existe y no puede reemplazarse.",
        "Genere la serie con otra semilla o en otra carpeta.",
    ),
    "synthetic.conformance": Mensaje(
        "El archivo sintético no cumple el contrato.",
        _REVISAR_REGISTRO,
    ),
    "synthetic.divergence_exhausted": Mensaje(
        "No se logró una serie sintética aceptable.",
        "Intente con otra semilla o con menos ruido.",
    ),
    "synthetic.physical_constraint": Mensaje(
        "Se violó una restricción física de los datos.",
        "Revise los parámetros de generación.",
    ),
    # --- plataforma
    "platform.contract_invalid": Mensaje(
        "Los datos recibidos no tienen el formato esperado.",
        "Vuelva a lanzar la corrida; si persiste, avise al equipo.",
    ),
    "platform.schema_version_unsupported": Mensaje(
        "La versión del documento no es compatible.",
        "Actualice la plataforma o use una versión del motor compatible.",
    ),
    "platform.artifact_missing": Mensaje(
        "No se encontró el resultado esperado.",
        _REVISAR_REGISTRO,
    ),
    "platform.engine_unavailable": Mensaje(
        "El motor no está disponible.",
        "Verifique que el motor esté instalado y reanude la ejecución.",
        reintentable=True,
    ),
    "platform.database_busy": Mensaje(
        "La base de datos estaba ocupada y no se pudo registrar la corrida.",
        "Espere un momento y reanude la ejecución.",
        reintentable=True,
    ),
    "platform.run_stopped": Mensaje(
        "La ejecución se detuvo a petición del operador.",
        "Reanude la ejecución cuando quiera.",
        reintentable=True,
    ),
}


def mensaje(codigo: str) -> Mensaje | None:
    return CATALOGO.get(codigo)


def reintentable(codigo: str) -> bool:
    entrada = CATALOGO.get(codigo)
    return entrada.reintentable if entrada is not None else False


def titulo(
    codigo: str,
    *,
    row_index: int | None = None,
    column: str | None = None,
    stage: str | None = None,
) -> str:
    """Título en español de ``codigo`` con los datos del error; el código mismo si no se conoce."""
    entrada = CATALOGO.get(codigo)
    if entrada is None:
        return codigo
    datos: dict[str, Any] = {
        "row_index": None if row_index is None else fila_en_archivo(row_index),
        "column": column,
        "stage": stage,
    }
    usados = [nombre for nombre in datos if "{" + nombre + "}" in entrada.titulo]
    if any(datos[nombre] is None for nombre in usados):
        return entrada.generico or entrada.titulo.split("{")[0].rstrip(" ,:") + "."
    return entrada.titulo.format(**datos)


def accion(codigo: str) -> str | None:
    entrada = CATALOGO.get(codigo)
    return entrada.accion if entrada is not None else None


def _ubicacion(row_index: int | None, column: str | None) -> str | None:
    partes = []
    if row_index is not None:
        partes.append(f"fila {fila_en_archivo(row_index)}")
    if column:
        partes.append(f"columna {column}")
    return ", ".join(partes) or None


def describir(error: _ConUbicacion | Mapping[str, Any]) -> Descripcion:
    """Descripción lista para mostrar de un ``ErrorInfo`` (modelo o diccionario)."""
    if isinstance(error, Mapping):
        codigo = str(error.get("code", ""))
        etapa, fila, columna = error.get("stage"), error.get("row_index"), error.get("column")
    else:
        codigo, etapa, fila, columna = error.code, error.stage, error.row_index, error.column
    texto = titulo(codigo, row_index=fila, column=columna, stage=etapa)
    entrada = CATALOGO.get(codigo)
    # Si el título ya nombra la fila y la columna no se repiten como ubicación aparte.
    ya_nombrada = (
        entrada is not None
        and "{row_index}" in entrada.titulo
        and fila is not None
        and columna is not None
    )
    return Descripcion(
        codigo=codigo,
        titulo=texto,
        accion=accion(codigo),
        reintentable=reintentable(codigo),
        ubicacion=None if ya_nombrada else _ubicacion(fila, columna),
    )
