"""The champion confirmation M3 published on the test block (engine ADR-023, migration 0011).

The contract v2 has no document for these rows, so the platform validates them here, where they
enter from the shared database. The fields keep the SQL names of ``m3_confirmaciones``,
``m3_comparaciones`` and ``m3_significancia``, and each rule mirrors the migration's checks and
the engine's meaning of the columns:

- A ratio is RMSE_champion / RMSE_other; 0 and infinity are valid edges, NaN is not.
- Bootstrap and Wilson intervals are stored as two columns, both present or both absent.
- ``p_iut`` is the largest p of the competitors and ``p_holm`` its Holm adjustment across classes;
  ``sin_comparables`` and ``no_aplica`` have no Holm p, and only ``confirmado`` has no cause.
- One run uses one ``alfa`` for every class.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from pred_platform.contract.models_v2 import Family, SkuClass

Text = Annotated[str, StringConstraints(min_length=1)]
Count = Annotated[int, Field(ge=0, strict=True)]
# ge=0 rejects NaN (every comparison with NaN is false) and keeps infinity.
Ratio = Annotated[float, Field(ge=0, strict=True)]
Probability = Annotated[float, Field(ge=0, le=1, strict=True)]
# The engine stores Wilson bounds unclipped: when the champion wins all or none of the SKUs, float
# noise puts a bound just past 1 or 0 (1.0000000000000002, -1.4e-17). Only that noise is accepted.
WilsonBound = Annotated[float, Field(ge=-1e-9, le=1 + 1e-9, strict=True)]
Level = Annotated[float, Field(gt=0, lt=1, strict=True)]
Statistic = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
Estado = Literal["confirmado", "no_confirmado", "sin_comparables", "no_aplica"]


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=True)


def _interval(lower: float | None, upper: float | None, name: str) -> None:
    if (lower is None) != (upper is None):
        raise ValueError(f"{name}: el intervalo exige sus dos extremos")
    if lower is not None and upper is not None and lower > upper:
        raise ValueError(f"{name}: los extremos están invertidos")


class M3Comparison(_Row):
    """The champion against one competitor of its class (``m3_comparaciones``)."""

    competidora: Family
    n_skus: Count
    mediana_razon: Ratio | None
    ic_mediana_inferior: Ratio | None
    ic_mediana_superior: Ratio | None
    hodges_lehmann: Ratio | None
    ic_hodges_lehmann_inferior: Ratio | None
    ic_hodges_lehmann_superior: Ratio | None
    n_victorias: Count
    ic_victorias_inferior: WilsonBound | None
    ic_victorias_superior: WilsonBound | None
    estadistico_wilcoxon: Statistic | None
    p_wilcoxon: Probability | None
    p_iut: Probability | None
    n_mejores: Count | None
    n_peores: Count | None
    causa: Text | None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _interval(self.ic_mediana_inferior, self.ic_mediana_superior, "ic_mediana")
        _interval(
            self.ic_hodges_lehmann_inferior, self.ic_hodges_lehmann_superior, "ic_hodges_lehmann"
        )
        _interval(self.ic_victorias_inferior, self.ic_victorias_superior, "ic_victorias")
        if self.n_victorias > self.n_skus:
            raise ValueError("n_victorias supera n_skus")
        if self.p_wilcoxon is None and self.causa is None:
            raise ValueError("una comparación sin p de Wilcoxon exige su causa")
        return self


class M3ClassConfirmation(_Row):
    """The confirmation of one class's frozen champion (``m3_confirmaciones``)."""

    sku_class: SkuClass
    familia_campeona: Family
    estado: Estado
    causa: Text | None
    p_iut: Probability | None
    p_holm: Probability | None
    alfa: Level
    efecto_n_skus: Count | None
    efecto_mediana_r: Ratio | None
    efecto_ic_inferior: Ratio | None
    efecto_ic_superior: Ratio | None
    efecto_causa: Text | None
    contendiente: Family | None
    comparaciones: list[M3Comparison]

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if (self.estado == "confirmado") != (self.causa is None):
            raise ValueError("solo un campeón confirmado no tiene causa")
        if (self.p_holm is None) != (self.estado in ("sin_comparables", "no_aplica")):
            raise ValueError("el p de Holm no corresponde al estado")
        _interval(self.efecto_ic_inferior, self.efecto_ic_superior, "efecto_ic")
        names = [c.competidora for c in self.comparaciones]
        if len(set(names)) != len(names):
            raise ValueError("una competidora aparece dos veces")
        if self.familia_campeona in names:
            raise ValueError("el campeón no compite contra sí mismo")
        return self


class M3ClassSignificance(_Row):
    """HLN-DM of the champion against Seasonal Naive, counted per class (``m3_significancia``)."""

    sku_class: SkuClass
    n_probados: Count
    n_mejores: Count
    n_peores: Count
    n_mejores_by: Count
    n_peores_by: Count
    nivel_fcr: Level | None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if self.n_mejores + self.n_peores > self.n_probados:
            raise ValueError("los significativos por BH superan los SKU probados")
        if self.n_mejores_by + self.n_peores_by > self.n_probados:
            raise ValueError("los significativos por BY superan los SKU probados")
        if (self.nivel_fcr is None) != (self.n_mejores + self.n_peores == 0):
            raise ValueError("el nivel FCR existe solo con SKU significativos por BH")
        return self


class M3Confirmation(_Row):
    """What M3 published on the test block of one PUBLICADA run, in class order.

    A class of ``m3_categorias`` may have no row here; the reader shows that class without one.
    """

    run_id: Text
    clases: list[M3ClassConfirmation]
    significancia: list[M3ClassSignificance]

    @model_validator(mode="after")
    def _rules(self) -> Self:
        for name, rows in (("clases", self.clases), ("significancia", self.significancia)):
            classes = [row.sku_class for row in rows]
            if len(set(classes)) != len(classes):
                raise ValueError(f"{name}: una clase aparece dos veces")
        if len({row.alfa for row in self.clases}) > 1:
            raise ValueError("las clases de una corrida comparten alfa")
        return self

    @property
    def alfa(self) -> float | None:
        return self.clases[0].alfa if self.clases else None
