# pyright: reportUnusedImport=false, reportUnsupportedDunderAll=false
"""Lazy public facade for execution-domain compilers and runtimes."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scopecat.inspection import (
        CompiledArtifactInspection,
        CompiledInspectionBounds,
        CompiledInspectionFact,
        CompiledPointInspection,
        CompiledProgramInspection,
        CompiledProgramInspectionLayer,
        CompiledProgramInspectionLink,
        CompiledProgramInspectionNode,
        CompiledProgramInspectionPage,
        CompiledProgramInspectionQuery,
        CompiledWaveformInspection,
    )
    from scopecat.sdk.domain.batch import (
        DomainBatchInputs,
        DomainBatchRequest,
    )
    from scopecat.sdk.domain.compiler import (
        DomainBatchCandidate,
        DomainBatchPreparationCost,
        DomainBatchPreparationLimits,
        DomainCompiler,
    )
    from scopecat.sdk.domain.evidence import DomainExecutionEvidence
    from scopecat.sdk.domain.execution import (
        DomainResidencyAddress,
        DomainResidencyRequirement,
        DomainStateAddress,
        DomainStateRequirement,
        DomainTransitionPolicy,
        PreparedDomainExecution,
    )
    from scopecat.sdk.domain.job import (
        DomainInvocationSpec,
        DomainResultValue,
    )
    from scopecat.sdk.domain.preparation import DomainPreparationBuilder
    from scopecat.sdk.domain.result_mapping import (
        DomainMappedResult,
        DomainResultBinding,
        DomainResultMapping,
    )
    from scopecat.sdk.domain.runtime import (
        DomainExecutionId,
        DomainExecutionReceipt,
        DomainExecutionResult,
        DomainInstrumentExecutor,
        DomainJobCheckpoint,
        DomainJobRuntime,
        DomainJobTransition,
        DomainSetup,
        ResumableDomainJobRuntime,
    )
    from scopecat.sdk.domain.synchronous import execute_domain_batch
    from scopecat.sdk.domain.view import (
        DomainCallView,
        DomainInputPortView,
        DomainPointRef,
        DomainProductAxisView,
        DomainProductContractView,
        DomainProductUseRef,
        DomainProgramView,
        DomainResultBindingView,
        DomainResultPortView,
    )

_BATCH_EXPORTS = (
    "DomainBatchInputs",
    "DomainBatchRequest",
)
_EXECUTION_EXPORTS = (
    "DomainResidencyAddress",
    "DomainResidencyRequirement",
    "DomainStateAddress",
    "DomainStateRequirement",
    "DomainTransitionPolicy",
    "PreparedDomainExecution",
)
_EVIDENCE_EXPORTS = ("DomainExecutionEvidence",)
_JOB_EXPORTS = (
    "DomainInvocationSpec",
    "DomainResultValue",
)
_INSPECTION_EXPORTS = (
    "CompiledArtifactInspection",
    "CompiledInspectionBounds",
    "CompiledInspectionFact",
    "CompiledPointInspection",
    "CompiledProgramInspection",
    "CompiledProgramInspectionLayer",
    "CompiledProgramInspectionLink",
    "CompiledProgramInspectionNode",
    "CompiledProgramInspectionPage",
    "CompiledProgramInspectionQuery",
    "CompiledWaveformInspection",
)
_RESULT_MAPPING_EXPORTS = (
    "DomainMappedResult",
    "DomainResultBinding",
    "DomainResultMapping",
)
_RUNTIME_EXPORTS = (
    "DomainExecutionId",
    "DomainExecutionReceipt",
    "DomainExecutionResult",
    "DomainInstrumentExecutor",
    "DomainJobCheckpoint",
    "DomainJobRuntime",
    "DomainJobTransition",
    "DomainSetup",
    "ResumableDomainJobRuntime",
)
_VIEW_EXPORTS = (
    "DomainCallView",
    "DomainInputPortView",
    "DomainPointRef",
    "DomainProductAxisView",
    "DomainProductContractView",
    "DomainProductUseRef",
    "DomainProgramView",
    "DomainResultBindingView",
    "DomainResultPortView",
)
_EXPORTS = {
    "execute_domain_batch": ("scopecat.sdk.domain.synchronous", "execute_domain_batch"),
    **{name: ("scopecat.sdk.domain.batch", name) for name in _BATCH_EXPORTS},
    **{name: ("scopecat.sdk.domain.execution", name) for name in _EXECUTION_EXPORTS},
    **{name: ("scopecat.sdk.domain.evidence", name) for name in _EVIDENCE_EXPORTS},
    **{name: ("scopecat.sdk.domain.job", name) for name in _JOB_EXPORTS},
    **{name: ("scopecat.inspection", name) for name in _INSPECTION_EXPORTS},
    **{
        name: ("scopecat.sdk.domain.result_mapping", name)
        for name in _RESULT_MAPPING_EXPORTS
    },
    **{name: ("scopecat.sdk.domain.runtime", name) for name in _RUNTIME_EXPORTS},
    **{name: ("scopecat.sdk.domain.view", name) for name in _VIEW_EXPORTS},
    "DomainBatchCandidate": (
        "scopecat.sdk.domain.compiler",
        "DomainBatchCandidate",
    ),
    "DomainBatchPreparationCost": (
        "scopecat.sdk.domain.compiler",
        "DomainBatchPreparationCost",
    ),
    "DomainBatchPreparationLimits": (
        "scopecat.sdk.domain.compiler",
        "DomainBatchPreparationLimits",
    ),
    "DomainCompiler": ("scopecat.sdk.domain.compiler", "DomainCompiler"),
    "DomainPreparationBuilder": (
        "scopecat.sdk.domain.preparation",
        "DomainPreparationBuilder",
    ),
}


def __getattr__(name: str) -> object:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = cast("object", getattr(import_module(module_name), attribute_name))
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = [
    "CompiledArtifactInspection",
    "CompiledInspectionBounds",
    "CompiledInspectionFact",
    "CompiledPointInspection",
    "CompiledProgramInspection",
    "CompiledProgramInspectionLayer",
    "CompiledProgramInspectionLink",
    "CompiledProgramInspectionNode",
    "CompiledProgramInspectionPage",
    "CompiledProgramInspectionQuery",
    "CompiledWaveformInspection",
    "DomainBatchCandidate",
    "DomainBatchInputs",
    "DomainBatchPreparationCost",
    "DomainBatchPreparationLimits",
    "DomainBatchRequest",
    "DomainCallView",
    "DomainCompiler",
    "DomainExecutionEvidence",
    "DomainExecutionId",
    "DomainExecutionReceipt",
    "DomainExecutionResult",
    "DomainInputPortView",
    "DomainInstrumentExecutor",
    "DomainInvocationSpec",
    "DomainJobCheckpoint",
    "DomainJobRuntime",
    "DomainJobTransition",
    "DomainMappedResult",
    "DomainPointRef",
    "DomainPreparationBuilder",
    "DomainProductAxisView",
    "DomainProductContractView",
    "DomainProductUseRef",
    "DomainProgramView",
    "DomainResidencyAddress",
    "DomainResidencyRequirement",
    "DomainResultBinding",
    "DomainResultBindingView",
    "DomainResultMapping",
    "DomainResultPortView",
    "DomainResultValue",
    "DomainSetup",
    "DomainStateAddress",
    "DomainStateRequirement",
    "DomainTransitionPolicy",
    "PreparedDomainExecution",
    "ResumableDomainJobRuntime",
    "execute_domain_batch",
]
