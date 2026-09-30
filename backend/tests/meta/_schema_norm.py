"""The JSON Schema normalizer the MCP parity meta-tests share (P2-01).

Two schemas that say the same thing in different shapes normalize to the same value:
`$ref`s are inlined (`#/$defs/...` from the MCP tool, `#/components/schemas/...` from the
OpenAPI document), `title`, `description` and `examples` are dropped, `required` and
`enum` are sorted, and a nullable type has one form: `anyOf [X, {"type": "null"}]` and
`type: [X, "null"]` both become X plus `"nullable": true`; a `null` default (what an
optional nullable field means anyway) is dropped. A semantic difference (another
type, bound or required field) survives normalization.
"""

from __future__ import annotations

from typing import Any, Final

DROPPED: Final = frozenset({"title", "description", "examples", "$defs", "definitions"})


def _resolve(ref: str, defs: dict[str, Any], components: dict[str, Any]) -> Any:
    name = ref.rsplit("/", 1)[-1]
    if ref.startswith("#/components/schemas/"):
        return components[name]
    return defs[name]


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    options = schema.get("anyOf")
    if isinstance(options, list):
        rest = [o for o in options if o != {"type": "null"}]
        if len(rest) < len(options):
            merged = dict(schema)
            del merged["anyOf"]
            if len(rest) == 1:
                merged.update(rest[0])
            else:
                merged["anyOf"] = rest
            merged["nullable"] = True
            return merged
    kind = schema.get("type")
    if isinstance(kind, list) and "null" in kind:
        merged = dict(schema)
        others = [k for k in kind if k != "null"]
        merged["type"] = others[0] if len(others) == 1 else sorted(others)
        merged["nullable"] = True
        return merged
    return schema


def norm(
    schema: Any,
    defs: dict[str, Any] | None = None,
    components: dict[str, Any] | None = None,
    _depth: int = 0,
) -> Any:
    """The normalized schema. `defs` defaults to the schema's own `$defs`; `components` is
    the OpenAPI document's `components.schemas`."""
    if _depth > 50:  # a recursive schema; deep enough to compare
        return {"recursive": True}
    if isinstance(schema, list):
        return [norm(item, defs, components, _depth + 1) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if defs is None:
        defs = dict(schema.get("$defs", {}))
    components = components or {}
    schema = _nullable(schema)  # first: a nullable reference is `anyOf [{"$ref"}, null]`
    if "$ref" in schema:
        target = _resolve(schema["$ref"], defs, components)
        extra = {k: v for k, v in schema.items() if k != "$ref"}
        return norm({**target, **extra}, defs, components, _depth + 1)
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in DROPPED or (key == "default" and value is None):
            continue
        if key in {"required", "enum"} and isinstance(value, list):
            out[key] = sorted(value, key=str)
        elif key == "properties" and isinstance(value, dict):
            out[key] = {
                name: norm(prop, defs, components, _depth + 1) for name, prop in value.items()
            }
        elif key == "allOf" and isinstance(value, list) and len(value) == 1:
            out.update(norm(value[0], defs, components, _depth + 1))
        else:
            out[key] = norm(value, defs, components, _depth + 1)
    if out.get("required") == []:
        del out["required"]
    return out
