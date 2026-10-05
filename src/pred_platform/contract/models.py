"""Modelos Pydantic v2 de REFERENCIA de los modelos de lectura de la plataforma (v1.0.0).

Describen lo que leen las vistas de `pred-platform` desde su DAL (SQLite, SRS 3.6).
Son la fuente de los esquemas de `../schemas/v1/` y de las reglas entre campos que
JSON Schema no expresa. No son la implementacion de la plataforma: TASK-UI-1.1-B4
los copia a `pred-platform` y los completa (ADR-05-001).

Exportar un esquema:
    python -c "import json, modelos_v1 as m; print(json.dumps(m.DOCUMENTS['run_status'].model_json_schema(), indent=2))"
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

Semver = Annotated[str, StringConstraints(pattern=r"^1\.[0-9]+\.[0-9]+$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GapId = Annotated[str, StringConstraints(pattern=r"^G[1-8]$")]
ErrorCode = Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$")]
# Decision D5: el identificador de modelo es `familia:algoritmo` en minusculas
# (por ejemplo `classical:sarima`); la familia se deduce del prefijo.
ModeloId = Annotated[
    str, StringConstraints(pattern=r"^(classical|ml|dl|foundation):[a-z0-9][a-z0-9._-]*$")
]


def _fecha_iso(valor: str) -> str:
    date.fromisoformat(valor)  # rechaza fechas que no existen en el calendario
    return valor


# Decision D5: fecha de corte como texto ISO 8601 `AAAA-MM-DD`, sin hora (como se guarda en
# SQLite, que no tiene tipo fecha). Ordena bien como texto.
FechaCorte = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    AfterValidator(_fecha_iso),
    Field(json_schema_extra={"format": "date"}),
]

# Clase de demanda del contrato 1.4 del motor (ADR-006 del motor).
SkuClass = Literal["smooth", "intermittent", "erratic", "lumpy"]
# Familias del motor (SelectionResult.family).
Family = Literal["classical", "ml", "dl", "foundation"]
Profile = Literal["dense_stable", "dense_variable", "sparse_stable", "sparse_variable"]
Source = Literal["dal", "fixture"]

# Vocabulario del DAL (dal/schema.py), que es el que leen las vistas.
Severidad = Literal["info", "advertencia", "error"]
EstadoTarea = Literal["pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable"]
EstadoEjecucion = Literal[
    "pendiente", "ejecutando", "completada", "completada_con_fallos", "detenida"
]
Veredicto = Literal["mantiene", "parcial", "falla"]
# Vocabulario del motor para la evidencia tecnica (Trial.estado).
EstadoTrial = Literal["pendiente", "corriendo", "completado", "podado", "fallido"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _familia_coincide(modelo: str | None, family: str | None) -> bool:
    """La familia declarada debe ser el prefijo del identificador de modelo."""
    return modelo is None or family is None or modelo.split(":", 1)[0] == family


# --------------------------------------------------------------------- comunes
class Availability(_Base):
    """Si el dato existe hoy. Sostiene estados vacios y acciones deshabilitadas."""

    status: Literal["available", "unavailable"]
    reason_code: (
        Literal["no_data", "not_implemented", "stage_not_implemented", "prerequisite_missing"]
        | None
    ) = None
    blocked_by: list[GapId] = Field(default_factory=list)
    detail: str | None = None

    @model_validator(mode="after")
    def _coherente(self) -> Availability:
        if self.status == "available" and (self.reason_code or self.blocked_by):
            raise ValueError("available no lleva reason_code ni blocked_by")
        if self.status == "unavailable" and self.reason_code is None:
            raise ValueError("unavailable exige reason_code")
        if self.reason_code == "not_implemented" and not self.blocked_by:
            raise ValueError("not_implemented exige blocked_by")
        return self


class Documento(_Base):
    """Sobre comun de todo modelo de lectura."""

    schema_version: Semver
    generated_at: AwareDatetime
    source: Source
    availability: Availability


class Page(_Base):
    number: int = Field(ge=1)
    size: int = Field(ge=1, le=500)
    total: int = Field(ge=0)


class ErrorInfo(_Base):
    """Error normalizado. `code` es el que se persiste en `bitacora_calidad.codigo`."""

    code: ErrorCode
    stage: Literal["L0", "L1", "L2", "L3", "L4", "platform"]
    severity: Severidad = "error"
    detail: str | None = None
    field: str | None = None
    row_index: int | None = Field(default=None, ge=0)
    column: str | None = None
    source_exception: str | None = None
    retryable: bool = False


class TopologyMetrics(_Base):
    sku_id: str = Field(min_length=1)
    n_periods: int = Field(ge=1)
    n_positive: int = Field(ge=0)
    adi: float = Field(gt=0)
    cv2: float = Field(ge=0)
    sku_class: SkuClass


# -------------------------------------------------------------------- capabilities
CapabilityId = Literal[
    "parquet_read",
    "ingest_report_persisted",
    "topology_persisted",
    "run_orchestration",
    "family_comparison",
    "selection_results_persisted",
    "trial_detail_persisted",
    "walkforward_evidence_persisted",
    "champion_selection",
    "retrospective_validation",
    "synthetic_log_read",
]


class Capability(_Base):
    id: CapabilityId
    available: bool
    reason_code: Literal["not_implemented", "stage_not_implemented"] | None = None
    blocked_by: list[GapId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherente(self) -> Capability:
        if self.available and (self.reason_code or self.blocked_by):
            raise ValueError("available no lleva reason_code ni blocked_by")
        if not self.available and self.reason_code is None:
            raise ValueError("no disponible exige reason_code")
        if self.reason_code == "not_implemented" and not self.blocked_by:
            raise ValueError("not_implemented exige blocked_by")
        return self


class Capabilities(Documento):
    items: list[Capability]


# ------------------------------------------------------------------ ingest_report
class SourceFile(_Base):
    name: str
    sha256: Sha256
    row_count: int = Field(ge=0)


class Deposit(_Base):
    raw_path: str
    status: Literal["deposited", "already_present_identical", "already_present_different"]
    would_overwrite: bool


class DiagnosticEntry(_Base):
    field: str
    severity: Severidad = "error"
    message: str
    action: str | None = None


class HeaderDiagnostic(_Base):
    status: Literal["accepted", "rejected"]
    entries: list[DiagnosticEntry] = Field(default_factory=list)


class QualityEntry(_Base):
    """Espejo de una fila de `bitacora_calidad`."""

    severity: Severidad
    code: ErrorCode
    message: str
    row_index: int | None = Field(default=None, ge=0)
    column: str | None = None


class ValidationSummary(_Base):
    rows_validated: int = Field(ge=0)
    rows_daily_panel: int = Field(ge=0)
    n_skus: int = Field(ge=0)


class PublishedArtifact(_Base):
    parquet_path: str
    rows: int = Field(ge=0)
    n_skus: int = Field(ge=0)
    columns: list[str]


class IngestReport(Documento):
    ingest_id: Sha256
    status: Literal["accepted", "rejected", "failed", "needs_confirmation"]
    source_file: SourceFile
    deposit: Deposit | None = None
    header_diagnostic: HeaderDiagnostic | None = None
    validation: ValidationSummary | None = None
    published: PublishedArtifact | None = None
    quality_log: list[QualityEntry] = Field(default_factory=list)
    error: ErrorInfo | None = None


class IngestSummary(_Base):
    ingest_id: Sha256 | None = None
    name: str
    status: Literal["accepted", "rejected", "failed", "needs_confirmation"] | None = None
    parquet_path: str | None = None
    rows: int | None = Field(default=None, ge=0)
    n_skus: int | None = Field(default=None, ge=0)
    published_at: AwareDatetime | None = None


class IngestList(Documento):
    items: list[IngestSummary]
    page: Page


# --------------------------------------------------------------- topology_report
class Thresholds(_Base):
    adi: float
    cv2: float


class TopologySummary(_Base):
    n_skus: int = Field(ge=0)
    by_class: dict[SkuClass, int]


class TopologyReport(Documento):
    ingest_id: Sha256
    # El motor clasifica cada SKU solo con la historia hasta t* (ADR-019 del motor).
    t_star: FechaCorte | None = None
    thresholds: Thresholds
    summary: TopologySummary
    items: list[TopologyMetrics]
    page: Page


# -------------------------------------------------------------------- run_status
class Ejecucion(_Base):
    """Fila de `ejecuciones`."""

    id: str = Field(min_length=1)
    ingest_id: Sha256 | None = None
    configuracion_id: str
    seed: int
    estado: EstadoEjecucion
    iniciada_en: AwareDatetime | None = None
    finalizada_en: AwareDatetime | None = None


class RunSettings(_Base):
    """Contenido de `configuraciones.parametros` (JSON) que la interfaz necesita mostrar."""

    # Version del formato del JSON guardado en `parametros` (decision D5).
    schema_version: int = Field(ge=1)
    configuracion_version: int | None = None
    families: list[Family] | None = None
    policy_version: str | None = None
    min_train: int = Field(ge=1)
    horizon: int = Field(ge=1)
    step: int = Field(ge=1)
    metric: Literal["mae", "rmse", "smape", "mase"]
    seasonality: int = Field(ge=1)
    aggregation: Literal["media", "mediana", "media_recortada"]
    trim: float = Field(ge=0, lt=0.5)


class Tarea(_Base):
    """Fila de `tareas`: una por unidad del motor, es decir, por SKU x modelo (decision D6).

    El estado resume las etapas L2 y L3 de esa unidad; la traza por etapa queda en el motor.
    """

    id: str = Field(min_length=1)
    sku: str | None = None
    modelo: ModeloId
    family: Family | None = None
    # Fecha de corte t* de la reserva (decisiones D5 y D6): la misma para todas las tareas
    # de una ejecucion. Los cortes de cada ventana de Walk-Forward estan en la evidencia.
    corte: FechaCorte
    estado: EstadoTarea
    seed: int
    tiempo_pared_s: float | None = Field(default=None, ge=0)
    error: ErrorInfo | None = None
    iniciada_en: AwareDatetime | None = None
    finalizada_en: AwareDatetime | None = None

    @model_validator(mode="after")
    def _coherente(self) -> Tarea:
        if self.estado == "fallida" and self.error is None:
            raise ValueError("fallida exige error")
        if self.estado in ("pendiente", "ejecutando") and self.finalizada_en:
            raise ValueError("una tarea sin terminar no tiene finalizada_en")
        if not _familia_coincide(self.modelo, self.family):
            raise ValueError("family no coincide con el prefijo de modelo")
        return self


class Progress(_Base):
    tareas_total: int = Field(ge=0)
    tareas_por_estado: dict[EstadoTarea, int]
    avance_pct: float = Field(ge=0, le=100)


class Reserve(_Base):
    """Reserva cronologica para M3 (ADR-03-003 del motor): el 20 % final del calendario."""

    fraction: float = Field(gt=0, lt=1)
    t_star: FechaCorte
    first_reserved: FechaCorte
    last_observed: FechaCorte
    reserved_days: int = Field(ge=1)

    @model_validator(mode="after")
    def _coherente(self) -> Reserve:
        t_star = date.fromisoformat(self.t_star)
        if date.fromisoformat(self.first_reserved) != t_star + timedelta(days=1):
            raise ValueError("first_reserved debe ser el dia siguiente a t_star")
        if (date.fromisoformat(self.last_observed) - t_star).days != self.reserved_days:
            raise ValueError("reserved_days debe ser la distancia de t_star a last_observed")
        return self


class RunStatus(Documento):
    ejecucion: Ejecucion | None = None
    settings: RunSettings | None = None
    reserve: Reserve | None = None
    progress: Progress | None = None
    tasks: list[Tarea]
    page: Page | None = None


# ----------------------------------------------------------------- sku_selection
class TrialRow(_Base):
    id: str
    configuracion: dict[str, JsonValue]
    estado: EstadoTrial
    valor: float | None = None
    n_ventanas: int = Field(ge=0)
    motivo: str | None = None
    timestamp: AwareDatetime | None = None


class WindowResult(_Base):
    indice: int = Field(ge=0)
    corte: FechaCorte
    inicio_train: int
    fin_train: int
    inicio_val: int
    fin_val: int
    metricas: dict[str, float | None]
    duracion_s: float | None = None
    fallo: str | None = None


class WalkForwardSummary(_Base):
    metrica_objetivo: Literal["mae", "rmse", "smape", "mase"]
    valor_agregado: float | None = None
    completo: bool
    n_ventanas_evaluadas: int = Field(ge=0)
    n_ventanas_totales: int = Field(ge=0)
    selection_scope: Literal["same_history"] = "same_history"
    statistical_comparison_available: bool = False
    windows: list[WindowResult] | None = None


class _EvidenciaHPO(_Base):
    metrica_objetivo: str
    valor: float
    n_ventanas: int = Field(ge=0)
    n_trials: int = Field(ge=0)
    n_completados: int = Field(ge=0)
    n_podados: int = Field(ge=0)
    n_fallidos: int = Field(ge=0)
    seed: int
    # Referencia al estudio HPO persistido por el motor (`hpo/<estudio_hpo>/`), donde
    # estan los ensayos; nula si la corrida no persistio estudios.
    estudio_hpo: str | None = None


class EvidenciaClasica(_EvidenciaHPO):
    family: Literal["classical"]
    order: Annotated[list[int], Field(min_length=3, max_length=3)]
    seasonal_order: Annotated[list[int], Field(min_length=4, max_length=4)]


class EvidenciaML(_EvidenciaHPO):
    family: Literal["ml"]
    hiperparametros: dict[str, JsonValue]


class EvidenciaDL(_EvidenciaHPO):
    family: Literal["dl"]
    hiperparametros: dict[str, JsonValue]


class EvidenciaFundacional(_Base):
    family: Literal["foundation"]
    configuracion: dict[str, JsonValue]
    optimizado: Literal[False] = False


Evidence = Annotated[
    EvidenciaClasica | EvidenciaML | EvidenciaDL | EvidenciaFundacional,
    Field(discriminator="family"),
]


class Exclusion(_Base):
    reason_code: Literal[
        "not_in_policy_matrix", "not_configured", "series_too_short", "execution_failed"
    ]
    detail: str | None = None


# Estado de una familia para un SKU: los cinco de `tareas` mas `excluida`.
EstadoFamilia = Literal[
    "pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable", "excluida"
]


class FamilyEntry(_Base):
    family: Family
    estado: EstadoFamilia
    exclusion: Exclusion | None = None
    modelo: ModeloId | None = None
    produced_by: str | None = None
    evidence: Evidence | None = None
    forecast_config: dict[str, JsonValue] | None = None
    forecast_seed: int | None = None
    trials: list[TrialRow] | None = None
    walk_forward: WalkForwardSummary | None = None
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def _coherente(self) -> FamilyEntry:
        if self.estado in ("excluida", "no_ejecutable") and self.exclusion is None:
            raise ValueError(f"{self.estado} exige exclusion")
        if self.estado not in ("excluida", "no_ejecutable") and self.exclusion:
            raise ValueError("exclusion solo aplica a excluida/no_ejecutable")
        if self.estado == "fallida" and self.error is None:
            raise ValueError("fallida exige error")
        if self.evidence is not None and self.evidence.family != self.family:
            raise ValueError("evidence.family no coincide con family")
        if not _familia_coincide(self.modelo, self.family):
            raise ValueError("family no coincide con el prefijo de modelo")
        return self


class Champion(_Base):
    """Fila de `resultados_comparativos`. Hoy nadie la escribe (M3 no existe)."""

    modelo: ModeloId
    family: Family | None = None
    metrica_seleccion: str
    valor_seleccion: float

    @model_validator(mode="after")
    def _coherente(self) -> Champion:
        if not _familia_coincide(self.modelo, self.family):
            raise ValueError("family no coincide con el prefijo de modelo")
        return self


class SkuSelection(Documento):
    ingest_id: Sha256 | None = None
    run_id: str | None = None
    sku_id: str
    sku_class: SkuClass
    profile: Profile
    policy_version: str | None = None
    topology: TopologyMetrics | None = None
    families: list[FamilyEntry]
    champion: Champion | None = None


class FamilyBrief(_Base):
    family: Family
    estado: EstadoFamilia
    metrica_objetivo: str | None = None
    valor: float | None = None


class SkuSelectionRow(_Base):
    sku_id: str
    sku_class: SkuClass
    profile: Profile
    families: list[FamilyBrief]
    champion: Champion | None = None


class SkuSelectionList(Documento):
    ingest_id: Sha256 | None = None
    run_id: str | None = None
    items: list[SkuSelectionRow]
    page: Page


# ------------------------------------------------------------ validation_verdicts
class SkuVerdict(_Base):
    """Fila de `reportes_validacion`."""

    sku_id: str
    modelo_campeon: ModeloId
    veredicto: Veredicto
    detalle: dict[str, JsonValue] = Field(default_factory=dict)


class ValidationVerdicts(Documento):
    run_id: str | None = None
    items: list[SkuVerdict]
    audit_bundle: dict[str, JsonValue] | None = None


# ----------------------------------------------------------------- synthetic_run
class SyntheticLog(_Base):
    """Espejo de `BitacoraCorrida` (aumentacion/bitacora.py del motor)."""

    semilla_ruta: str
    semilla_aleatoria: int
    contract_version: str
    period: int
    n_series_por_sku: int
    skus_procesados: int
    skus_omitidos: int
    tolerancia_divergencia: float
    tasa_rechazo: float
    intentos_bootstrap: int
    n_demanda_rectificada: int
    n_lead_time_acotado: int
    row_count: int
    artefacto_sha256: Sha256
    artefacto_path: str
    iniciada_en: AwareDatetime
    finalizada_en: AwareDatetime | None = None
    # Campos que el motor agrego despues (metodo de aumento, ADR-016, e identidad de la
    # corrida). Las bitacoras anteriores no los traen, asi que son opcionales.
    metodo: str | None = None
    block_size: int | None = Field(default=None, ge=1)
    ruido_relativo: float | None = Field(default=None, ge=0)
    max_reintentos: int | None = Field(default=None, ge=0)
    mapeo_columnas: dict[str, str] | None = None
    semilla_sha256: Sha256 | None = None
    huella_corrida: str | None = None
    configuracion: dict[str, JsonValue] | None = None


class SyntheticArtifact(_Base):
    file_name: str
    sha256: Sha256
    rows: int = Field(ge=0)
    media_type: Literal["text/csv"] = "text/csv"
    columns: list[str]


class SyntheticRun(Documento):
    run_ref: str = Field(min_length=1)
    log_file: str
    log: SyntheticLog
    artifact: SyntheticArtifact


class SyntheticList(Documento):
    items: list[SyntheticRun]
    page: Page


DOCUMENTS: dict[str, type[BaseModel]] = {
    "capabilities": Capabilities,
    "ingest_report": IngestReport,
    "ingest_list": IngestList,
    "topology_report": TopologyReport,
    "run_status": RunStatus,
    "sku_selection": SkuSelection,
    "sku_selection_list": SkuSelectionList,
    "validation_verdicts": ValidationVerdicts,
    "synthetic_run": SyntheticRun,
    "synthetic_list": SyntheticList,
    "error": ErrorInfo,
}
