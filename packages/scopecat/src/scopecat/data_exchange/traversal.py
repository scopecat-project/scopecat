"""Walk typed evidence without interpreting arbitrary metadata strings."""

from collections.abc import Iterable, Iterator, Mapping
from typing import cast

from pydantic import BaseModel


def evidence_models(value: object) -> Iterator[BaseModel]:
    if isinstance(value, BaseModel):
        yield value
        for name in type(value).model_fields:
            yield from evidence_models(cast("object", getattr(value, name)))
    elif isinstance(value, Mapping):
        for item in cast("Mapping[object, object]", value).values():
            yield from evidence_models(item)
    elif isinstance(value, tuple | list):
        for item in cast("Iterable[object]", value):
            yield from evidence_models(item)
