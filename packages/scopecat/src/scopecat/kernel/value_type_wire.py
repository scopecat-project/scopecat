"""Pydantic wire models for durable scalar value type declarations."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictInt,
    StrictStr,
    TypeAdapter,
)

from scopecat.kernel.value_types import (
    Bool,
    Complex,
    Entity,
    Float,
    Int,
    Payload,
    Quantity,
    Scalar,
    String,
)

type _FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
type _NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
type _Choices = Annotated[tuple[StrictStr, ...], Field(min_length=1)]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _BoolWire(_WireModel):
    type: Literal["bool"]


class _IntWire(_WireModel):
    type: Literal["int"]
    minimum: StrictInt | None = None
    maximum: StrictInt | None = None


class _FiniteFloatWire(_WireModel):
    type: Literal["float"]
    minimum: _FiniteNumber | None = None
    maximum: _FiniteNumber | None = None
    finite: Literal[True] = True


class _StringWire(_WireModel):
    type: Literal["string"]
    choices: _Choices | None = None


class _FiniteQuantityWire(_WireModel):
    type: Literal["quantity"]
    dimension: StrictStr | None = None
    unit: StrictStr | None = None
    minimum: _FiniteNumber | None = None
    maximum: _FiniteNumber | None = None
    finite: Literal[True] = True


class _EntityWire(_WireModel):
    type: Literal["entity"]
    entity_kind: _NonEmptyString | None = None


class _PayloadWire(_WireModel):
    type: Literal["payload"]
    schema_id: _NonEmptyString


type _PersistableScalarModel = Annotated[
    _BoolWire
    | _IntWire
    | _FiniteFloatWire
    | _StringWire
    | _FiniteQuantityWire
    | _EntityWire,
    Field(discriminator="type"),
]
type _InstrumentPropertyScalarModel = Annotated[
    _BoolWire | _IntWire | _FiniteFloatWire | _StringWire | _FiniteQuantityWire,
    Field(discriminator="type"),
]
type _InstrumentOperationScalarModel = Annotated[
    _BoolWire
    | _IntWire
    | _FiniteFloatWire
    | _StringWire
    | _FiniteQuantityWire
    | _PayloadWire,
    Field(discriminator="type"),
]

_PERSISTABLE_SCALAR_ADAPTER = TypeAdapter[_PersistableScalarModel](
    _PersistableScalarModel
)
_INSTRUMENT_PROPERTY_SCALAR_ADAPTER = TypeAdapter[_InstrumentPropertyScalarModel](
    _InstrumentPropertyScalarModel
)
_INSTRUMENT_OPERATION_SCALAR_ADAPTER = TypeAdapter[_InstrumentOperationScalarModel](
    _InstrumentOperationScalarModel
)


def _scalar_from_model(
    wire: (
        _PersistableScalarModel
        | _InstrumentPropertyScalarModel
        | _InstrumentOperationScalarModel
    ),
) -> Scalar:
    match wire:
        case _BoolWire():
            atom = Bool()
        case _IntWire(minimum=minimum, maximum=maximum):
            atom = Int(minimum=minimum, maximum=maximum)
        case _FiniteFloatWire(
            minimum=minimum,
            maximum=maximum,
            finite=finite,
        ):
            atom = Float(minimum=minimum, maximum=maximum, finite=finite)
        case _StringWire(choices=choices):
            atom = String(choices=choices)
        case _FiniteQuantityWire(
            dimension=dimension,
            unit=unit,
            minimum=minimum,
            maximum=maximum,
            finite=finite,
        ):
            atom = Quantity(
                dimension=dimension,
                unit=unit,
                minimum=minimum,
                maximum=maximum,
                finite=finite,
            )
        case _EntityWire(entity_kind=entity_kind):
            atom = Entity(entity_kind=entity_kind)
        case _PayloadWire(schema_id=schema_id):
            atom = Payload(schema_id=schema_id)
    return Scalar(atom=atom)


def _persistable_scalar_from_wire(value: object) -> Scalar:
    if isinstance(value, Scalar):
        return value
    return _scalar_from_model(_PERSISTABLE_SCALAR_ADAPTER.validate_python(value))


def _instrument_operation_scalar_from_wire(value: object) -> Scalar:
    if isinstance(value, Scalar):
        return value
    return _scalar_from_model(
        _INSTRUMENT_OPERATION_SCALAR_ADAPTER.validate_python(value)
    )


def _instrument_property_scalar_from_wire(value: object) -> Scalar:
    if isinstance(value, Scalar):
        return value
    return _scalar_from_model(
        _INSTRUMENT_PROPERTY_SCALAR_ADAPTER.validate_python(value)
    )


def _scalar_to_wire(
    value: Scalar,
) -> (
    _PersistableScalarModel
    | _InstrumentPropertyScalarModel
    | _InstrumentOperationScalarModel
):
    match value.atom:
        case Bool():
            return _BoolWire(type="bool")
        case Int(minimum=minimum, maximum=maximum):
            return _IntWire(type="int", minimum=minimum, maximum=maximum)
        case Float(minimum=minimum, maximum=maximum, finite=True):
            return _FiniteFloatWire(
                type="float",
                minimum=minimum,
                maximum=maximum,
            )
        case String(choices=choices):
            return _StringWire(type="string", choices=choices)
        case Quantity(
            dimension=dimension,
            unit=unit,
            minimum=minimum,
            maximum=maximum,
            finite=True,
        ):
            return _FiniteQuantityWire(
                type="quantity",
                dimension=dimension,
                unit=unit,
                minimum=minimum,
                maximum=maximum,
            )
        case Entity(entity_kind=entity_kind):
            return _EntityWire(type="entity", entity_kind=entity_kind)
        case Payload(schema_id=schema_id):
            return _PayloadWire(type="payload", schema_id=schema_id)
        case Complex():
            raise TypeError(
                "complex scalar is unsupported by configuration/instrument contracts"
            )
        case Float() | Quantity():
            msg = "durable scalar types must require finite numeric values"
            raise ValueError(msg)


type PersistableScalarWire = Annotated[
    Scalar,
    BeforeValidator(
        _persistable_scalar_from_wire,
        json_schema_input_type=_PersistableScalarModel,
    ),
    PlainSerializer(_scalar_to_wire, return_type=_PersistableScalarModel),
]

type InstrumentOperationScalarWire = Annotated[
    Scalar,
    BeforeValidator(
        _instrument_operation_scalar_from_wire,
        json_schema_input_type=_InstrumentOperationScalarModel,
    ),
    PlainSerializer(_scalar_to_wire, return_type=_InstrumentOperationScalarModel),
]

type InstrumentPropertyScalarWire = Annotated[
    Scalar,
    BeforeValidator(
        _instrument_property_scalar_from_wire,
        json_schema_input_type=_InstrumentPropertyScalarModel,
    ),
    PlainSerializer(_scalar_to_wire, return_type=_InstrumentPropertyScalarModel),
]
