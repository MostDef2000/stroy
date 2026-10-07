"""Pure spatial validation (R2): geometry, rules, deterministic reports.

No database access and no scene mutation: callers hand over a ``Scene``
snapshot and receive a ``ValidationReport``.
"""

from __future__ import annotations

from stroy.spatial.models import (
    CheckResult,
    CheckSeverity,
    ValidationConfig,
    ValidationReport,
    ValidationSummary,
)
from stroy.spatial.validator import (
    RULE_IDS,
    config_hash,
    report_hash,
    scene_content_hash,
    validate_scene,
)

__all__ = [
    "RULE_IDS",
    "CheckResult",
    "CheckSeverity",
    "ValidationConfig",
    "ValidationReport",
    "ValidationSummary",
    "config_hash",
    "report_hash",
    "scene_content_hash",
    "validate_scene",
]
