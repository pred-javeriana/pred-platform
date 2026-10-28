"""Contrato 2.1.0: lectura de evidencia publicada por M3, sin recalcularla.

Fuente de schemas/v2. Las filas conservan los nombres publicados por M3.
Las reglas entre campos validan integridad, no seleccionan modelos ni emiten veredictos.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Text = Annotated[str, StringConstraints(min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Semver = Annotated[str, StringConstraints(pattern=r"^2\.[0-9]+\.[0-9]+$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Positive = Annotated[int, Field(ge=1, strict=True)]
Number = Annotated[float, Field(strict=True)]
NonNegative = Annotated[Number, Field(ge=0)]
Probability = Annotated[Number, Field(ge=0, le=1)]
SkuClass = Literal["smooth", "intermittent", "erratic", "lumpy"]
Family = Literal["classical", "ml", "dl", "foundation", "seasonal_naive"]
Veredicto = Literal[
    "FALLO_TECNICO", "NO_EVALUABLE", "EVIDENCIA_INSUFICIENTE", "VALIDADO", "EXPLORATORIO"
]
EstadoM3 = Literal["EN_CURSO", "EVALUADA", "SELECCIONADA", "PUBLICADA", "RECHAZADA"]
Metrica = Literal["mae", "rmse", "mase", "me", "razon_sn"]
MetricaPersistida = Literal[
    "mae", "rmse", "mase", "me", "razon_sn", "n_pares",
    "n_ventanas_totales", "n_ventanas_validas", "n_pronosticos_validos",
]


def _date(value: str) -> str:
    date.fromisoformat(value)
    return value


Fecha = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    AfterValidator(_date), Field(json_schema_extra={"format": "date"}),
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Availability(_Base):
    status: Literal["available", "unavailable"]
    reason_code: Literal["no_data", "not_published", "metadata_missing"] | None
    detail: Text | None

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if (self.status == "available") != (self.reason_code is None):
            raise ValueError("unavailable exige causa; available no la admite")
        if self.status == "unavailable" and self.detail is None:
            raise ValueError("unavailable exige detalle visible")
        return self


class Documento(_Base):
    schema_version: Semver
    generated_at: AwareDatetime
    source: Literal["dal", "fixture"]
    availability: Availability


class Page(_Base):
    number: Positive
    size: Annotated[int, Field(ge=1, le=500, strict=True)]
    total: Count


def _page(items: list, page: Page) -> None:
    expected = min(page.size, max(0, page.total - (page.number - 1) * page.size))
    if len(items) != expected:
        raise ValueError("items no corresponde al total y la pagina solicitada")


def _unique(values: list, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contiene claves repetidas")


class Trace(_Base):
    run_id: Text
    ingesta_sha256: Sha256


class RunRow(Trace):
    """Fila agregada: sku es null, nunca un SKU ficticio."""

    sku: None


class SkuRow(Trace):
    sku: Text


class Origin(_Base):
    datos_sinteticos: Annotated[bool, Field(strict=True)]
    origen_declarado: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def _origin(self) -> Self:
        if self.datos_sinteticos and not self.origen_declarado:
            raise ValueError("datos sinteticos exige origen declarado")
        return self


class SelectionPolicy(_Base):
    """Proyeccion de m3_protocolos.protocolo.politica; no son ajustes de la vista."""

    version: Text
    n_min: Positive
    n_min_dm: Annotated[int, Field(ge=2, strict=True)]
    alfa_dm: Annotated[Number, Field(gt=0, lt=1)]


class M3Run(RunRow, Origin):
    run_id_m2: Text | None
    identidad: Text
    estado: EstadoM3
    causa_rechazo: Text | None
    creado_en: AwareDatetime
    policy: SelectionPolicy | None
    publicadas_misma_identidad: list[Text]
    aviso: Literal["varias_publicadas_misma_identidad"] | None

    @model_validator(mode="after")
    def _run(self) -> Self:
        if (self.estado == "RECHAZADA") != (self.causa_rechazo is not None):
            raise ValueError("solo RECHAZADA exige causa_rechazo")
        _unique(self.publicadas_misma_identidad, "publicadas_misma_identidad")
        if self.estado == "PUBLICADA" and self.run_id not in self.publicadas_misma_identidad:
            raise ValueError("una PUBLICADA debe incluir su run_id en publicadas_misma_identidad")
        if (len(self.publicadas_misma_identidad) > 1) != (self.aviso is not None):
            raise ValueError("varias PUBLICADA de una identidad exigen aviso")
        return self


class M3Runs(Documento):
    items: list[M3Run]
    page: Page
    selected_run_id: Text | None

    @model_validator(mode="after")
    def _runs(self) -> Self:
        _page(self.items, self.page)
        _unique([r.run_id for r in self.items], "corridas")
        if self.availability.status == "unavailable" and (self.items or self.selected_run_id):
            raise ValueError("una lista no disponible no selecciona ni expone corridas")
        for row in self.items:
            if row.run_id == self.selected_run_id and row.estado != "PUBLICADA":
                raise ValueError("solo se selecciona una corrida PUBLICADA")
        return self


class M3Progress(RunRow):
    n_unidades_previstas: Count | None
    n_resueltas: Count
    n_confirmadas: Count
    n_fallidas: Count

    @model_validator(mode="after")
    def _counts(self) -> Self:
        if self.n_resueltas != self.n_confirmadas + self.n_fallidas:
            raise ValueError("avance no conserva las unidades resueltas")
        if self.n_unidades_previstas is not None and self.n_resueltas > self.n_unidades_previstas:
            raise ValueError("avance supera las unidades previstas")
        return self


class M3RunStatus(Documento):
    run: M3Run | None
    progress: M3Progress | None

    @model_validator(mode="after")
    def _status(self) -> Self:
        if (self.availability.status == "available") != (self.run is not None):
            raise ValueError("estado disponible exige corrida")
        if self.progress is not None:
            _same_run(self.progress, self.run)
        return self


def _same_run(row: Trace, run: M3Run | None) -> None:
    if run is None or (row.run_id, row.ingesta_sha256) != (run.run_id, run.ingesta_sha256):
        raise ValueError("run_id e ingesta_sha256 de cada fila deben coincidir con la corrida")


class ConteosVeredicto(_Base):
    FALLO_TECNICO: Count
    NO_EVALUABLE: Count
    EVIDENCIA_INSUFICIENTE: Count
    VALIDADO: Count
    EXPLORATORIO: Count


class PorcentajesVeredicto(_Base):
    FALLO_TECNICO: Annotated[Number, Field(ge=0, le=100)]
    NO_EVALUABLE: Annotated[Number, Field(ge=0, le=100)]
    EVIDENCIA_INSUFICIENTE: Annotated[Number, Field(ge=0, le=100)]
    VALIDADO: Annotated[Number, Field(ge=0, le=100)]
    EXPLORATORIO: Annotated[Number, Field(ge=0, le=100)]


class M3Funnel(RunRow):
    total_productos: Count
    excluded_count: Count
    tras_exclusiones: Count
    no_evaluados: Count
    evaluados: Count
    conteos: ConteosVeredicto

    @model_validator(mode="after")
    def _funnel(self) -> Self:
        counts = self.conteos
        if self.total_productos != self.excluded_count + self.tras_exclusiones:
            raise ValueError("el embudo debe conservar todos los productos")
        if self.evaluados != counts.VALIDADO + counts.EXPLORATORIO:
            raise ValueError("evaluados solo suma VALIDADO y EXPLORATORIO")
        if self.no_evaluados != (
            counts.FALLO_TECNICO + counts.NO_EVALUABLE + counts.EVIDENCIA_INSUFICIENTE
        ):
            raise ValueError("no_evaluados suma los otros tres veredictos")
        if self.tras_exclusiones != self.evaluados + self.no_evaluados:
            raise ValueError("cada producto no excluido necesita uno de los cinco veredictos")
        return self


class LateLaunchExclusion(SkuRow):
    causa: Literal["lanzado despues del corte"]


class LateLaunches(RunRow):
    """Corte y lista completa de exclusiones que M3 publica con la corrida (m3_cortes, m3_lanzamientos_tardios)."""

    t_star: Fecha
    excluded_count: Count
    items: list[LateLaunchExclusion]

    @model_validator(mode="after")
    def _exclusions(self) -> Self:
        if self.excluded_count != len(self.items):
            raise ValueError("excluded_count debe coincidir con la lista completa")
        _unique([r.sku for r in self.items], "exclusiones")
        for row in self.items:
            if (row.run_id, row.ingesta_sha256) != (self.run_id, self.ingesta_sha256):
                raise ValueError("exclusion de otra corrida o ingesta")
        return self


class EvaluatedModel(SkuRow):
    candidato_id: Text
    familia: Family
    modelo: Text
    razon_sn: NonNegative | None


class M3Verdict(SkuRow, Origin):
    """evidencia_publicada + ingesta y n_min; dm_* ocupa una columna independiente."""

    identidad: Text
    sku_class: SkuClass
    veredicto: Veredicto
    familia_campeona: Family
    candidato_campeon: Text | None
    n_ventanas: Count
    n_min: Positive
    cobertura: Probability
    razon_sn: NonNegative | None
    pierde_frente_a_linea_base: Annotated[bool, Field(strict=True)] | None
    comparacion_incompleta: Annotated[bool, Field(strict=True)]
    iqr_diferencia_mae: NonNegative | None
    dm_n_ventanas: Count
    dm_horizonte: Count
    dm_estadistico: Number | None
    dm_p_valor: Probability | None
    dm_p_ajustado: Probability | None
    dm_alfa: Annotated[Number, Field(gt=0, lt=1)] | None
    dm_significativa: Annotated[bool, Field(strict=True)] | None
    dm_causa: Text | None
    justificacion: Text
    modelos_evaluados: list[EvaluatedModel]

    @model_validator(mode="after")
    def _verdict(self) -> Self:
        inferential = (
            self.dm_estadistico, self.dm_p_valor, self.dm_p_ajustado,
            self.dm_alfa, self.dm_significativa,
        )
        if self.dm_causa is None:
            if any(v is None for v in inferential) or not self.dm_n_ventanas or not self.dm_horizonte:
                raise ValueError("sin dm_causa se exige la prueba HLN-DM y su ajuste BH completos")
            if self.datos_sinteticos or not self.origen_declarado:
                raise ValueError("la inferencia solo se publica sobre datos reales declarados")
        elif any(v is not None for v in inferential):
            raise ValueError("dm_causa exige resultados inferenciales nulos")
        if self.veredicto == "VALIDADO" and (self.datos_sinteticos or not self.origen_declarado):
            raise ValueError("VALIDADO exige origen real declarado")
        _unique([r.candidato_id for r in self.modelos_evaluados], "modelos_evaluados")
        if self.candidato_campeon is not None:
            champion = next(
                (r for r in self.modelos_evaluados if r.candidato_id == self.candidato_campeon), None
            )
            if champion is None or (champion.familia, champion.razon_sn) != (
                self.familia_campeona, self.razon_sn
            ):
                raise ValueError("el campeon debe corresponder al candidato publicado")
        for row in self.modelos_evaluados:
            if (row.run_id, row.ingesta_sha256, row.sku) != (
                self.run_id, self.ingesta_sha256, self.sku
            ):
                raise ValueError("modelo evaluado de otro SKU, corrida o ingesta")
        return self


class PublishedDocument(Documento):
    run: M3Run | None

    @model_validator(mode="after")
    def _published(self) -> Self:
        if self.availability.status == "available":
            if self.run is None or self.run.estado != "PUBLICADA":
                raise ValueError("la evidencia solo esta disponible para una corrida PUBLICADA")
            if self.run.policy is None:
                raise ValueError("la evidencia PUBLICADA exige la politica congelada")
        elif self.availability.reason_code == "no_data":
            if self.run is not None:
                raise ValueError("no_data no identifica una corrida existente")
        elif self.availability.reason_code == "not_published":
            if self.run is None or self.run.estado == "PUBLICADA":
                raise ValueError("not_published exige una corrida no PUBLICADA")
        elif self.run is None or self.run.estado != "PUBLICADA":
            raise ValueError("metadata_missing exige una corrida PUBLICADA")
        return self


class PublishedPage[Row: Trace](PublishedDocument):
    items: list[Row]
    page: Page

    @model_validator(mode="after")
    def _rows(self) -> Self:
        _page(self.items, self.page)
        if self.availability.status == "unavailable" and (self.items or self.page.total):
            raise ValueError("la evidencia no disponible no expone filas ni conteos parciales")
        for row in self.items:
            _same_run(row, self.run)
        return self


class M3Evidence(PublishedPage[M3Verdict]):
    funnel: M3Funnel | None
    late_launches: LateLaunches | None

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        if self.availability.status == "unavailable":
            if self.funnel is not None or self.late_launches is not None:
                raise ValueError("la evidencia no disponible no expone embudo ni exclusiones")
            return self
        if self.funnel is None or self.late_launches is None:
            raise ValueError("la evidencia disponible exige embudo y exclusiones completos")
        run = self.run
        if run is None or run.policy is None:
            raise ValueError("la evidencia PUBLICADA exige la politica congelada")
        _same_run(self.funnel, run)
        _same_run(self.late_launches, run)
        if self.funnel.excluded_count != self.late_launches.excluded_count:
            raise ValueError("el embudo no coincide con las exclusiones publicadas")
        if self.page.total > self.funnel.tras_exclusiones:
            raise ValueError("la consulta supera los productos con veredicto")
        excluded = {r.sku for r in self.late_launches.items}
        _unique([r.sku for r in self.items], "veredictos")
        counts = Counter(r.veredicto for r in self.items)
        for verdict, count in counts.items():
            if count > getattr(self.funnel.conteos, verdict):
                raise ValueError("la pagina supera el conteo global de su veredicto")
        for row in self.items:
            if row.sku in excluded:
                raise ValueError("un producto excluido no puede tener veredicto")
            if (row.identidad, row.datos_sinteticos, row.origen_declarado, row.n_min) != (
                run.identidad, run.datos_sinteticos, run.origen_declarado, run.policy.n_min,
            ):
                raise ValueError("el veredicto no conserva identidad, origen o n_min de la corrida")
        return self


class M3Metric(SkuRow):
    candidato_id: Text
    ventana: Literal["agregada"] | Fecha
    metrica: MetricaPersistida
    valor: Number | None
    causa: Text | None

    @model_validator(mode="after")
    def _value(self) -> Self:
        if (self.valor is None) != (self.causa is not None):
            raise ValueError("una metrica nula exige causa y un valor calculado no la admite")
        if self.valor is not None and self.metrica != "me" and self.valor < 0:
            raise ValueError("solo ME puede ser negativa")
        return self


class M3Metrics(PublishedPage[M3Metric]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([(r.sku, r.candidato_id, r.ventana, r.metrica) for r in self.items], "metricas")
        return self


class M3Category(RunRow):
    sku_class: SkuClass
    familia_campeona: Family
    motivo: Literal[
        "menor_mediana", "empate_por_simplicidad", "compuerta_linea_base", "sin_skus_comparables"
    ]
    adverso: Annotated[bool, Field(strict=True)]
    medianas_r: dict[Family, NonNegative]
    skus_comparables: list[Text]
    excluidos: dict[Text, Text]
    familias_excluidas: dict[Family, Literal["no_entregada", "no_elegible"]]
    conteos: ConteosVeredicto
    porcentajes: PorcentajesVeredicto
    mediana_r: NonNegative | None
    n_adversos: Count

    @model_validator(mode="after")
    def _category(self) -> Self:
        _unique(self.skus_comparables, "skus_comparables")
        if set(self.skus_comparables) & self.excluidos.keys():
            raise ValueError("un SKU comparable no puede estar excluido de la seleccion")
        counts = self.conteos.model_dump()
        total = sum(counts.values())
        if self.n_adversos > total:
            raise ValueError("n_adversos supera los productos de la categoria")
        for label, value in self.porcentajes.model_dump().items():
            expected = 100.0 * counts[label] / total if total else 0.0
            if abs(value - expected) > 1e-8:
                raise ValueError("los porcentajes no corresponden a los conteos publicados")
        return self


class M3Categories(PublishedPage[M3Category]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([r.sku_class for r in self.items], "categorias")
        return self


class M3PortfolioRow(RunRow):
    sku_class: SkuClass
    familia: Family
    metrica: Metrica
    n_skus: Count
    n_instancias: Count
    n_calculables: Count
    n_no_calculables: Count
    media: Number | None
    mediana: Number | None
    p25: Number | None
    p75: Number | None

    @model_validator(mode="after")
    def _distribution(self) -> Self:
        if self.n_instancias != self.n_calculables + self.n_no_calculables:
            raise ValueError("la distribucion debe conservar las instancias")
        if self.n_skus > self.n_instancias:
            raise ValueError("n_skus no puede superar las instancias")
        values = (self.media, self.mediana, self.p25, self.p75)
        if (self.n_calculables == 0 and any(v is not None for v in values)) or (
            self.n_calculables > 0 and any(v is None for v in values)
        ):
            raise ValueError("la distribucion solo existe con instancias calculables")
        return self


class M3Portfolio(PublishedPage[M3PortfolioRow]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([(r.sku_class, r.familia, r.metrica) for r in self.items], "cartera")
        return self


class M3Forecast(SkuRow):
    candidato_id: Text
    origen: Fecha
    h: Positive
    fecha: Fecha
    valor: Number | None


class M3Forecasts(PublishedPage[M3Forecast]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([(r.sku, r.candidato_id, r.origen, r.h) for r in self.items], "pronosticos")
        return self


class M3Failure(SkuRow):
    candidato_id: Text
    modelo: str | None
    campo: Text
    codigo_error: Text
    mensaje: Text


class M3Failures(PublishedPage[M3Failure]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([r.candidato_id for r in self.items], "fallos")
        return self


class M3Unit(SkuRow):
    candidato_id: Text
    origen: Fecha
    familia: Family
    modelo: Text
    estado: Literal["confirmada", "fallida"]
    causa: Text | None
    emitido_en: AwareDatetime | None
    codigo_version: Text | None
    semilla: Annotated[int, Field(strict=True)] | None

    @model_validator(mode="after")
    def _unit(self) -> Self:
        if (self.estado == "fallida") != (self.causa is not None):
            raise ValueError("una unidad fallida exige causa y una confirmada no la admite")
        return self


class M3Units(PublishedPage[M3Unit]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([(r.sku, r.candidato_id, r.origen) for r in self.items], "unidades")
        return self


class M3Observation(SkuRow):
    """Demanda observada de un dia de la reserva (m3_observaciones)."""
    fecha: Fecha
    valor: Number | None


class M3Observations(PublishedPage[M3Observation]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([(r.sku, r.fecha) for r in self.items], "observaciones")
        return self


class M3ChampionCount(RunRow):
    """SKU por familia campeona publicada (vista m3_campeones); sku_class null es la cartera."""
    sku_class: SkuClass | None
    familia: Family
    n_skus: Count
    n_evaluados: Count
    n_no_evaluados: Count

    @model_validator(mode="after")
    def _total(self) -> Self:
        if self.n_skus != self.n_evaluados + self.n_no_evaluados:
            raise ValueError("n_skus debe sumar evaluados y no evaluados")
        return self


class M3Champions(PublishedPage[M3ChampionCount]):
    @model_validator(mode="after")
    def _keys(self) -> Self:
        _unique([(r.sku_class, r.familia) for r in self.items], "campeones")
        return self


DOCUMENTS: dict[str, type[BaseModel]] = {
    "m3_runs": M3Runs,
    "m3_run_status": M3RunStatus,
    "m3_evidence": M3Evidence,
    "m3_metrics": M3Metrics,
    "m3_categories": M3Categories,
    "m3_portfolio": M3Portfolio,
    "m3_forecasts": M3Forecasts,
    "m3_failures": M3Failures,
    "m3_units": M3Units,
    "m3_observations": M3Observations,
    "m3_champions": M3Champions,
}
