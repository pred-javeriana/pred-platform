"""Inputs and traceable results of the final seed-group calculation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from pred_platform.contract.models_v2 import Family, SkuClass

if TYPE_CHECKING:
    from pred_platform.evidence.lumpy import LumpyEvidence


class EvidenceInvalid(ValueError):
    """The requested publications do not form a comparable final seed group."""


@dataclass(frozen=True)
class GroupRunOption:
    run_id: str
    seed: int
    ingesta_sha256: str
    run_id_m2: str | None


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    sku: str
    sku_class: SkuClass
    familia_campeona: Family
    candidato_campeon: str | None
    dm_p_valor: FiniteFloat | None = Field(ge=0, le=1)
    dm_estadistico: FiniteFloat | None
    dm_alfa: FiniteFloat | None = Field(gt=0, lt=1)
    dm_causa: str | None
    modelos_evaluados: list[Candidate]

    @model_validator(mode="after")
    def valid_test(self) -> Verdict:
        if (self.dm_p_valor is None) != (self.dm_causa is not None):
            raise ValueError("una prueba ausente debe tener su causa")
        if self.dm_p_valor is not None and (self.dm_estadistico is None or self.dm_alfa is None):
            raise ValueError("una prueba válida exige estadístico y alfa")
        return self


class Candidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidato_id: str
    familia: Family
    modelo: str


class Exclusion(BaseModel):
    run_id: str
    sku: str
    sku_class: SkuClass
    codigo: str
    causa: str


@dataclass(frozen=True)
class RunReference:
    run_id: str
    seed: int
    ingesta_sha256: str
    run_id_m2: str | None
    identity: dict[str, Any]
    protocol: dict[str, Any]


@dataclass(frozen=True)
class RunEvidence(RunReference):
    verdicts: tuple[Verdict, ...]
    exclusions: tuple[Exclusion, ...]


@dataclass(frozen=True)
class ProductSource:
    run_id: str
    seed: int
    p_value: float | None
    statistic: float | None
    direction: Literal["negativo", "cero", "positivo"] | None
    cause: str | None
    champion: str | None
    candidate_id: str | None


@dataclass(frozen=True)
class GroupProduct:
    sku: str
    sku_class: str
    sources: tuple[ProductSource, ...]
    p_max: float | None
    p_adjusted: float | None
    significant: bool | None
    improved: bool | None


@dataclass(frozen=True)
class ClassSignificance:
    sku_class: str
    tested: int
    untested: int
    significant: int
    improved: int
    worsened: int
    mixed: int


@dataclass(frozen=True)
class GroupSignificance:
    runs: tuple[RunReference, ...]
    complete: bool
    alpha: float
    products: tuple[GroupProduct, ...]
    classes: tuple[ClassSignificance, ...]


@dataclass(frozen=True)
class GroupEvidence:
    significance: GroupSignificance
    lumpy: LumpyEvidence
