"""Review fences are retained separately from immutable candidate identity."""

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run import AnalysisCandidateRunConfigSource, RunSnapshot


def test_old_candidate_source_round_trips_without_hash_change() -> None:
    old = {
        "kind": "analysis_candidate",
        "source_run_id": "baseline",
        "analysis_record_id": "analysis-fit-r1",
        "proposal_id": "carrier",
        "base_config_content_hash": "sha256:" + "a" * 64,
        "content_hash": "sha256:" + "b" * 64,
    }
    source = AnalysisCandidateRunConfigSource.model_validate(old)
    assert source.registry_generation is None
    assert source.model_dump(mode="json") == old
    assert sha256_json_hash(source.model_dump(mode="json")) == sha256_json_hash(old)
    run = RunSnapshot(
        run_id="candidate",
        config_content_hash=source.content_hash,
        config_source=source,
    )
    assert run.model_dump(mode="json")["config_source"] == old
    assert RunSnapshot.model_validate_json(run.model_dump_json()) == run


def test_review_generation_is_retained_but_not_candidate_request_identity() -> None:
    source = AnalysisCandidateRunConfigSource(
        source_run_id="baseline",
        analysis_record_id="analysis-fit-r1",
        proposal_id="carrier",
        base_config_content_hash="sha256:" + "a" * 64,
        content_hash="sha256:" + "b" * 64,
        registry_generation=3,
    )
    assert (
        AnalysisCandidateRunConfigSource.model_validate_json(
            source.model_dump_json()
        ).registry_generation
        == 3
    )
    first = LaunchRequest(
        action="preview", experiment="signal", version="v1", config_source=source
    )
    next_review = first.model_copy(
        update={"config_source": source.model_copy(update={"registry_generation": 4})}
    )
    assert first.request_hash == next_review.request_hash
    other_candidate = first.model_copy(
        update={"config_source": source.model_copy(update={"proposal_id": "other"})}
    )
    assert first.request_hash != other_candidate.request_hash
