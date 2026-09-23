"""Save reusable capability policy without selecting a global measurement context."""

from datetime import UTC, datetime

from scopecat.daemon.calibration_checks import (
    CalibrationProfilePage,
    CalibrationProfileReportQuery,
    CalibrationReport,
    CalibrationReportQuery,
)
from scopecat.records.calibration_policy import (
    CalibrationProfile,
    CalibrationProfileRecord,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.services.calibration_checks import CalibrationCheckQueries
from scopecat_server.storage.sqlite.calibration_profiles import CalibrationProfileStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


class CalibrationProfileService:
    def __init__(self, sqlite: SQLiteDatabase, checks: CalibrationCheckQueries) -> None:
        self._sqlite = sqlite
        self._checks = checks
        self._store = CalibrationProfileStore()

    def save(self, profile: CalibrationProfile) -> CalibrationProfileRecord:
        with self._sqlite.write_transaction() as connection:
            existing = self._store.read(connection, profile.id)
            if existing is not None:
                if existing.profile != profile:
                    raise BackendConflict(
                        "profile ID already has different requirements; save a new ID"
                    )
                return existing
            record = CalibrationProfileRecord(
                profile=profile, created_at=datetime.now(UTC)
            )
            self._store.insert(connection, record)
            return record

    def get(self, identity: str) -> CalibrationProfileRecord:
        with self._sqlite.read_transaction() as connection:
            record = self._store.read(connection, identity)
            if record is None:
                raise BackendNotFound("calibration profile was not found")
            return record

    def list(self, limit: int, cursor: int | None) -> CalibrationProfilePage:
        with self._sqlite.read_transaction() as connection:
            return self._store.list(connection, limit, cursor)

    def report(
        self, identity: str, query: CalibrationProfileReportQuery
    ) -> CalibrationReport:
        profile = self.get(identity).profile
        # Profiles are immutable; only evidence needs a shared read transaction.
        try:
            request = CalibrationReportQuery(
                requirements=profile.requirements
                if query.requirement_ids is None
                else profile.select(query.requirement_ids),
                context=query.context,
                history_limit=query.history_limit,
            )
        except ValueError as error:
            raise BackendConflict(
                "profile report requires a valid selection with at most "
                "32 requirements "
                "including prerequisites and a history budget of 2000 requests: "
                f"{error}"
            ) from error
        return self._checks.report(request).model_copy(update={"profile_id": identity})
