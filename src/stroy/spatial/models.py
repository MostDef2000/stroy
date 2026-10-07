"""Pure spatial validation models (R2).

No database, no mutation: the validator consumes a ``Scene`` snapshot and
returns a deterministic report. The scene is never touched.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class CheckSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CheckResult(BaseModel):
    severity: CheckSeverity
    entity_ids: list[str]
    rule_id: str
    measured_mm: float | None = None
    expected_min_mm: float | None = None
    explanation: str
    suggestion: str | None = None


class ValidationConfig(BaseModel):
    min_walkway_mm: float = 600.0
    enabled_rules: list[str] | None = None


class ValidationSummary(BaseModel):
    info: int = 0
    warning: int = 0
    error: int = 0


class ValidationReport(BaseModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    scene_revision_id: str
    scene_content_hash: str
    config: ValidationConfig
    summary: ValidationSummary = Field(default_factory=ValidationSummary)
    results: list[CheckResult] = Field(default_factory=list)
