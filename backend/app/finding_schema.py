"""Pure validation of a source-paper finding revision (contract source-evidence-packet/0.1.0).

A finding is one atomic proposition reported by, or explicitly derived from, an
article. Unknown context is stated with an explicit marker, never left null, and
numbers are carried exactly as typed: no unit is guessed and a non-significant
result stays a result, not evidence of absence.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Origin = Literal["source-explicit", "ai-paraphrase", "reviewer-interpretation"]
NDefinition = Literal["article-level", "experimental-unit", "group", "unknown"]
# Explicit-unknown vocabulary of the packet contract; they are values, not nulls.
UNKNOWN_MARKERS = ("unknown", "not-assessed", "not-available", "withheld")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FindingContext(_Strict):
    population: Text
    exposure: Text
    comparator: Text
    outcome: Text
    time: Text
    experimental_unit: Text


class NumericN(_Strict):
    value: Annotated[int, Field(ge=0)]
    definition: NDefinition


class NumericResult(_Strict):
    name: Text
    value: float
    unit: Text
    n: NumericN

    @field_validator("value")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("numeric result must be finite")
        return value


class FindingContent(_Strict):
    statement: Text
    origin: Origin
    context: FindingContext
    numeric_results: Annotated[tuple[NumericResult, ...], Field(max_length=64)] = ()

    @field_validator("numeric_results")
    @classmethod
    def distinct_names(cls, results):
        names = [r.name for r in results]
        if len(set(names)) != len(names):
            raise ValueError("numeric result names must be distinct")
        return results
