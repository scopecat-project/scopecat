"""Retry authorization cannot resolve an earlier physical execution's unknowns."""

from experiments.result_ledger.core import Store


def test_authorized_retry_preserves_original_physical_unknown(tmp_path):
    path = tmp_path / "certainty.db"
    with Store(path) as store:
        store.initialize(
            "certainty",
            [{"id": "p", "coordinates": {"x": {"value": 1, "unit": "V"}}}],
            [{"id": "u", "point_ids": ["p"], "required_outputs": ["signal"]}],
            [{"id": "g", "unit_ids": ["u"], "closed": True}],
        )
        original_fence = store.acquire("original-executor")
        original = store.begin_attempt(original_fence, "u")
        assert store.state()["attempts"][0]["physical_status"] == "unconfirmed"

    with Store(path) as store:
        retry_fence = store.acquire("operator-alice")
        assert store.reconcile_unknowns(retry_fence) == [original]
        before = store.state()["attempts"][0]
        assert before["physical_status"] == "unknown"
        assert before["reason"]
        authorization = "Synthetic device checked; accept possible duplicate action"
        store.authorize_retry(retry_fence, original, authorization)
        authorized = store.state()["attempts"][0]
        assert authorized["status"] == "retry_authorized"
        assert authorized["physical_status"] == "unknown"
        assert authorized["reason"] == before["reason"]
        assert authorized["retry_reason"] == authorization
        assert authorized["retry_authorized_by"] == "operator-alice"
        assert authorized["retry_authorized_fence"] == retry_fence

        # An idempotent request under another owner cannot rewrite who authorized it.
        current = store.acquire("retry-executor")
        store.authorize_retry(current, original, authorization)
        assert store.state()["attempts"][0] == authorized
        retry = store.begin_attempt(current, "u")
        assert retry != original
        store.save_result(
            current,
            retry,
            [
                {
                    "id": "signal",
                    "point_ids": ["p"],
                    "dims": ["point"],
                    "shape": [1],
                    "dtype": "float64",
                    "unit": "V",
                    "values": [2.0],
                    "availability": [True],
                    "coordinates": {},
                    "provenance": {"source": "synthetic"},
                }
            ],
            {"provider": "synthetic"},
        )
        attempts = store.state()["attempts"]
        assert attempts[0] == authorized
        assert attempts[1]["physical_status"] == "result_observed"
        assert attempts[1]["retry_reason"] is None

    with Store(path, readonly=True) as reader:
        attempts = reader.state()["attempts"]
        assert attempts[0]["physical_status"] == "unknown"
        assert attempts[1]["physical_status"] == "result_observed"
