"""Published Fieller confidence sets for the champion's r against Seasonal Naive.

A null upper bound means no upper limit, not a missing interval. A tail adds a second
range starting at ``inferior_cola``. No bounds or confidence levels are estimated here.
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Bound = Annotated[float, Field(ge=0, strict=True)]


class M3Interval(BaseModel):
    """One product's unadjusted set, using the names from ``m3_intervalos_r``."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    sku: Annotated[str, StringConstraints(min_length=1, strict=True)]
    inferior: Bound
    superior: Bound | None
    inferior_cola: Bound | None

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.superior is not None and self.superior < self.inferior:
            raise ValueError("superior debe ser mayor o igual que inferior")
        if self.inferior_cola is not None and (
            self.superior is None or self.inferior_cola <= self.superior
        ):
            raise ValueError("inferior_cola exige superior y debe ser mayor que superior")
        return self
