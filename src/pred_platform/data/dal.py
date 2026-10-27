"""The ``dal`` data source: what the platform's own database can answer today.

The ingestion side is real (G1): the engine's ingest reports are imported into the DAL
(``dal/importacion.py``) and the list of ingests, each ingest report and the demand topology are
read from there. So are the run status (G3) and the selection per SKU (G4): the worker imports the
run's ``seleccion.jsonl`` when it closes and the selection list and detail read the families, trials
and Walk-Forward windows from the DAL. What still does not reach the DAL (the synthetic log, G8, and
the champion and verdicts of M3) answers honestly that it is not available and names the gap that
blocks it (``data/capabilities.py`` is the single source for that). Nothing is ever invented: a view
that shows this data shows only what is stored.

Documents that always exist in some form (lists, topology, run status, verdicts) come back with
``availability.status == "unavailable"``. Documents that have no empty form (an ingest report, one
SKU's selection, a synthetic run, a download) raise ``ArtifactMissing`` naming the open gaps.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never, get_args

from pred_platform.config import DataSource
from pred_platform.contract import CONTRACT_VERSION, models
from pred_platform.contract.errors import ArtifactMissing, EngineUnavailable, InvalidQuery
from pred_platform.contract.models import CapabilityId
from pred_platform.dal import lectura
from pred_platform.dal.importacion import PANEL_COLUMNS
from pred_platform.data import capabilities
from pred_platform.data.query import (
    DEFAULT_SIZE,
    PageRequest,
    check_choice,
    check_sort,
    paginate,
)
from pred_platform.data.repository import ArtifactDownload
from pred_platform.worker.ingesta import IngestLaunchError, Submitter

# Same constants the engine's classifier uses (ADR-006 of the engine); shown while no metrics exist.
_ADI_THRESHOLD = 1.32
_CV2_THRESHOLD = 0.49

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_SKU_CLASSES = get_args(models.SkuClass)
_FAMILIES = get_args(models.Family)
_TASK_STATES = get_args(models.EstadoTarea)
_FAMILY_STATES = get_args(models.EstadoFamilia)
_TOPOLOGY_SORT = {"sku_id", "adi", "cv2", "n_positive"}
_SELECTION_SORT = {"sku_id", "valor"}


def _unidad(evento: dict[str, Any]) -> tuple[Any, Any, Any]:
    return evento.get("sku_id"), evento.get("family"), evento.get("model")


def _avance_en_curso(progreso: Path) -> tuple[str | None, int | None, int | None]:
    """Etapa en curso y ``(total, terminados)`` de sus SKU según ``progreso.jsonl``.

    El intento actual empieza en el último ``run_started``; cada etapa cuenta desde su último
    ``planned``, así que lo que una reanudación vuelve a anunciar no se cuenta dos veces. Un SKU
    termina la etapa cuando terminan, bien o mal, todas sus unidades de ese plan.
    """
    # shortcut: lee todo el archivo en cada consulta; leer solo la cola si a escala completa pesa.
    try:
        lineas = progreso.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, None, None
    etapa, plan, terminadas = None, None, set()
    for linea in lineas:
        try:
            evento = json.loads(linea)
        except ValueError:
            continue  # una línea rota por escrituras concurrentes no detiene la lectura
        if not isinstance(evento, dict):
            continue
        tipo, de_la_etapa = evento.get("event"), evento.get("stage") == etapa
        if tipo == "run_started":
            etapa, plan, terminadas = None, None, set()
        elif tipo == "stage_started" and not de_la_etapa:
            etapa, plan, terminadas = evento.get("stage"), None, set()
        elif tipo == "planned" and de_la_etapa:
            # Cada etapa se anuncia por SKU x familia (o candidato); el avance cuenta SKU.
            plan, terminadas = {_unidad(u) for u in evento.get("units", [])}, set()
        elif tipo == "unit_finished" and de_la_etapa and plan is not None:
            terminadas.add(_unidad(evento))
    if plan is None:
        return etapa, None, None
    skus = {sku for sku, _, _ in plan}
    pendientes = {sku for sku, _, _ in plan - terminadas}
    return etapa, len(skus), len(skus - pendientes)


def _now() -> datetime:
    return datetime.now(UTC)


def _envelope(availability: models.Availability) -> dict[str, object]:
    return {
        "schema_version": CONTRACT_VERSION,
        "generated_at": _now(),
        "source": "dal",
        "availability": availability,
    }


def _missing(capability: CapabilityId, what: str) -> Never:
    raise ArtifactMissing(
        f"{what}: la plataforma aún no lo guarda", blocked_by=capabilities.blocked_by(capability)
    )


def _champion(row: lectura.ChampionRow | None) -> models.Champion | None:
    if row is None:
        return None
    family = row.modelo.split(":", 1)[0]
    return models.Champion(
        modelo=row.modelo,
        family=family if family in _FAMILIES else None,  # type: ignore[arg-type]
        metrica_seleccion=row.metrica_seleccion,
        valor_seleccion=row.valor_seleccion,
    )


class DalRepository:
    """``DataRepository`` over the platform's SQLite database (read-only)."""

    source: DataSource = "dal"

    def __init__(
        self, db_path: Path, data_root: Path | None = None, submit: Submitter | None = None
    ) -> None:
        # Not opened here: the application must boot without the database (CI smoke test).
        self.db_path = db_path
        # Paths in the contract resolve against the data root (guide 16.6 of the contract).
        self.data_root = data_root
        # What ingests an uploaded CSV by running the engine (``worker/ingesta.py``); without it
        # the repository cannot take uploads and says so.
        self._submit = submit

    # ----------------------------------------------------------------- capabilities
    def get_capabilities(self) -> models.Capabilities:
        return models.Capabilities(
            **_envelope(models.Availability(status="available")),  # type: ignore[arg-type]
            items=capabilities.capability_items(),
        )

    # ---------------------------------------------------------------------- ingests
    def list_ingests(self, *, page: int = 1, size: int = DEFAULT_SIZE) -> models.IngestList:
        request = PageRequest(page, size)
        rows, total = lectura.list_ingestas(
            self.db_path, offset=(request.number - 1) * request.size, limit=request.size
        )
        paging = models.Page(number=request.number, size=request.size, total=total)
        availability = (
            models.Availability(status="available")
            if total
            else models.Availability(
                status="unavailable",
                reason_code="no_data",
                detail="Todavía no se ha cargado ningún archivo.",
            )
        )
        items = [
            models.IngestSummary(
                ingest_id=row.sha256,
                name=row.nombre_archivo,
                status=row.estado,  # type: ignore[arg-type]  # CHECK-constrained in the DAL
                parquet_path=self._display_path(row.ruta_parquet),
                rows=row.filas,
                n_skus=row.skus,
                published_at=row.publicada_en,  # type: ignore[arg-type]  # ISO 8601 text
            )
            for row in rows
        ]
        return models.IngestList(**_envelope(availability), items=items, page=paging)  # type: ignore[arg-type]

    def get_ingest_report(self, ingest_id: str) -> models.IngestReport:
        detail = self._require_ingest(ingest_id)
        if detail.estado is None:
            raise ArtifactMissing(
                f"La ingesta {ingest_id[:12]}… se cargó antes de que la plataforma guardara su "
                "informe; vuelva a ingerir el archivo con el motor"
            )
        header = json.loads(detail.diagnostico_json) if detail.diagnostico_json else None
        error = json.loads(detail.error_json) if detail.error_json else None
        return models.IngestReport(
            **_envelope(models.Availability(status="available")),  # type: ignore[arg-type]
            ingest_id=detail.sha256,
            status=detail.estado,  # type: ignore[arg-type]  # CHECK-constrained in the DAL
            source_file=models.SourceFile(
                name=detail.nombre_archivo, sha256=detail.sha256, row_count=detail.filas
            ),
            deposit=(
                models.Deposit(
                    raw_path=self._display_path(detail.ruta_raw) or "",
                    status=detail.estado_deposito,  # type: ignore[arg-type]
                    would_overwrite=bool(detail.deposito_colision),
                )
                if detail.estado_deposito is not None
                else None
            ),
            header_diagnostic=(
                models.HeaderDiagnostic(
                    status=header["status"],
                    entries=[models.DiagnosticEntry(**e) for e in header["entries"]],
                )
                if header is not None
                else None
            ),
            validation=(
                models.ValidationSummary(
                    rows_validated=detail.filas_validadas,
                    rows_daily_panel=detail.filas_panel or 0,
                    n_skus=detail.skus,
                )
                if detail.filas_validadas is not None
                else None
            ),
            published=(
                models.PublishedArtifact(
                    parquet_path=self._display_path(detail.ruta_parquet) or "",
                    rows=detail.filas_panel or 0,
                    n_skus=detail.skus,
                    columns=list(PANEL_COLUMNS),
                )
                if detail.ruta_parquet is not None
                else None
            ),
            quality_log=[
                models.QualityEntry(
                    severity=row.severidad,  # type: ignore[arg-type]
                    code=row.codigo,
                    message=row.mensaje,
                    row_index=row.fila,
                    column=row.columna,
                )
                for row in lectura.quality_log(self.db_path, detail.id)
            ],
            error=models.ErrorInfo(**error) if error is not None else None,
        )

    def submit_ingest(
        self, file_name: str, content: bytes, *, confirm_overwrite: bool = False
    ) -> models.IngestReport:
        if self._submit is None:
            raise EngineUnavailable(
                "Cargar archivos necesita el motor, que no está conectado a la plataforma",
                blocked_by=(),
            )
        try:
            sha256 = self._submit(file_name, content, confirm_overwrite)
        except ValueError as exc:
            raise InvalidQuery(str(exc)) from exc
        except IngestLaunchError as exc:
            raise EngineUnavailable(str(exc), blocked_by=()) from exc
        return self.get_ingest_report(sha256)

    # --------------------------------------------------------------------- topology
    def get_topology_report(
        self,
        ingest_id: str,
        *,
        sku_class: str | None = None,
        q: str | None = None,
        sort: str = "sku_id",
        descending: bool = False,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models.TopologyReport:
        check_choice("sku_class", sku_class, _SKU_CLASSES)
        check_sort(sort, _TOPOLOGY_SORT)
        request = PageRequest(page, size)
        detail = self._require_ingest(ingest_id)

        if detail.estado != "accepted":
            reason = (
                "La ingesta no fue aceptada: no hay topología que mostrar."
                if detail.estado is not None
                else "Esta ingesta se cargó antes de que la plataforma guardara su topología."
            )
            return models.TopologyReport(
                **_envelope(  # type: ignore[arg-type]
                    models.Availability(
                        status="unavailable", reason_code="prerequisite_missing", detail=reason
                    )
                ),
                ingest_id=ingest_id,
                thresholds=self._thresholds(detail),
                summary=models.TopologySummary(n_skus=0, by_class=dict.fromkeys(_SKU_CLASSES, 0)),
                items=[],
                page=models.Page(number=request.number, size=request.size, total=0),
            )

        rows, total = lectura.topology_rows(
            self.db_path,
            detail.id,
            sku_class=sku_class or None,
            q=q,
            sort=sort,
            descending=descending,
            offset=(request.number - 1) * request.size,
            limit=request.size,
        )
        counts = lectura.topology_counts(self.db_path, detail.id)
        return models.TopologyReport(
            **_envelope(models.Availability(status="available")),  # type: ignore[arg-type]
            ingest_id=ingest_id,
            t_star=detail.t_star,
            thresholds=self._thresholds(detail),
            summary=models.TopologySummary(
                n_skus=sum(counts.values()),
                by_class={c: counts.get(c, 0) for c in _SKU_CLASSES},  # type: ignore[misc]
            ),
            items=[
                models.TopologyMetrics(
                    sku_id=row.sku,
                    n_periods=row.n_obs,
                    n_positive=row.n_positive or 0,
                    adi=row.adi or _ADI_THRESHOLD,
                    cv2=row.cv2 or 0.0,
                    sku_class=row.clase,  # type: ignore[arg-type]  # CHECK-constrained
                )
                for row in rows
            ],
            page=models.Page(number=request.number, size=request.size, total=total),
        )

    # -------------------------------------------------------------------- run status
    def get_run_status(
        self,
        run_id: str,
        *,
        estado: str | None = None,
        sku: str | None = None,
        family: str | None = None,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models.RunStatus:
        wanted_state = check_choice("estado", estado, _TASK_STATES)
        wanted_family = check_choice("family", family, _FAMILIES)
        request = PageRequest(page, size)

        run = lectura.get_run(self.db_path, run_id)
        if run is None:
            if lectura.count_runs(self.db_path) == 0:
                # Nothing was ever launched: an honest empty state, not a missing document.
                return models.RunStatus(
                    **_envelope(  # type: ignore[arg-type]
                        models.Availability(
                            status="unavailable",
                            reason_code="no_data",
                            detail="Aún no se ha lanzado ninguna ejecución.",
                        )
                    ),
                    tasks=[],
                )
            raise ArtifactMissing(f"No existe la ejecución {run_id!r}")

        rows, total = lectura.list_tasks(
            self.db_path,
            run.id,
            estado=wanted_state,
            sku=sku,
            family=wanted_family,
            offset=(request.number - 1) * request.size,
            limit=request.size,
        )
        counts = lectura.task_state_counts(self.db_path, run.id)
        by_state = {state: counts.get(state, 0) for state in _TASK_STATES}
        total_tasks = sum(by_state.values())
        finished = by_state["exitosa"] + by_state["fallida"] + by_state["no_ejecutable"]
        etapa, skus_total, skus_terminados = None, None, None
        if run.estado == "ejecutando" and run.ruta_corrida and self.data_root is not None:
            etapa, skus_total, skus_terminados = _avance_en_curso(
                self.data_root / run.ruta_corrida / "progreso.jsonl"
            )
        parametros = json.loads(run.parametros)
        return models.RunStatus(
            **_envelope(models.Availability(status="available")),  # type: ignore[arg-type]
            ejecucion=models.Ejecucion(
                id=str(run.id),
                ingest_id=run.ingest_sha256,
                configuracion_id=str(run.configuracion_id),
                seed=run.seed,
                estado=run.estado,  # type: ignore[arg-type]  # CHECK-constrained in the DAL
                iniciada_en=run.iniciada_en,  # type: ignore[arg-type]  # ISO 8601 text
                finalizada_en=run.finalizada_en,  # type: ignore[arg-type]
            ),
            settings=models.RunSettings(
                schema_version=parametros.get("schema_version", 1),
                configuracion_version=run.configuracion_version,
                families=parametros.get("families"),
                min_train=parametros["min_train"],
                horizon=parametros["horizon"],
                step=parametros["step"],
                metric=parametros["metric"],
                seasonality=parametros["seasonality"],
                aggregation=parametros.get("aggregation", "media_recortada"),
                trim=parametros.get("trim", 0.1),
            ),
            reserve=models.Reserve(**json.loads(run.reserva_json)) if run.reserva_json else None,
            progress=models.Progress(
                tareas_total=total_tasks,
                tareas_por_estado=by_state,  # type: ignore[arg-type]
                avance_pct=round(100 * finished / total_tasks, 1) if total_tasks else 0.0,
                etapa_actual=etapa,  # type: ignore[arg-type]  # lo escribe el motor (L1 a L4)
                skus_total=skus_total,
                skus_terminados=skus_terminados,
            ),
            tasks=[
                models.Tarea(
                    id=f"t-{task.id:04d}",
                    sku=task.sku,
                    modelo=task.modelo,
                    family=task.modelo.split(":", 1)[0],  # type: ignore[arg-type]
                    corte=task.corte,
                    estado=task.estado,  # type: ignore[arg-type]
                    seed=task.seed,
                    tiempo_pared_s=task.tiempo_pared_s,
                    error=models.ErrorInfo(**json.loads(task.detalle_error))
                    if task.detalle_error
                    else None,
                    iniciada_en=task.iniciada_en,  # type: ignore[arg-type]
                    finalizada_en=task.finalizada_en,  # type: ignore[arg-type]
                )
                for task in rows
            ],
            page=models.Page(number=request.number, size=request.size, total=total),
        )

    # ------------------------------------------------------------------ selections
    def list_sku_selections(
        self,
        run_id: str,
        *,
        sku_class: str | None = None,
        family: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        sort: str = "sku_id",
        descending: bool = False,
        page: int = 1,
        size: int = DEFAULT_SIZE,
    ) -> models.SkuSelectionList:
        wanted_class = check_choice("sku_class", sku_class, _SKU_CLASSES)
        wanted_family = check_choice("family", family, _FAMILIES)
        wanted_state = check_choice("estado", estado, _FAMILY_STATES)
        check_sort(sort, _SELECTION_SORT)
        request = PageRequest(page, size)
        empty_page = models.Page(number=request.number, size=request.size, total=0)

        run = lectura.get_run(self.db_path, run_id)
        if run is None:
            if lectura.count_runs(self.db_path) == 0:
                return models.SkuSelectionList(
                    **_envelope(  # type: ignore[arg-type]
                        models.Availability(
                            status="unavailable",
                            reason_code="no_data",
                            detail="Aún no se ha lanzado ninguna ejecución.",
                        )
                    ),
                    run_id=run_id,
                    items=[],
                    page=empty_page,
                )
            raise ArtifactMissing(f"No existe la ejecución {run_id!r}")

        rows, total = lectura.list_selections(
            self.db_path,
            run.id,
            sku_class=wanted_class,
            family=wanted_family,
            estado=wanted_state,
            q=q,
            sort=sort,
            descending=descending,
            offset=(request.number - 1) * request.size,
            limit=request.size,
        )
        if total == 0 and lectura.count_selections(self.db_path, run.id) == 0:
            pending = run.estado in ("pendiente", "ejecutando")
            return models.SkuSelectionList(
                **_envelope(  # type: ignore[arg-type]
                    models.Availability(
                        status="unavailable",
                        reason_code="prerequisite_missing",
                        detail=(
                            "La selección por SKU estará disponible cuando termine la ejecución."
                            if pending
                            else "La ejecución no dejó la selección por SKU: el motor que la "
                            "corrió no la entrega o su archivo no se pudo importar."
                        ),
                    )
                ),
                ingest_id=run.ingest_sha256,
                run_id=str(run.id),
                items=[],
                page=empty_page,
            )
        champions = lectura.get_champions(self.db_path, run.id, [row.sku for row in rows])
        return models.SkuSelectionList(
            **_envelope(models.Availability(status="available")),  # type: ignore[arg-type]
            ingest_id=run.ingest_sha256,
            run_id=str(run.id),
            items=[
                models.SkuSelectionRow(
                    sku_id=row.sku,
                    sku_class=row.clase,  # type: ignore[arg-type]  # CHECK-constrained
                    profile=row.perfil,  # type: ignore[arg-type]
                    families=[models.FamilyBrief(**brief) for brief in row.familias],
                    champion=_champion(champions.get(row.sku)),
                )
                for row in rows
            ],
            page=models.Page(number=request.number, size=request.size, total=total),
        )

    def get_sku_selection(
        self, run_id: str, sku_id: str, *, include_windows: bool = False
    ) -> models.SkuSelection:
        run = lectura.get_run(self.db_path, run_id)
        if run is None:
            raise ArtifactMissing(f"No existe la ejecución {run_id!r}")
        detail = lectura.get_selection(
            self.db_path, run.id, sku_id, include_windows=include_windows
        )
        if detail is None:
            raise ArtifactMissing(f"No hay selección del SKU {sku_id!r} en la ejecución {run_id!r}")
        topology = lectura.get_topology_row(self.db_path, run.ingest_sha256, sku_id)
        return models.SkuSelection(
            **_envelope(models.Availability(status="available")),  # type: ignore[arg-type]
            ingest_id=run.ingest_sha256,
            run_id=str(run.id),
            sku_id=detail.sku,
            sku_class=detail.clase,  # type: ignore[arg-type]  # CHECK-constrained
            profile=detail.perfil,  # type: ignore[arg-type]
            policy_version=detail.version_politica,
            topology=(
                models.TopologyMetrics(
                    sku_id=topology.sku,
                    n_periods=topology.n_obs,
                    n_positive=topology.n_positive or 0,
                    adi=topology.adi or _ADI_THRESHOLD,
                    cv2=topology.cv2 or 0.0,
                    sku_class=topology.clase,  # type: ignore[arg-type]
                )
                if topology is not None and topology.clase is not None
                else None
            ),
            families=[models.FamilyEntry.model_validate(entry) for entry in detail.familias],
            champion=_champion(lectura.get_champions(self.db_path, run.id, [sku_id]).get(sku_id)),
        )

    # -------------------------------------------------------------------- verdicts
    def get_validation_verdicts(self, run_id: str) -> models.ValidationVerdicts:
        availability = capabilities.unavailable(
            "retrospective_validation", "La validación retrospectiva (L4) no está implementada."
        )
        return models.ValidationVerdicts(
            **_envelope(availability),  # type: ignore[arg-type]
            run_id=run_id,
            items=[],
        )

    # -------------------------------------------------------------- synthetic runs
    def list_synthetic_runs(
        self, *, page: int = 1, size: int = DEFAULT_SIZE
    ) -> models.SyntheticList:
        _, paging = paginate([], PageRequest(page, size))
        availability = capabilities.unavailable(
            "synthetic_log_read", "La plataforma aún no lee las bitácoras de corridas sintéticas."
        )
        return models.SyntheticList(**_envelope(availability), items=[], page=paging)  # type: ignore[arg-type]

    def get_synthetic_run(self, run_ref: str) -> models.SyntheticRun:
        _missing("synthetic_log_read", f"No hay corrida sintética {run_ref!r}")

    def download_synthetic_artifact(self, run_ref: str) -> ArtifactDownload:
        _missing("synthetic_log_read", f"No hay artefacto de la corrida sintética {run_ref!r}")

    # --------------------------------------------------------------------- helpers
    def _require_ingest(self, ingest_id: str) -> lectura.IngestDetail:
        if not _SHA256.match(ingest_id):
            raise InvalidQuery(
                f"ingest_id must be a lowercase SHA-256 hex digest, got {ingest_id!r}"
            )
        detail = lectura.get_ingesta(self.db_path, ingest_id)
        if detail is None:
            raise ArtifactMissing(f"No existe la ingesta {ingest_id[:12]}…")
        return detail

    def _display_path(self, relative: str | None) -> str | None:
        """A path stored relative to the data root, as the contract shows it (data/raw/...)."""
        if relative is None:
            return None
        if self.data_root is None:
            return relative
        return (self.data_root / relative).as_posix()

    @staticmethod
    def _thresholds(detail: lectura.IngestDetail) -> models.Thresholds:
        return models.Thresholds(
            adi=detail.umbral_adi if detail.umbral_adi is not None else _ADI_THRESHOLD,
            cv2=detail.umbral_cv2 if detail.umbral_cv2 is not None else _CV2_THRESHOLD,
        )
