"""High-level experiment authoring API.

This facade exposes the objects used to describe experiments. Domain and
generated-client implementations import lower-level schema and recorder types
from their owning modules.
"""

from scopecat.authoring._experiment_module import ExperimentModule
from scopecat.authoring._module_context import ModuleContext
from scopecat.authoring._module_invocation import (
    ModuleInvocation,
)
from scopecat.authoring._module_results import DataRef, ProductBundle, RecordedProducts
from scopecat.authoring.capability_resources import (
    CapabilityResource,
    capability_resource,
    ensure_state_targets,
)
from scopecat.authoring.definitions import (
    Experiment,
    ExperimentContext,
    Input,
    Result,
    Symbolic,
    experiment,
    input_ref,
    module,
)
from scopecat.authoring.entity_selection import (
    ConcreteEntityInput,
    EachEntity,
    EntityInput,
    EntitySelection,
    OneEntity,
    PerEntity,
    each,
    one,
)
from scopecat.authoring.experiments import (
    ExperimentInvocation,
)
from scopecat.authoring.instrument_recorder import (
    InstrumentRecorder,
    InstrumentResource,
    InstrumentStateTarget,
    instrument_recorder,
)
from scopecat.authoring.member_projection import (
    StateProjector,
    StateTarget,
)
from scopecat.authoring.parameters import (
    ParameterAssignment,
    ParameterCell,
    ParameterField,
    ParameterRow,
    ParameterRowKey,
    ParameterScalar,
    ParameterSchema,
    parameter_catalog,
    parameter_field,
    parameter_schema,
)
from scopecat.authoring.scans import Axis, PointRow, axis
from scopecat.kernel.resource_identity import (
    ANY_RESOURCE_ROLE,
    DEFAULT_RESOURCE_ROLE,
    ResourceRoleInput,
    ResourceRoleSelector,
    resource_role,
)
from scopecat.program.control_contract import ControlValidationContext
from scopecat.program.controls import Control, ControlSet
from scopecat.program.measurement_types import (
    EntityAcquisitionPolicy,
    EntityAcquisitionSemantics,
)
from scopecat.program.products import (
    EntityAxisDef,
    ProductRef,
    ProductValueSpec,
)
from scopecat.program.record_refs import RecordRef
from scopecat.program.value_types import (
    Array as ArrayType,
)
from scopecat.program.value_types import (
    ArrayDimension,
    TableColumn,
    ValueType,
    ValueValidationError,
)
from scopecat.program.value_types import (
    Bool as BoolType,
)
from scopecat.program.value_types import (
    Complex as ComplexType,
)
from scopecat.program.value_types import (
    Entity as EntityType,
)
from scopecat.program.value_types import (
    Float as FloatType,
)
from scopecat.program.value_types import (
    Int as IntType,
)
from scopecat.program.value_types import (
    Payload as PayloadType,
)
from scopecat.program.value_types import (
    Quantity as QuantityType,
)
from scopecat.program.value_types import (
    Scalar as ScalarType,
)
from scopecat.program.value_types import (
    String as StringType,
)
from scopecat.program.value_types import (
    Table as TableType,
)
from scopecat.program.values import (
    ComputeInput,
    CoordinateRef,
    MetadataValue,
    ModuleInput,
    ParameterKeyInput,
    RuntimeInput,
    ScalarInput,
    ValueRef,
    constant,
    coordinate,
    parameter,
    parameter_lookup,
)

__all__ = [
    "ANY_RESOURCE_ROLE",
    "DEFAULT_RESOURCE_ROLE",
    "ArrayDimension",
    "ArrayType",
    "Axis",
    "BoolType",
    "CapabilityResource",
    "ComplexType",
    "ComputeInput",
    "ConcreteEntityInput",
    "Control",
    "ControlSet",
    "ControlValidationContext",
    "CoordinateRef",
    "DataRef",
    "EachEntity",
    "EntityAcquisitionPolicy",
    "EntityAcquisitionSemantics",
    "EntityAxisDef",
    "EntityInput",
    "EntitySelection",
    "EntityType",
    "Experiment",
    "ExperimentContext",
    "ExperimentInvocation",
    "ExperimentModule",
    "FloatType",
    "Input",
    "InstrumentRecorder",
    "InstrumentResource",
    "InstrumentStateTarget",
    "IntType",
    "MetadataValue",
    "ModuleContext",
    "ModuleInput",
    "ModuleInvocation",
    "OneEntity",
    "ParameterAssignment",
    "ParameterCell",
    "ParameterField",
    "ParameterKeyInput",
    "ParameterRow",
    "ParameterRowKey",
    "ParameterScalar",
    "ParameterSchema",
    "PayloadType",
    "PerEntity",
    "PointRow",
    "ProductBundle",
    "ProductRef",
    "ProductValueSpec",
    "QuantityType",
    "RecordRef",
    "RecordedProducts",
    "ResourceRoleInput",
    "ResourceRoleSelector",
    "Result",
    "RuntimeInput",
    "ScalarInput",
    "ScalarType",
    "StateProjector",
    "StateTarget",
    "StringType",
    "Symbolic",
    "TableColumn",
    "TableType",
    "ValueRef",
    "ValueType",
    "ValueValidationError",
    "axis",
    "capability_resource",
    "constant",
    "coordinate",
    "each",
    "ensure_state_targets",
    "experiment",
    "input_ref",
    "instrument_recorder",
    "module",
    "one",
    "parameter",
    "parameter_catalog",
    "parameter_field",
    "parameter_lookup",
    "parameter_schema",
    "resource_role",
]
