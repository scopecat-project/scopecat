"""Internal canonical local run storage refs."""

from pathlib import PurePosixPath

ARTIFACTS_DIR = "artifacts"
DATASETS_DIR = "data"
RECORDS_DIR = "records"
EXECUTION_DIR = "execution"
CONFIG_PROFILE_SNAPSHOT_REF = "config-profile.snapshot.json"
RUN_REQUEST_REF = "run-request.json"
SCIENTIFIC_BINDING_REF = "scientific-binding.json"


def artifact_content_ref(*, artifact_id: str, kind: str) -> str:
    _validate_storage_segment(artifact_id)
    _validate_storage_segment(kind)
    return f"{ARTIFACTS_DIR}/{kind}/{artifact_id}"


def dataset_content_ref(*, dataset_id: str, kind: str) -> str:
    _validate_storage_segment(dataset_id)
    _validate_storage_segment(kind)
    return f"{DATASETS_DIR}/{kind}/{dataset_id}"


def record_content_ref(*, record_id: str, kind: str) -> str:
    _validate_storage_segment(record_id)
    _validate_storage_segment(kind)
    return f"{RECORDS_DIR}/{kind}/{record_id}.json"


def _validate_storage_segment(value: str) -> None:
    if not value or "\\" in value:
        msg = f"storage ref segment must be a single path segment: {value!r}"
        raise ValueError(msg)
    path = PurePosixPath(value)
    if path.name != value or path.is_absolute() or ".." in path.parts:
        msg = f"storage ref segment must be a single path segment: {value!r}"
        raise ValueError(msg)
