"""One parameter declaration for mutable values, schemas and symbolic inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import copy, deepcopy
from typing import (
    ClassVar,
    Protocol,
    Self,
    cast,
    dataclass_transform,
    get_args,
    get_origin,
    get_type_hints,
    overload,
    override,
)

from scopecat.authoring.parameter_dataclasses import dataclass_parameter_fields
from scopecat.authoring.parameter_fields import (
    ParameterSpec,
    ResolvedParameterField,
    resolve_parameter_field,
    stored_parameter_value,
    table_from_parameter_fields,
)
from scopecat.config.parameter_updates import UpdateParameterRows, update_parameter_rows
from scopecat.config.validation import coerce_stored_parameter_value
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Table
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.value_refs import ValueRef
from scopecat.program.values import ParameterKeyInput, parameter, parameter_lookup
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)


class _Missing:
    pass


_MISSING = _Missing()


class ParameterFieldIdentity(Protocol):
    """Read-only identity, shared by input references and candidate targets."""

    @property
    def name(self) -> str: ...
    @property
    def owner(self) -> type[ParameterModel]: ...


class _Field[T]:
    """A class attribute identifies a field; an instance reads/writes its value."""

    def __init__(
        self,
        *,
        default: T | _Missing = _MISSING,
        key: bool = False,
        spec: ParameterSpec | None = None,
    ) -> None:
        self.default = default
        self.key = key
        self.spec = spec or ParameterSpec()
        self.name: str = ""
        self.owner: type[ParameterModel]

    def __set_name__(self, owner: type[ParameterModel], name: str) -> None:
        self.owner, self.name = owner, name

    @overload
    def __get__(self, instance: None, owner: type[ParameterModel]) -> Self: ...
    @overload
    def __get__(
        self, instance: ParameterModel, owner: type[ParameterModel] | None = None
    ) -> T: ...
    def __get__(
        self, instance: ParameterModel | None, owner: type[ParameterModel] | None = None
    ) -> Self | T:
        if instance is None:
            if owner is not None and owner is not self.owner:
                inherited = copy(self)
                inherited.owner = owner
                return inherited
            return self
        values = cast(
            "dict[str, object]", object.__getattribute__(instance, "_parameter_values")
        )
        if self.name not in values:
            raise ValueError(
                f"{type(instance).__name__}.{self.name}: parameter is unknown"
            )
        return cast("T", values[self.name])

    def __set__(self, instance: ParameterModel, value: T) -> None:
        values = cast(
            "dict[str, object]", object.__getattribute__(instance, "_parameter_values")
        )
        values[self.name] = value


class Param[T](_Field[T]):
    """A plain value descriptor; references keep its scalar Python type."""


def param[T](
    *,
    default: T | _Missing = _MISSING,
    key: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
    entity_kind: str | None = None,
) -> Param[T]:
    """Declare a plain scalar or entity value; defaults apply to new rows only."""
    return Param(
        default=default,
        key=key,
        spec=ParameterSpec(minimum=minimum, maximum=maximum, entity_kind=entity_kind),
    )


class Magnitude[T: float | None](_Field[T]):
    """Edit numbers in the declared unit; references carry physical Quantity."""


def quantity[T: float | None](
    *,
    unit: str,
    default: T | _Missing = _MISSING,
    key: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> Magnitude[T]:
    return Magnitude(
        default=default,
        key=key,
        spec=ParameterSpec(unit=unit, minimum=minimum, maximum=maximum),
    )


@dataclass_transform(kw_only_default=True, field_specifiers=(param, quantity))
class ParameterModel:
    """Data-only, mutable parameter row declaration with typed field identities.

    New rows are detached values. Workspace selection binds the same declaration
    to existing data without invoking this constructor or applying defaults.
    """

    _parameter_table: ClassVar[str] = ""
    _parameter_values: dict[str, object]

    def __init_subclass__(cls, *, table: str | None = None) -> None:
        super().__init_subclass__()
        if table is not None:
            cls._parameter_table = table

    def __init__(self, **values: object) -> None:
        self._parameter_values = {}
        for field in parameter_fields(type(self)):
            descriptor = cast("_Field[object]", getattr(type(self), field.name))
            if field.name in values:
                self._parameter_values[field.name] = values.pop(field.name)
            elif not isinstance(descriptor.default, _Missing):
                self._parameter_values[field.name] = descriptor.default
            else:
                raise TypeError(f"{type(self).__name__}: missing {field.name!r}")
        if values:
            raise TypeError(
                f"{type(self).__name__}: unexpected fields {sorted(values)}"
            )

    def _editing_values(self) -> dict[str, object]:
        """Read detached values without consuming unknown required fields."""
        return self._parameter_values.copy()

    def copy(self) -> Self:
        """Return an independent editable row, preserving unknown cells."""
        row = object.__new__(type(self))
        row._parameter_values = self._editing_values()
        return row

    def __copy__(self) -> Self:
        return self.copy()

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        row = self.copy()
        row._parameter_values = deepcopy(row._parameter_values, memo)
        return row

    @override
    def __repr__(self) -> str:
        values = self._editing_values()
        display = ", ".join(
            f"{field.name}={values[field.name]!r}"
            if field.name in values
            else f"{field.name}=<unknown>"
            for field in parameter_fields(type(self))
        )
        return f"{type(self).__name__}({display})"


def parameter_table_name(model: type[ParameterModel]) -> str:
    name = cast("str", getattr(model, "_parameter_table"))  # noqa: B009 - private metadata
    if not name:
        raise TypeError(f"{model.__name__}: declare table='...' explicitly")
    return name


def parameter_fields(row_type: type[object]) -> tuple[ResolvedParameterField, ...]:
    if not issubclass(row_type, ParameterModel):
        return dataclass_parameter_fields(row_type)
    hints = cast("dict[str, object]", get_type_hints(row_type, include_extras=True))
    result: list[ResolvedParameterField] = []
    for name, annotation in hints.items():
        origin = get_origin(annotation)
        if origin not in (Param, Magnitude):
            if name.startswith("_") or origin is ClassVar:
                continue
            raise TypeError(
                f"{row_type.__name__}.{name}: annotate parameter fields as Param[T] "
                "or Magnitude[T]; use ClassVar for class metadata"
            )
        if name.startswith("_"):
            raise TypeError(
                f"{row_type.__name__}.{name}: parameter names cannot start with '_' "
            )
        descriptor = cast("object", getattr(row_type, name))
        # Bound workspace subclasses replace descriptors with live properties.
        if isinstance(descriptor, property):
            descriptor = cast(
                "object",
                next(
                    vars(base)[name]
                    for base in row_type.__mro__[1:]
                    if name in vars(base)
                ),
            )
        if not isinstance(descriptor, _Field):
            raise TypeError(f"{row_type.__name__}.{name}: use param() or quantity()")
        selected = cast("_Field[object]", descriptor)
        argument = cast("tuple[object, ...]", get_args(annotation))[0]
        field = resolve_parameter_field(
            name, argument, selected.spec, label=f"{row_type.__name__}.{name}"
        )
        if origin is Magnitude and field.python_type is not float:
            raise TypeError(f"{row_type.__name__}.{name}: Magnitude requires float")
        result.append(field)
    if not result:
        raise TypeError(f"{row_type.__name__}: declare at least one parameter field")
    return tuple(result)


def parameter_key(model: type[ParameterModel]) -> tuple[str, ...]:
    return tuple(
        f.name
        for f in parameter_fields(model)
        if cast("_Field[object]", getattr(model, f.name)).key
    )


def parameter_table_schema(
    row_type: type[object], *, primary_key: tuple[str, ...]
) -> Table:
    fields = parameter_fields(row_type)
    if not primary_key and issubclass(row_type, ParameterModel):
        raise TypeError(f"{row_type.__name__}: declare a primary key")
    return table_from_parameter_fields(
        fields, primary_key=primary_key, label=row_type.__name__
    )


@overload
def parameter_ref[T: float | None](
    field: Magnitude[T], key: ParameterKeyInput | tuple[ParameterKeyInput, ...]
) -> ValueRef[Quantity]: ...
@overload
def parameter_ref[T: bool | int | float | str | EntityRef](
    field: Param[T | None], key: ParameterKeyInput | tuple[ParameterKeyInput, ...]
) -> ValueRef[T]: ...
@overload
def parameter_ref[T: bool | int | float | str | EntityRef](
    field: Param[T], key: ParameterKeyInput | tuple[ParameterKeyInput, ...]
) -> ValueRef[T]: ...


def parameter_ref(
    field: ParameterFieldIdentity,
    key: ParameterKeyInput | tuple[ParameterKeyInput, ...],
) -> ValueRef[object]:
    """Require a field from the run's frozen parameters when actually consumed.

    Optional editing values are not nullable symbolic inputs. Creating an unused
    reference does not consume a missing cell or fill it with a default.
    """
    model = field.owner
    table = parameter_table_name(model)
    schema = parameter_table_schema(model, primary_key=parameter_key(model))
    keys = key if isinstance(key, tuple) else (key,)
    if len(keys) != len(schema.primary_key):
        raise ValueError(f"{table}.{field.name}: expected keys {schema.primary_key}")
    column = next(column for column in schema.columns if column.id == field.name)
    return parameter_lookup(
        table,
        key=dict(zip(schema.primary_key, keys, strict=True)),
        column=field.name,
        value_type=column.value_type,
    )


def parameter_definition(
    model: type[ParameterModel] | ParameterFieldIdentity,
) -> ParameterDefinition:
    """Derive a durable table or column definition from its declaration."""
    if not isinstance(model, type):
        field = next(
            field for field in parameter_fields(model.owner) if field.name == model.name
        )
        return ParameterDefinition(id=field.name, value_type=field.value_type)
    return ParameterDefinition(
        id=parameter_table_name(model),
        value_type=parameter_table_schema(model, primary_key=parameter_key(model)),
        description=" ".join(model.__doc__.split()) if model.__doc__ else None,
    )


def parameter_snapshot(
    id: str,
    *,
    tables: Mapping[type[ParameterModel], Sequence[ParameterModel]],
    scalars: Mapping[str, ParameterAtomValue] | None = None,
) -> ParameterSnapshot:
    """Build initial stored values; defaults belong to constructed rows only.

    Empty sequences declare empty tables. Unknown optional cells stay absent.
    Project admission uses the same catalog validation as edited workspaces.
    """
    return ParameterSnapshot(
        id=id,
        values=(
            *(
                ScalarParameterValue(id=name, value=value)
                for name, value in (scalars or {}).items()
            ),
            *(_initial_table(model, rows) for model, rows in tables.items()),
        ),
    )


def parameter_row_values(row: ParameterModel) -> dict[str, ParameterAtomValue]:
    """Detach stored values from a model, preserving declared physical units."""
    values: dict[str, ParameterAtomValue] = {}
    for field in parameter_fields(type(row)):
        value = cast("object", getattr(row, field.name))
        if value is None and field.optional:
            continue
        values[field.name] = stored_parameter_value(value, field, label=field.name)
    return values


def parameter_table_ref(
    model: type[ParameterModel],
) -> ValueRef[list[dict[str, object]]]:
    """Reference a complete frozen table for compiler and policy inputs."""
    return parameter(
        parameter_table_name(model),
        parameter_table_schema(model, primary_key=parameter_key(model)),
    )


def _initial_table(
    model: type[ParameterModel], rows: Sequence[ParameterModel]
) -> TableParameterValue:
    name = parameter_table_name(model)
    if any(not isinstance(row, model) for row in rows):
        raise TypeError(f"{name}: initial rows must be {model.__name__} instances")
    stored = TableParameterValue(
        id=name, rows=tuple(parameter_row_values(row) for row in rows)
    )
    normalized = coerce_stored_parameter_value(
        parameter_definition(model), stored, path=(name,), allow_missing=True
    )
    assert isinstance(normalized, TableParameterValue)
    return normalized


@overload
def parameter_update[T: float | None](
    field: Magnitude[T],
    key: ParameterAtomValue | tuple[ParameterAtomValue, ...],
    value: float | Quantity,
) -> UpdateParameterRows: ...
@overload
def parameter_update[T](
    field: Param[T], key: ParameterAtomValue | tuple[ParameterAtomValue, ...], value: T
) -> UpdateParameterRows: ...


def parameter_update(
    field: ParameterFieldIdentity,
    key: ParameterAtomValue | tuple[ParameterAtomValue, ...],
    value: object,
) -> UpdateParameterRows:
    """Build an explicit proposed edit using the same field as experiment inputs."""
    model = field.owner
    name = parameter_table_name(model)
    schema = parameter_table_schema(model, primary_key=parameter_key(model))
    keys = key if isinstance(key, tuple) else (key,)
    if len(keys) != len(schema.primary_key):
        raise ValueError(f"{name}.{field.name}: expected keys {schema.primary_key}")
    columns = {column.id: column.value_type for column in schema.columns}
    selected = next(f for f in parameter_fields(model) if f.name == field.name)
    return update_parameter_rows(
        name,
        key={
            column: cast(
                "ParameterAtomValue",
                coerce_literal(columns[column], item, path=(name, column)),
            )
            for column, item in zip(schema.primary_key, keys, strict=True)
        },
        values={
            field.name: stored_parameter_value(
                value, selected, label=f"{name}.{field.name}"
            )
        },
    )


def parameter_catalog(
    id: str, *declarations: type[ParameterModel] | ParameterDefinition
) -> ParameterCatalog:
    """Build the existing durable catalog from model and scalar declarations."""
    return ParameterCatalog(
        id=id,
        definitions=tuple(
            parameter_definition(item) if isinstance(item, type) else item
            for item in declarations
        ),
    )
