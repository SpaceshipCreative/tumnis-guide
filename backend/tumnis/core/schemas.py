"""Versioned payload models (R-02). P0-07 needs only the base class; P0-11 adds the schema
registry, `versioned(family, name, version)` and the N-1 upgraders to this module."""

from pydantic import BaseModel, ConfigDict


class VersionedPayload(BaseModel):
    """Base for every payload that crosses a process or a release: frozen, no unknown
    fields, and an integer `schema_version` that each subclass pins with
    `schema_version: Literal[<n>] = <n>`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
