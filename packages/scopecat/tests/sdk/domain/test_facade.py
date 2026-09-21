"""Public execution-domain SDK facade contracts."""

from __future__ import annotations

import subprocess
import sys

import scopecat.sdk.domain as domain

_DOMAIN_ADAPTER_CONTRACTS = {
    "execute_domain_batch",
    "DomainBatchInputs",
    "DomainBatchRequest",
    "DomainExecutionReceipt",
    "DomainExecutionResult",
    "DomainInvocationSpec",
    "DomainJobCheckpoint",
    "DomainJobRuntime",
    "DomainMappedResult",
    "DomainResultValue",
}


def test_domain_facade_exports_curated_adapter_contracts() -> None:
    assert set(domain.__all__) >= _DOMAIN_ADAPTER_CONTRACTS


def test_domain_facade_keeps_implementation_modules_cold() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import scopecat.sdk.domain

forbidden = {
    "numpy",
    "scopecat.sdk.domain.batch",
    "scopecat.sdk.domain.compiler",
    "scopecat.sdk.domain.execution",
    "scopecat.sdk.domain.job",
    "scopecat.sdk.domain.preparation",
    "scopecat.sdk.domain.result_mapping",
    "scopecat.sdk.domain.runtime",
    "scopecat.sdk.domain.view",
}
loaded = forbidden.intersection(sys.modules)
if loaded:
    raise SystemExit(f"domain facade imported implementations: {sorted(loaded)}")
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
