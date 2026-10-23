"""Convert fastmcp tool definitions into Anthropic ``strict: true`` tool definitions.

Strict tool use needs a closed schema: every property required, ``additionalProperties: false`` on every
object (recursively), no ``default`` keywords. Optional properties become nullable (``{"type": [T, "null"]}``)
so the model can still express "not given".
"""

from __future__ import annotations

import copy
from typing import Any

_JSON_TYPES = ("string", "number", "integer", "boolean", "object", "array", "null")


def to_anthropic_tool(mcp_tool: Any) -> dict[str, Any]:
    """Build ``{"name", "description", "input_schema", "strict": True}`` from a fastmcp/MCP tool.

    Accepts an ``mcp.types.Tool`` (``.name``, ``.description``, ``.input_schema`` or legacy ``.inputSchema``)
    or a plain dict with the same keys.
    """
    if isinstance(mcp_tool, dict):
        name = mcp_tool.get("name")
        description = mcp_tool.get("description") or ""
        schema = mcp_tool.get("input_schema") or mcp_tool.get("inputSchema") or {}
    else:
        name = mcp_tool.name
        description = getattr(mcp_tool, "description", None) or ""
        # mcp-types 2.x renamed inputSchema -> input_schema (the old name still works but warns).
        schema = getattr(mcp_tool, "input_schema", None)
        if schema is None:
            schema = getattr(mcp_tool, "inputSchema", None) or {}
    if not name:
        raise ValueError("tool has no name")
    return {
        "name": str(name),
        "description": str(description),
        "input_schema": strictify(schema),
        "strict": True,
    }


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``schema`` that satisfies the strict-mode rules (see module docstring)."""
    node = copy.deepcopy(schema) if schema else {}
    if "type" not in node and "properties" not in node and "anyOf" not in node and "oneOf" not in node:
        node["type"] = "object"
    return _strict_node(node)


def _strict_node(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    node.pop("default", None)

    for defs_key in ("$defs", "definitions"):
        if isinstance(node.get(defs_key), dict):
            node[defs_key] = {k: _strict_node(v) for k, v in node[defs_key].items()}

    for combo in ("anyOf", "oneOf", "allOf"):
        if isinstance(node.get(combo), list):
            node[combo] = [_strict_node(b) for b in node[combo]]
    if "anyOf" in node:
        node = _collapse_nullable_union(node)

    is_object = (
        node.get("type") == "object"
        or "properties" in node
        or (isinstance(node.get("type"), list) and "object" in node["type"])
    )
    if is_object:
        props = node.get("properties")
        if not isinstance(props, dict):
            props = {}
        required = set(node.get("required") or [])
        new_props: dict[str, Any] = {}
        for prop_name, prop_schema in props.items():
            cleaned = _strict_node(prop_schema if isinstance(prop_schema, dict) else {})
            if prop_name not in required:
                cleaned = _make_nullable(cleaned)
            new_props[prop_name] = cleaned
        node["properties"] = new_props
        node["required"] = list(new_props.keys())
        node["additionalProperties"] = False
        node.pop("patternProperties", None)

    if "items" in node:
        if isinstance(node["items"], list):
            node["items"] = [_strict_node(i) for i in node["items"]]
        else:
            node["items"] = _strict_node(node["items"])
    if isinstance(node.get("prefixItems"), list):
        node["prefixItems"] = [_strict_node(i) for i in node["prefixItems"]]
    return node


def _collapse_nullable_union(node: dict[str, Any]) -> dict[str, Any]:
    """``{"anyOf": [{"type": "string"}, {"type": "null"}]}`` -> ``{"type": ["string", "null"]}``.

    Only collapses when every non-null branch is a bare ``{"type": T}`` (plus harmless keywords). Anything
    richer keeps the ``anyOf`` with each branch made strict.
    """
    branches = node.get("anyOf") or []
    null_present = any(isinstance(b, dict) and b.get("type") == "null" for b in branches)
    others = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
    simple = all(
        isinstance(b, dict)
        and isinstance(b.get("type"), str)
        and b["type"] in _JSON_TYPES
        and set(b.keys()) <= {"type", "title", "description", "format", "enum"}
        and b["type"] not in ("object", "array")
        for b in others
    )
    if not others or not simple:
        return node
    types: list[str] = []
    enum_values: list[Any] | None = None
    for b in others:
        if b["type"] not in types:
            types.append(b["type"])
        if "enum" in b:
            enum_values = (enum_values or []) + list(b["enum"])
    if null_present and "null" not in types:
        types.append("null")
    rest = {k: v for k, v in node.items() if k != "anyOf"}
    out: dict[str, Any] = {**rest, "type": types if len(types) > 1 else types[0]}
    if enum_values is not None:
        if null_present and None not in enum_values:
            enum_values.append(None)
        out["enum"] = enum_values
    return out


def _make_nullable(node: dict[str, Any]) -> dict[str, Any]:
    """Allow ``null`` for an optional property, whatever shape its schema has."""
    t = node.get("type")
    if isinstance(t, str):
        if t != "null":
            node["type"] = [t, "null"]
    elif isinstance(t, list):
        if "null" not in t:
            node["type"] = [*t, "null"]
    elif "anyOf" in node:
        if not any(isinstance(b, dict) and b.get("type") == "null" for b in node["anyOf"]):
            node["anyOf"] = [*node["anyOf"], {"type": "null"}]
    elif "oneOf" in node:
        if not any(isinstance(b, dict) and b.get("type") == "null" for b in node["oneOf"]):
            node["oneOf"] = [*node["oneOf"], {"type": "null"}]
    elif "$ref" in node or "allOf" in node:
        inner = {k: v for k, v in node.items() if k not in ("title", "description")}
        meta = {k: v for k, v in node.items() if k in ("title", "description")}
        node = {**meta, "anyOf": [inner, {"type": "null"}]}
    else:
        node["type"] = ["string", "null"]
    if isinstance(node.get("enum"), list) and None not in node["enum"]:
        node["enum"] = [*node["enum"], None]
    return node


def assert_strict(schema: dict[str, Any]) -> None:
    """Raise AssertionError when ``schema`` violates a strict-mode rule. Used by tests."""

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")
            return
        if not isinstance(node, dict):
            return
        assert "default" not in node, f"{path}: default present"
        is_object = node.get("type") == "object" or "properties" in node
        if is_object:
            props = node.get("properties", {})
            assert node.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
            assert set(node.get("required", [])) == set(props), f"{path}: every property must be required"
        for key, value in node.items():
            if key in ("enum", "required", "type", "title", "description", "format", "const"):
                continue
            _walk(value, f"{path}.{key}")

    _walk(schema, "$")
