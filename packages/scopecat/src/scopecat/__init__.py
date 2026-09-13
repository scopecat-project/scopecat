# ruff: noqa: F401
# pyright: reportUnusedImport=false, reportUnsupportedDunderAll=false
"""High-level experiment authoring facade with one notebook entry point.

The root exports authoring tools and ``open_project``. Returned workflow
handles stay in their owner modules so the root does not become a second API
for every subsystem. Program IR and generated-client implementation types are
imported from their owner modules by extensions instead of being re-exported
here. Imports are lazy to keep the dependency graph cold.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scopecat.adaptive_domains import (
        AdaptiveRegion,
        DomainProposalAttempt,
        RegionOptimizationComplete,
        ResolvedDomainAxis,
        ResolvedDomainFragment,
    )
    from scopecat.analysis.datasets import (
        DerivedDataset,
        DerivedDatasetField,
        DerivedDatasetSchema,
        derived_dataset,
    )
    from scopecat.analysis.facts import AnalysisFactSchema
    from scopecat.api.analysis import (
        Analysis,
        AnalysisContext,
        AnalysisField,
        AnalysisPlot,
        AnalysisProducts,
        analysis_function,
        analysis_step,
    )
    from scopecat.api.calibration_planner import CalibrationPlanningContext
    from scopecat.api.instruments import (
        InstrumentClientFactory,
        InstrumentRef,
        TemporaryInstrumentRef,
        instrument,
        temporary_instrument,
    )
    from scopecat.api.parameters import TypedParameterTable
    from scopecat.api.procedure_planner import ProcedurePlanningContext
    from scopecat.api.procedures import LabProcedureContext
    from scopecat.api.published_analysis import (
        AnalysisResult,
        PublishedAnalysis,
        PublishedAnalysisArtifact,
    )
    from scopecat.authoring import (
        ANY_RESOURCE_ROLE,
        DEFAULT_RESOURCE_ROLE,
        ArrayDimension,
        ArrayType,
        Axis,
        BoolType,
        CapabilityResource,
        ComplexType,
        Control,
        ControlSet,
        ControlSpec,
        ControlValidationContext,
        CoordinateRef,
        DataRef,
        EachEntity,
        EntityAcquisitionPolicy,
        EntityAcquisitionSemantics,
        EntityAxisDef,
        EntityType,
        Experiment,
        ExperimentContext,
        ExperimentInvocation,
        ExperimentModule,
        ExperimentRequest,
        FloatType,
        Input,
        IntType,
        ModuleContext,
        ModuleInvocation,
        OneEntity,
        PayloadType,
        PerEntity,
        ProductBundle,
        ProductRef,
        ProductValueSpec,
        QuantityType,
        RecordedProducts,
        RecordRef,
        ResourceRoleSelector,
        Result,
        ScalarType,
        Scan,
        StringType,
        Symbolic,
        TableColumn,
        TableType,
        ValueRef,
        ValueType,
        axis,
        capability_resource,
        constant,
        coordinate,
        each,
        ensure_state_targets,
        experiment,
        input_ref,
        module,
        one,
        parameter,
        parameter_catalog,
        parameter_lookup,
        resource_role,
    )
    from scopecat.authoring.parameter_dataclasses import (
        ParameterSpec,
        dataclass_table_schema,
    )
    from scopecat.authoring.parameter_fields import ParameterColumn, column
    from scopecat.authoring.parameter_models import (
        Magnitude,
        Param,
        ParameterModel,
        param,
        parameter_definition,
        parameter_ref,
        parameter_snapshot,
        parameter_table_name,
        parameter_table_ref,
        parameter_update,
        quantity,
    )
    from scopecat.automation import (
        CalibrationDefinition,
        CalibrationDependencyEvidence,
        CalibrationDependencyRequirement,
        CalibrationObservation,
        CalibrationRegistry,
        CalibrationTargetRef,
        IntervalOccurrence,
        IntervalTrigger,
        ProcedureScheduleDefinition,
        ProcedureScheduleRegistry,
        calibration,
        interval_schedule,
        procedure,
    )
    from scopecat.config.parameters import (
        delete_parameter_rows,
        insert_parameter_rows,
        replace_scalar_parameter,
        replace_table_parameter,
        update_parameter_rows,
    )
    from scopecat.kernel.entity import (
        EntityRef,
        entity_ref,
    )
    from scopecat.kernel.quantity import Quantity
    from scopecat.optimization import (
        AdaptiveDomainPlan,
        CompletedPointObservation,
        DomainOptimizer,
        DomainOptimizerContext,
        DomainProposalDecision,
        DomainProposalLedger,
        DomainProposalSummary,
        OptimizationComplete,
        OptimizerMeasurementObservation,
        OptimizerScalarObservation,
        OptimizerUnavailableObservation,
    )
    from scopecat.planning.system import ExperimentSystem
    from scopecat.project import open_project
    from scopecat.sdk.payloads import (
        PayloadCodec,
        PayloadCodecCatalog,
        PayloadCodecDescription,
        PayloadCodecRegistry,
        PayloadContract,
        byte_payload_codec,
    )
    from scopecat.sdk.structured_payloads import (
        FrozenFloat64Vector,
        FrozenInt16Vector,
        StructuredPayloadError,
        StructuredValueCodec,
        pydantic_buffer_bundle_codec,
        pydantic_buffer_bundle_value_codec,
    )

_EXPORTS: dict[str, tuple[str, str]] = {
    "ANY_RESOURCE_ROLE": ("scopecat.authoring", "ANY_RESOURCE_ROLE"),
    "ArrayDimension": ("scopecat.authoring", "ArrayDimension"),
    "ArrayType": ("scopecat.authoring", "ArrayType"),
    "Axis": ("scopecat.authoring", "Axis"),
    "BoolType": ("scopecat.authoring", "BoolType"),
    "CapabilityResource": ("scopecat.authoring", "CapabilityResource"),
    "Control": ("scopecat.authoring", "Control"),
    "ControlSet": ("scopecat.authoring", "ControlSet"),
    "ControlSpec": ("scopecat.authoring", "ControlSpec"),
    "ControlValidationContext": ("scopecat.authoring", "ControlValidationContext"),
    "CoordinateRef": ("scopecat.authoring", "CoordinateRef"),
    "DataRef": ("scopecat.authoring", "DataRef"),
    "DEFAULT_RESOURCE_ROLE": ("scopecat.authoring", "DEFAULT_RESOURCE_ROLE"),
    "DerivedDataset": ("scopecat.analysis.datasets", "DerivedDataset"),
    "DerivedDatasetField": (
        "scopecat.analysis.datasets",
        "DerivedDatasetField",
    ),
    "DerivedDatasetSchema": (
        "scopecat.analysis.datasets",
        "DerivedDatasetSchema",
    ),
    "EachEntity": ("scopecat.authoring", "EachEntity"),
    "EntityAcquisitionPolicy": (
        "scopecat.authoring",
        "EntityAcquisitionPolicy",
    ),
    "EntityAcquisitionSemantics": (
        "scopecat.authoring",
        "EntityAcquisitionSemantics",
    ),
    "EntityAxisDef": ("scopecat.authoring", "EntityAxisDef"),
    "EntityType": ("scopecat.authoring", "EntityType"),
    "Experiment": ("scopecat.authoring", "Experiment"),
    "ExperimentContext": ("scopecat.authoring", "ExperimentContext"),
    "ExperimentInvocation": ("scopecat.authoring", "ExperimentInvocation"),
    "ExperimentRequest": ("scopecat.authoring", "ExperimentRequest"),
    "Scan": ("scopecat.authoring", "Scan"),
    "ExperimentModule": ("scopecat.authoring", "ExperimentModule"),
    "FloatType": ("scopecat.authoring", "FloatType"),
    "ComplexType": ("scopecat.authoring", "ComplexType"),
    "Input": ("scopecat.authoring", "Input"),
    "IntType": ("scopecat.authoring", "IntType"),
    "ModuleContext": ("scopecat.authoring", "ModuleContext"),
    "ModuleInvocation": ("scopecat.authoring", "ModuleInvocation"),
    "OneEntity": ("scopecat.authoring", "OneEntity"),
    "PayloadType": ("scopecat.authoring", "PayloadType"),
    "PerEntity": ("scopecat.authoring", "PerEntity"),
    "ProductRef": ("scopecat.authoring", "ProductRef"),
    "ProductBundle": ("scopecat.authoring", "ProductBundle"),
    "ProductValueSpec": ("scopecat.authoring", "ProductValueSpec"),
    "QuantityType": ("scopecat.authoring", "QuantityType"),
    "RecordRef": ("scopecat.authoring", "RecordRef"),
    "RecordedProducts": ("scopecat.authoring", "RecordedProducts"),
    "Result": ("scopecat.authoring", "Result"),
    "ResourceRoleSelector": ("scopecat.authoring", "ResourceRoleSelector"),
    "ScalarType": ("scopecat.authoring", "ScalarType"),
    "StringType": ("scopecat.authoring", "StringType"),
    "Symbolic": ("scopecat.authoring", "Symbolic"),
    "TableColumn": ("scopecat.authoring", "TableColumn"),
    "TableType": ("scopecat.authoring", "TableType"),
    "ValueRef": ("scopecat.authoring", "ValueRef"),
    "ValueType": ("scopecat.authoring", "ValueType"),
    "coordinate": ("scopecat.authoring", "coordinate"),
    "capability_resource": ("scopecat.authoring", "capability_resource"),
    "constant": ("scopecat.authoring", "constant"),
    "each": ("scopecat.authoring", "each"),
    "ensure_state_targets": ("scopecat.authoring", "ensure_state_targets"),
    "experiment": ("scopecat.authoring", "experiment"),
    "input_ref": ("scopecat.authoring", "input_ref"),
    "module": ("scopecat.authoring", "module"),
    "one": ("scopecat.authoring", "one"),
    "column": ("scopecat.authoring.parameter_fields", "column"),
    "ParameterColumn": ("scopecat.authoring.parameter_fields", "ParameterColumn"),
    "parameter": ("scopecat.authoring", "parameter"),
    "parameter_catalog": ("scopecat.authoring.parameter_models", "parameter_catalog"),
    "parameter_lookup": ("scopecat.authoring", "parameter_lookup"),
    "procedure": ("scopecat.automation", "procedure"),
    "interval_schedule": ("scopecat.automation", "interval_schedule"),
    "resource_role": ("scopecat.authoring", "resource_role"),
    "axis": ("scopecat.authoring", "axis"),
    "ExperimentSystem": ("scopecat.planning.system", "ExperimentSystem"),
    "PayloadCodec": ("scopecat.sdk.payloads", "PayloadCodec"),
    "PayloadCodecCatalog": ("scopecat.sdk.payloads", "PayloadCodecCatalog"),
    "PayloadCodecDescription": (
        "scopecat.sdk.payloads",
        "PayloadCodecDescription",
    ),
    "PayloadCodecRegistry": ("scopecat.sdk.payloads", "PayloadCodecRegistry"),
    "PayloadContract": ("scopecat.sdk.payloads", "PayloadContract"),
    "byte_payload_codec": ("scopecat.sdk.payloads", "byte_payload_codec"),
    "FrozenFloat64Vector": (
        "scopecat.sdk.structured_payloads",
        "FrozenFloat64Vector",
    ),
    "FrozenInt16Vector": (
        "scopecat.sdk.structured_payloads",
        "FrozenInt16Vector",
    ),
    "StructuredPayloadError": (
        "scopecat.sdk.structured_payloads",
        "StructuredPayloadError",
    ),
    "StructuredValueCodec": (
        "scopecat.sdk.structured_payloads",
        "StructuredValueCodec",
    ),
    "pydantic_buffer_bundle_codec": (
        "scopecat.sdk.structured_payloads",
        "pydantic_buffer_bundle_codec",
    ),
    "pydantic_buffer_bundle_value_codec": (
        "scopecat.sdk.structured_payloads",
        "pydantic_buffer_bundle_value_codec",
    ),
    "EntityRef": ("scopecat.kernel.entity", "EntityRef"),
    "entity_ref": ("scopecat.kernel.entity", "entity_ref"),
    "Magnitude": ("scopecat.authoring.parameter_models", "Magnitude"),
    "Param": ("scopecat.authoring.parameter_models", "Param"),
    "parameter_definition": (
        "scopecat.authoring.parameter_models",
        "parameter_definition",
    ),
    "parameter_snapshot": ("scopecat.authoring.parameter_models", "parameter_snapshot"),
    "parameter_table_ref": (
        "scopecat.authoring.parameter_models",
        "parameter_table_ref",
    ),
    "parameter_table_name": (
        "scopecat.authoring.parameter_models",
        "parameter_table_name",
    ),
    "parameter_update": ("scopecat.authoring.parameter_models", "parameter_update"),
    "ParameterModel": ("scopecat.authoring.parameter_models", "ParameterModel"),
    "param": ("scopecat.authoring.parameter_models", "param"),
    "quantity": ("scopecat.authoring.parameter_models", "quantity"),
    "parameter_ref": ("scopecat.authoring.parameter_models", "parameter_ref"),
    "ParameterSpec": ("scopecat.authoring.parameter_dataclasses", "ParameterSpec"),
    "dataclass_table_schema": (
        "scopecat.authoring.parameter_dataclasses",
        "dataclass_table_schema",
    ),
    "TypedParameterTable": ("scopecat.api.parameters", "TypedParameterTable"),
    "delete_parameter_rows": ("scopecat.config.parameters", "delete_parameter_rows"),
    "derived_dataset": ("scopecat.analysis.datasets", "derived_dataset"),
    "insert_parameter_rows": ("scopecat.config.parameters", "insert_parameter_rows"),
    "replace_scalar_parameter": (
        "scopecat.config.parameters",
        "replace_scalar_parameter",
    ),
    "replace_table_parameter": (
        "scopecat.config.parameters",
        "replace_table_parameter",
    ),
    "update_parameter_rows": ("scopecat.config.parameters", "update_parameter_rows"),
    "Analysis": ("scopecat.api.analysis", "Analysis"),
    "AnalysisFactSchema": ("scopecat.analysis.facts", "AnalysisFactSchema"),
    "AnalysisContext": ("scopecat.api.analysis", "AnalysisContext"),
    "AnalysisField": ("scopecat.api.analysis", "AnalysisField"),
    "PublishedAnalysis": (
        "scopecat.api.published_analysis",
        "PublishedAnalysis",
    ),
    "PublishedAnalysisArtifact": (
        "scopecat.api.published_analysis",
        "PublishedAnalysisArtifact",
    ),
    "InstrumentClientFactory": (
        "scopecat.api.instruments",
        "InstrumentClientFactory",
    ),
    "InstrumentRef": ("scopecat.api.instruments", "InstrumentRef"),
    "CalibrationPlanningContext": (
        "scopecat.api.calibration_planner",
        "CalibrationPlanningContext",
    ),
    "CalibrationDefinition": ("scopecat.automation", "CalibrationDefinition"),
    "CalibrationDependencyEvidence": (
        "scopecat.automation",
        "CalibrationDependencyEvidence",
    ),
    "CalibrationDependencyRequirement": (
        "scopecat.automation",
        "CalibrationDependencyRequirement",
    ),
    "CalibrationObservation": ("scopecat.automation", "CalibrationObservation"),
    "CalibrationRegistry": ("scopecat.automation", "CalibrationRegistry"),
    "CalibrationTargetRef": ("scopecat.automation", "CalibrationTargetRef"),
    "IntervalOccurrence": ("scopecat.automation", "IntervalOccurrence"),
    "IntervalTrigger": ("scopecat.automation", "IntervalTrigger"),
    "LabProcedureContext": (
        "scopecat.api.procedures",
        "LabProcedureContext",
    ),
    "ProcedurePlanningContext": (
        "scopecat.api.procedure_planner",
        "ProcedurePlanningContext",
    ),
    "ProcedureScheduleDefinition": (
        "scopecat.automation",
        "ProcedureScheduleDefinition",
    ),
    "ProcedureScheduleRegistry": (
        "scopecat.automation",
        "ProcedureScheduleRegistry",
    ),
    "TemporaryInstrumentRef": (
        "scopecat.api.instruments",
        "TemporaryInstrumentRef",
    ),
    "Quantity": ("scopecat.kernel.quantity", "Quantity"),
    "AdaptiveRegion": ("scopecat.adaptive_domains", "AdaptiveRegion"),
    "DomainProposalAttempt": (
        "scopecat.adaptive_domains",
        "DomainProposalAttempt",
    ),
    "RegionOptimizationComplete": (
        "scopecat.adaptive_domains",
        "RegionOptimizationComplete",
    ),
    "ResolvedDomainAxis": (
        "scopecat.adaptive_domains",
        "ResolvedDomainAxis",
    ),
    "ResolvedDomainFragment": (
        "scopecat.adaptive_domains",
        "ResolvedDomainFragment",
    ),
    "AdaptiveDomainPlan": ("scopecat.optimization", "AdaptiveDomainPlan"),
    "CompletedPointObservation": (
        "scopecat.optimization",
        "CompletedPointObservation",
    ),
    "OptimizationComplete": ("scopecat.optimization", "OptimizationComplete"),
    "OptimizerMeasurementObservation": (
        "scopecat.optimization",
        "OptimizerMeasurementObservation",
    ),
    "OptimizerScalarObservation": (
        "scopecat.optimization",
        "OptimizerScalarObservation",
    ),
    "OptimizerUnavailableObservation": (
        "scopecat.optimization",
        "OptimizerUnavailableObservation",
    ),
    "DomainOptimizer": ("scopecat.optimization", "DomainOptimizer"),
    "DomainOptimizerContext": (
        "scopecat.optimization",
        "DomainOptimizerContext",
    ),
    "DomainProposalDecision": (
        "scopecat.optimization",
        "DomainProposalDecision",
    ),
    "DomainProposalLedger": (
        "scopecat.optimization",
        "DomainProposalLedger",
    ),
    "DomainProposalSummary": (
        "scopecat.optimization",
        "DomainProposalSummary",
    ),
    "open_project": ("scopecat.project", "open_project"),
    "AnalysisResult": ("scopecat.api.published_analysis", "AnalysisResult"),
    "AnalysisProducts": ("scopecat.api.analysis", "AnalysisProducts"),
    "AnalysisPlot": ("scopecat.api.analysis", "AnalysisPlot"),
    "analysis_function": ("scopecat.api.analysis", "analysis_function"),
    "analysis_step": ("scopecat.api.analysis", "analysis_step"),
    "calibration": ("scopecat.automation", "calibration"),
    "instrument": ("scopecat.api.instruments", "instrument"),
    "temporary_instrument": (
        "scopecat.api.instruments",
        "temporary_instrument",
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


__all__ = sorted(_EXPORTS)
