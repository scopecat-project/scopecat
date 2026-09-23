"""Standard calibration result contract shared by clients and services."""

from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.records.calibration_check import CalibrationCheckResult

CHECK_RESULT = AnalysisFactSchema(
    "scopecat.calibration-check-result.v1", CalibrationCheckResult
)
