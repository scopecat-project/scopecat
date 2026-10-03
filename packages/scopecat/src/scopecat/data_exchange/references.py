"""Scientific analysis edges shared by store capture and portable verification."""

from collections.abc import Iterable, Iterator

from scopecat.measurements.datasets import MEASUREMENT_DATASET_CODEC
from scopecat.records.analysis import (
    CONFIGURATION_ANALYSIS_INPUT_CODEC,
    AnalysisFigureRecordOutput,
    AnalysisPublishedDatasetViewSource,
    AnalysisPublishedOutputReference,
    AnalysisRecord,
    AnalysisSubject,
    ConfigurationAnalysisRecordInput,
    InterpretationAnalysisRecordInput,
    MeasurementAnalysisRecordInput,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
    published_output_input_identity,
)
from scopecat.runs.refs import CONFIG_PROFILE_SNAPSHOT_REF

from .models import AnalysisEvidence, ScientificEvidence


def published_dependencies(
    record: AnalysisRecord,
) -> Iterator[tuple[AnalysisPublishedOutputReference, tuple[str, str, str, str]]]:
    for item in record.inputs:
        if isinstance(item, PublishedAnalysisRecordInput):
            yield item.source, (item.kind, item.target, item.content_hash, item.codec)
    for output in record.outputs:
        if isinstance(output, AnalysisFigureRecordOutput):
            for layer in output.content.layers:
                source = layer.source
                if isinstance(source, AnalysisPublishedDatasetViewSource):
                    yield (
                        source.source,
                        (
                            "analysis_dataset",
                            source.dataset.dataset_id,
                            source.dataset.content_hash,
                            source.dataset.codec,
                        ),
                    )


def validate_published_references(analyses: Iterable[AnalysisEvidence]) -> None:
    indexed: dict[tuple[AnalysisSubject, str], AnalysisEvidence] = {}
    for analysis in analyses:
        identity = (analysis.record.subject, analysis.entry.id)
        if identity in indexed:
            raise ValueError("exchange contains duplicate analysis identities")
        indexed[identity] = analysis
    for analysis in indexed.values():
        for reference, expected in published_dependencies(analysis.record):
            source = indexed.get((reference.subject, reference.analysis_record_id))
            if source is None:
                raise ValueError("analysis input publication is missing from exchange")
            output = next(
                (
                    item
                    for item in source.record.outputs
                    if item.id == reference.output_id
                ),
                None,
            )
            if published_output_input_identity(output) != expected:
                raise ValueError(
                    "analysis input evidence differs from its exact output"
                )


def validate_analysis_references(evidence: ScientificEvidence) -> None:
    validate_published_references(evidence.analyses)
    runs = {run.snapshot.run_id: run for run in evidence.runs}
    interpretations = {item.reference: item for item in evidence.interpretations}
    if len(interpretations) != len(evidence.interpretations):
        raise ValueError("exchange contains duplicate interpretation identities")
    for analysis in evidence.analyses:
        if (
            isinstance(analysis.record.subject, RunAnalysisSubject)
            and analysis.record.subject.run_id not in runs
        ):
            raise ValueError("analysis subject run is missing from exchange")
        for item in analysis.record.inputs:
            if isinstance(
                item, ConfigurationAnalysisRecordInput | MeasurementAnalysisRecordInput
            ):
                run = runs.get(item.run_id)
                if run is None:
                    raise ValueError("analysis input run is missing from exchange")
                if isinstance(item, ConfigurationAnalysisRecordInput):
                    if (
                        item.target != CONFIG_PROFILE_SNAPSHOT_REF
                        or item.codec != CONFIGURATION_ANALYSIS_INPUT_CODEC
                        or item.content_hash != run.snapshot.config_content_hash
                    ):
                        raise ValueError(
                            "configuration input differs from retained run"
                        )
                else:
                    entry = next(
                        (
                            entry
                            for entry in run.contents
                            if entry.role == "dataset" and entry.id == item.target
                        ),
                        None,
                    )
                    if (
                        entry is None
                        or entry.kind
                        not in {"measurement_dataset", "measurement_slice"}
                        or entry.content_hash != item.content_hash
                        or item.codec != MEASUREMENT_DATASET_CODEC
                    ):
                        raise ValueError(
                            "measurement input differs from retained dataset"
                        )
            elif isinstance(item, InterpretationAnalysisRecordInput):
                if item.source not in interpretations:
                    raise ValueError("analysis interpretation is missing from exchange")
                if (
                    item.target != item.source.step_key
                    or item.content_hash != item.source.response_hash
                    or item.codec != "scopecat.interpretation-response.v1"
                ):
                    raise ValueError(
                        "interpretation input differs from retained judgment"
                    )
