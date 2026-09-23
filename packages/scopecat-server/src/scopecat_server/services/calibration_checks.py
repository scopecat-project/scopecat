"""Server admission of declared checks; no laboratory Python is executed."""

import sqlite3
from collections.abc import Mapping

from pydantic import ValidationError
from scopecat.daemon.wire import RunSubmission
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
)
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.sample import SampleSelector
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)

from scopecat_server.errors import BackendConflict
from scopecat_server.services.parameter_resolution import resolve_parameters
from scopecat_server.services.samples import SampleService
from scopecat_server.services.scientific_binding import validate_scientific_binding
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore


def declared_check(intent: Mapping[str, object]) -> CalibrationCheckRequest | None:
    if "calibration_check" not in intent:
        return None
    try:
        return CalibrationCheckRequest.model_validate(
            thaw_json_value(intent["calibration_check"])
        )
    except ValidationError as error:
        raise BackendConflict(
            f"invalid calibration check declaration: {error}"
        ) from error


class CalibrationCheckAdmission:
    def __init__(self, samples: SampleService, targets: TargetCatalogStore) -> None:
        self._samples = samples
        self._targets = targets

    def validate(
        self,
        connection: sqlite3.Connection,
        request: CalibrationCheckRequest,
        *,
        samples: tuple[SampleSelector, ...],
        binding: ResolvedScientificBinding | None,
    ) -> None:
        """Resolve authoritative inputs under the parent's admission transaction.

        Parameter/setup authority is read in that transaction. Subject validation
        resolves exact immutable sample/target revisions using existing services.
        """
        current = SQLiteSetupRepository(connection).read_current()
        if (
            current is None
            or current.revision.setup.execution_content_hash
            != request.context.setup_content_hash
        ):
            raise BackendConflict("check setup differs from current authority")
        resolved = resolve_parameters(
            connection,
            parameters=request.context.parameters,
            setup=current.revision.ref,
        )
        expected = ResolvedScientificBinding(
            subject=request.context.subject,
            scenario=request.context.scenario,
            config_content_hash=resolved.config_source.content_hash,
            setup_content_hash=request.context.setup_content_hash,
        )
        if isinstance(expected.subject, UnboundSubject) and expected.scenario is None:
            raise BackendConflict(
                "physical calibration checks require a declared subject"
            )
        validate_scientific_binding(
            expected,
            resolved.config,
            sample_service=self._samples,
            targets=self._targets,
        )
        if expected.sample_selectors() != samples:
            raise BackendConflict(
                "check subject differs from procedure sample selection"
            )
        if binding is not None and binding != expected:
            raise BackendConflict(
                "check context differs from procedure scientific binding"
            )


def require_check_measurement(
    intent: Mapping[str, object], submission: RunSubmission
) -> None:
    request = declared_check(intent)
    child = submission.procedure_child
    if request is None or child is None or child.step_key != request.measurement_step:
        return
    source = submission.config_source
    binding = submission.scientific_binding
    if (
        not isinstance(source, ParameterRunConfigSource)
        or source.overrides
        or CalibrationContext(
            source.parameters,
            binding.subject,
            binding.setup_content_hash,
            binding.scenario,
        )
        != request.context
    ):
        raise BackendConflict("check measurement differs from admitted declaration")
