"""Stable structural schemas for durable JSON analysis facts."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from types import UnionType
from typing import (
    Annotated,
    Literal,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    create_model,
)

from scopecat.kernel.content_identity import canonical_json, stable_content_hash
from scopecat.kernel.quantity import Quantity

ANALYSIS_FACT_SCHEMA_CODEC = "scopecat.analysis-fact-schema.v1"
SCALAR_FACT_SCHEMA_ID = "scopecat.scalar.v1"
QUANTITY_FACT_SCHEMA_ID = "scopecat.quantity.v1"

type ScalarFactValue = bool | int | float | str | None
type _FactEncoder[ValueT] = Callable[[ValueT], JsonValue]
type _FactDecoder[ValueT] = Callable[[JsonValue], ValueT]


def _schema_hash(schema: JsonValue) -> str:
    identity = {
        "codec": ANALYSIS_FACT_SCHEMA_CODEC,
        "schema": schema,
    }
    return f"sha256:{stable_content_hash(identity)}"


def analysis_fact_structure_hash(structure: JsonValue) -> str:
    """Return the stable identity of one durable fact structure."""

    return _schema_hash(structure)


_SCALAR_ADAPTER: TypeAdapter[ScalarFactValue] = TypeAdapter(
    ScalarFactValue,
    config=ConfigDict(strict=True),
)
_SCALAR_FACT_STRUCTURE: JsonValue = {
    "type": "union",
    "variants": [
        {"type": "bool"},
        {"type": "float"},
        {"type": "int"},
        {"type": "null"},
        {"type": "string"},
    ],
}
_QUANTITY_FACT_STRUCTURE: JsonValue = {
    "type": "quantity",
    "value": {"type": "float"},
    "unit": {"type": "string"},
}
_EMPTY_FACT_ANCESTORS: frozenset[type[object]] = frozenset()
SCALAR_FACT_SCHEMA_HASH = _schema_hash(_SCALAR_FACT_STRUCTURE)
QUANTITY_FACT_SCHEMA_HASH = _schema_hash(_QUANTITY_FACT_STRUCTURE)


@dataclass(frozen=True, slots=True)
class AnalysisFactSchema[ValueT]:
    """Local adapter for one versioned, structurally stable fact contract.

    ``schema_hash`` is derived from Scopecat's own structural type IR. Python
    class names, docstrings, default values, and Pydantic's generated JSON
    Schema are deliberately outside the durable identity.
    """

    id: str
    value_type: type[ValueT]
    schema_codec: Literal["scopecat.analysis-fact-schema.v1"] = field(
        init=False,
        default=ANALYSIS_FACT_SCHEMA_CODEC,
    )
    structure: JsonValue = field(init=False)
    schema_hash: str = field(init=False)
    _encode: _FactEncoder[ValueT] = field(init=False, repr=False, compare=False)
    _decode: _FactDecoder[ValueT] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("analysis fact schema id must not be empty")
        structure = _fact_type_structure(self.value_type)
        encoder, decoder = _fact_value_adapter(self.id, self.value_type)
        object.__setattr__(self, "structure", structure)
        object.__setattr__(self, "schema_hash", _schema_hash(structure))
        object.__setattr__(self, "_encode", encoder)
        object.__setattr__(self, "_decode", decoder)

    def encode(self, value: ValueT) -> JsonValue:
        """Validate and encode one typed value as canonical JSON content."""

        encoded = self._encode(value)
        _validate_fact_json(encoded, self.structure, path="$fact")
        return encoded

    def decode(self, value: JsonValue) -> ValueT:
        """Validate durable JSON and reconstruct the caller's local type."""

        _validate_fact_json(value, self.structure, path="$fact")
        return self._decode(value)


def _fact_value_adapter[ValueT](
    schema_id: str,
    value_type: type[ValueT],
) -> tuple[_FactEncoder[ValueT], _FactDecoder[ValueT]]:
    if issubclass(value_type, BaseModel):
        model_type = cast("type[BaseModel]", value_type)

        def encode_model(value: ValueT) -> JsonValue:
            if not isinstance(value, model_type):
                raise TypeError(
                    f"analysis fact schema {schema_id!r} requires "
                    f"{value_type.__qualname__}"
                )
            return cast(
                "JsonValue",
                model_type.model_validate(value).model_dump(mode="json"),
            )

        def decode_model(value: JsonValue) -> ValueT:
            return cast("ValueT", model_type.model_validate_json(canonical_json(value)))

        return encode_model, decode_model

    if not is_dataclass(value_type):
        raise TypeError(
            "structured analysis fact schemas require a dataclass or "
            "Pydantic model type"
        )
    members = fields(value_type)
    if any(not member.init for member in members):
        raise TypeError("analysis fact dataclass fields must participate in init")
    hints = _dataclass_type_hints(value_type)
    definitions: dict[str, tuple[object, object]] = {}
    for member in members:
        if member.default_factory is not MISSING:
            default: object = Field(
                default_factory=cast("Callable[[], object]", member.default_factory)
            )
        elif member.default is not MISSING:
            default = cast("object", member.default)
        else:
            default = ...
        definitions[member.name] = (hints[member.name], default)
    model_factory = cast("Callable[..., type[BaseModel]]", create_model)
    validation_model = model_factory(
        "AnalysisFactValue",
        __config__=ConfigDict(
            title=schema_id,
            extra="forbid",
            strict=True,
        ),
        **definitions,
    )

    def encode_dataclass(value: ValueT) -> JsonValue:
        if not isinstance(value, value_type):
            raise TypeError(
                f"analysis fact schema {schema_id!r} requires {value_type.__qualname__}"
            )
        validated = validation_model.model_validate(value, from_attributes=True)
        return cast("JsonValue", validated.model_dump(mode="json"))

    def decode_dataclass(value: JsonValue) -> ValueT:
        validated = validation_model.model_validate_json(canonical_json(value))
        constructor = cast("Callable[..., ValueT]", value_type)
        return constructor(
            **{member.name: getattr(validated, member.name) for member in members}
        )

    return encode_dataclass, decode_dataclass


def _fact_type_structure(
    annotation: object,
    *,
    ancestors: frozenset[type[object]] | None = None,
) -> JsonValue:
    selected_ancestors = _EMPTY_FACT_ANCESTORS if ancestors is None else ancestors
    origin = get_origin(annotation)
    if origin is Annotated:
        arguments = cast("tuple[object, ...]", get_args(annotation))
        return _fact_type_structure(arguments[0], ancestors=selected_ancestors)
    if annotation is None or annotation is type(None):
        return {"type": "null"}
    if annotation is bool:
        return {"type": "bool"}
    if annotation is int:
        return {"type": "int"}
    if annotation is float:
        return {"type": "float"}
    if annotation is str:
        return {"type": "string"}
    if annotation is Quantity:
        return _QUANTITY_FACT_STRUCTURE
    if origin is UnionType:
        arguments = cast("tuple[object, ...]", get_args(annotation))
        variants = [
            _fact_type_structure(item, ancestors=selected_ancestors)
            for item in arguments
        ]
        variants.sort(key=canonical_json)
        return {"type": "union", "variants": variants}
    if origin is Literal:
        arguments = cast("tuple[object, ...]", get_args(annotation))
        values = [_literal_json(value) for value in arguments]
        values.sort(key=canonical_json)
        return {"type": "literal", "values": values}
    if origin in {list, Sequence}:
        arguments = cast("tuple[object, ...]", get_args(annotation))
        if len(arguments) != 1:
            raise TypeError("analysis fact sequences require one item type")
        return {
            "type": "array",
            "items": _fact_type_structure(
                arguments[0],
                ancestors=selected_ancestors,
            ),
        }
    if origin is tuple:
        arguments = cast("tuple[object, ...]", get_args(annotation))
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return {
                "type": "array",
                "items": _fact_type_structure(
                    arguments[0],
                    ancestors=selected_ancestors,
                ),
            }
        return {
            "type": "tuple",
            "items": [
                _fact_type_structure(item, ancestors=selected_ancestors)
                for item in arguments
            ],
        }
    if origin in {dict, Mapping}:
        key_type, value_type = cast(
            "tuple[object, object]",
            get_args(annotation),
        )
        if key_type is not str:
            raise TypeError("analysis fact mappings require string keys")
        return {
            "type": "mapping",
            "values": _fact_type_structure(
                value_type,
                ancestors=selected_ancestors,
            ),
        }
    if not isinstance(annotation, type):
        raise TypeError(
            f"analysis fact field type {annotation!r} has no stable JSON contract"
        )
    value_type = cast("type[object]", annotation)
    if value_type in selected_ancestors:
        raise TypeError("recursive analysis fact types are not supported")
    nested_ancestors: frozenset[type[object]] = selected_ancestors | {value_type}
    if is_dataclass(value_type):
        hints = _dataclass_type_hints(value_type)
        return {
            "type": "object",
            "fields": {
                member.name: _fact_type_structure(
                    hints[member.name],
                    ancestors=nested_ancestors,
                )
                for member in fields(value_type)
                if member.init
            },
        }
    if issubclass(value_type, BaseModel):
        return {
            "type": "object",
            "fields": {
                name: _fact_type_structure(
                    field_info.annotation,
                    ancestors=nested_ancestors,
                )
                for name, field_info in value_type.model_fields.items()
            },
        }
    raise TypeError(
        "analysis fact field type "
        f"{value_type.__qualname__} has no stable JSON contract"
    )


def _literal_json(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError("analysis fact Literal values must be scalar JSON values")


def _validate_fact_json(  # noqa: C901 - compact interpreter for the closed schema IR
    value: JsonValue,
    schema: JsonValue,
    *,
    path: str,
) -> None:
    if not isinstance(schema, dict) or not isinstance(
        schema_type := schema.get("type"),
        str,
    ):
        raise TypeError("analysis fact structural schema is invalid")
    if schema_type == "null":
        valid = value is None
    elif schema_type == "bool":
        valid = isinstance(value, bool)
    elif schema_type == "int":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif schema_type == "float":
        valid = isinstance(value, int | float) and not isinstance(value, bool)
    elif schema_type == "string":
        valid = isinstance(value, str)
    elif schema_type == "quantity":
        if not isinstance(value, dict) or set(value) != {"value", "unit"}:
            return _require_fact_json(False, path, "quantity")
        quantity = cast("dict[str, JsonValue]", value)
        _validate_fact_json(
            quantity["value"],
            {"type": "float"},
            path=f"{path}.value",
        )
        _validate_fact_json(
            quantity["unit"],
            {"type": "string"},
            path=f"{path}.unit",
        )
        return
    elif schema_type == "union":
        variants = cast("list[JsonValue]", schema["variants"])
        if any(_matches_fact_json(value, variant, path=path) for variant in variants):
            return
        valid = False
    elif schema_type == "literal":
        values = cast("list[JsonValue]", schema["values"])
        valid = any(type(value) is type(item) and value == item for item in values)
    elif schema_type in {"array", "tuple"}:
        if not isinstance(value, list):
            valid = False
        elif schema_type == "array":
            item_schema = cast("JsonValue", schema["items"])
            for index, item in enumerate(value):
                _validate_fact_json(item, item_schema, path=f"{path}[{index}]")
            return
        else:
            item_schemas = cast("list[JsonValue]", schema["items"])
            valid = len(value) == len(item_schemas)
            if valid:
                for index, (item, item_schema) in enumerate(
                    zip(value, item_schemas, strict=True)
                ):
                    _validate_fact_json(item, item_schema, path=f"{path}[{index}]")
                return
    elif schema_type == "mapping":
        if not isinstance(value, dict):
            valid = False
        else:
            item_schema = cast("JsonValue", schema["values"])
            for key, item in value.items():
                _validate_fact_json(item, item_schema, path=f"{path}.{key}")
            return
    elif schema_type == "object":
        field_schemas = cast("dict[str, JsonValue]", schema["fields"])
        if not isinstance(value, dict) or set(value) != set(field_schemas):
            valid = False
        else:
            for name, field_schema in field_schemas.items():
                _validate_fact_json(
                    value[name],
                    field_schema,
                    path=f"{path}.{name}",
                )
            return
    else:
        raise TypeError(f"unknown analysis fact structural type: {schema_type}")
    _require_fact_json(valid, path, schema_type)


def validate_analysis_fact_json(value: JsonValue, structure: JsonValue) -> None:
    """Validate durable JSON against one first-party fact structure.

    Analysis publication normally validates through a local
    :class:`AnalysisFactSchema`. Control-plane consumers such as interactive
    procedure inputs retain only the durable structure, so they use this
    function without importing the user's Python response type.
    """

    _validate_fact_json(value, structure, path="$fact")


def _matches_fact_json(value: JsonValue, schema: JsonValue, *, path: str) -> bool:
    try:
        _validate_fact_json(value, schema, path=path)
    except TypeError:
        return False
    return True


def _require_fact_json(valid: bool, path: str, expected: str) -> None:
    if not valid:
        raise TypeError(f"analysis fact {path} must match structural type {expected}")


def _dataclass_type_hints(value_type: type[object]) -> Mapping[str, object]:
    initializer = getattr(value_type, "__init__", None)
    retained_globals = getattr(initializer, "__globals__", None)
    globalns = cast(
        "dict[str, object] | None",
        retained_globals if isinstance(retained_globals, dict) else None,
    )
    return get_type_hints(
        value_type,
        globalns=globalns,
        include_extras=False,
    )


__all__ = [
    "ANALYSIS_FACT_SCHEMA_CODEC",
    "QUANTITY_FACT_SCHEMA_HASH",
    "QUANTITY_FACT_SCHEMA_ID",
    "SCALAR_FACT_SCHEMA_HASH",
    "SCALAR_FACT_SCHEMA_ID",
    "AnalysisFactSchema",
    "ScalarFactValue",
    "analysis_fact_structure_hash",
    "validate_analysis_fact_json",
]
