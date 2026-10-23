"""to_anthropic_tool over every registered tool, plus synthetic schemas for the recursive rules."""

import fastmcp
import pytest

from app.mcp.strict_schema import assert_strict, strictify, to_anthropic_tool


async def _tools(server):
    async with fastmcp.Client(server) as c:
        return await c.list_tools()


@pytest.mark.parametrize(
    "server_path", ["app.mcp.internal:paypal_ops_internal", "app.mcp.public:paypal_ops_public"]
)
async def test_every_registered_tool_converts_to_a_strict_tool(server_path):
    import importlib

    module_name, attr = server_path.split(":")
    server = getattr(importlib.import_module(module_name), attr)
    tools = await _tools(server)
    assert tools, "server registers no tools"
    for tool in tools:
        converted = to_anthropic_tool(tool)
        assert set(converted) == {"name", "description", "input_schema", "strict"}
        assert converted["name"] == tool.name
        assert converted["strict"] is True
        assert converted["description"].strip(), f"{tool.name} has no description"
        schema = converted["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"]), tool.name
        assert_strict(schema)
        # Inputs are logical handles only: circle ids, round numbers, member handles, note text, plus the
        # recovery path's commitment (amount string, ISO date), remedy set id and remedy list (B6).
        for prop, spec in schema["properties"].items():
            assert prop in {
                "circle_id",
                "round_n",
                "member",
                "text",
                "amount",
                "date",
                "remedy_set_id",
                "remedies",
            }, f"{tool.name}.{prop}"
            if prop == "remedies":
                assert spec["type"] == "array", f"{tool.name}.{prop}"
            else:
                assert spec["type"] in ("string", "integer"), f"{tool.name}.{prop}"


async def test_internal_tool_set_matches_contract():
    from app.mcp.internal import TOOL_NAMES, paypal_ops_internal

    names = [t.name for t in await _tools(paypal_ops_internal)]
    assert names == list(TOOL_NAMES)
    assert names == [
        "get_circle",
        "get_round_matrix",
        "get_member_history",
        "collect_round",
        "charge_vaulted_member",
        "get_order_details",
        "get_available_balance",
        "create_pot_payout",
        "get_payout_status",
        "record_note",
        # B6 recovery path
        "list_inbox_replies",
        "record_commitment",
        "propose_remedies",
        "open_vote",
        "get_vote_status",
        "request_organizer_approval",
        "execute_remedies",
        "reschedule_turns",
    ]


async def test_public_tool_set_is_read_only_subset():
    from app.mcp.public import PUBLIC_TOOL_NAMES, paypal_ops_public

    names = {t.name for t in await _tools(paypal_ops_public)}
    assert (
        names
        == set(PUBLIC_TOOL_NAMES)
        == {"get_circle", "get_round_matrix", "get_charter", "get_payout_status"}
    )
    assert not names & {"collect_round", "charge_vaulted_member", "create_pot_payout", "record_note"}


def test_optional_with_default_becomes_required_nullable_without_default():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer", "default": 2},
            "label": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["a"],
    }
    out = strictify(schema)
    assert out["required"] == ["a", "b", "label"]
    assert out["additionalProperties"] is False
    assert out["properties"]["a"] == {"type": "integer"}
    assert out["properties"]["b"] == {"type": ["integer", "null"]}
    assert out["properties"]["label"] == {"type": ["string", "null"]}
    assert "default" not in str(out)
    assert_strict(out)
    # input untouched
    assert schema["properties"]["b"]["default"] == 2


def test_nested_objects_and_arrays_are_closed_recursively():
    schema = {
        "type": "object",
        "properties": {
            "circle_id": {"type": "string"},
            "opts": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "x": {"type": "number", "default": 1.0},
                    "inner": {"type": "object", "properties": {"y": {"type": "boolean"}}},
                },
            },
            "items": {"type": "array", "items": {"type": "object", "properties": {"k": {"type": "string"}}}},
        },
        "required": ["circle_id", "opts"],
    }
    out = strictify(schema)
    opts = out["properties"]["opts"]
    assert opts["additionalProperties"] is False
    assert set(opts["required"]) == {"x", "inner"}
    assert opts["properties"]["x"] == {"type": ["number", "null"]}
    inner = opts["properties"]["inner"]
    assert inner["additionalProperties"] is False and inner["required"] == ["y"]
    assert inner["type"] == ["object", "null"]  # optional nested object stays an object but allows null
    arr = out["properties"]["items"]
    assert arr["type"] == ["array", "null"]
    assert arr["items"]["additionalProperties"] is False and arr["items"]["required"] == ["k"]
    assert_strict(out)


def test_enum_and_ref_optionals_get_null_added():
    schema = {
        "type": "object",
        "properties": {
            "wallet": {"type": "string", "enum": ["PAYPAL", "VENMO"], "default": "PAYPAL"},
            "member": {"$ref": "#/$defs/Member"},
        },
        "$defs": {
            "Member": {"type": "object", "properties": {"handle": {"type": "string"}}, "required": ["handle"]}
        },
    }
    out = strictify(schema)
    wallet = out["properties"]["wallet"]
    assert wallet["type"] == ["string", "null"] and wallet["enum"] == ["PAYPAL", "VENMO", None]
    member = out["properties"]["member"]
    assert member == {"anyOf": [{"$ref": "#/$defs/Member"}, {"type": "null"}]}
    assert out["$defs"]["Member"]["additionalProperties"] is False
    assert_strict(out)


def test_rich_anyof_is_kept_but_each_branch_is_strict():
    schema = {
        "type": "object",
        "properties": {
            "target": {
                "anyOf": [{"type": "object", "properties": {"id": {"type": "string"}}}, {"type": "null"}]
            }
        },
        "required": ["target"],
    }
    out = strictify(schema)
    branches = out["properties"]["target"]["anyOf"]
    assert branches[0]["additionalProperties"] is False and branches[0]["required"] == ["id"]
    assert branches[1] == {"type": "null"}


def test_dict_input_with_legacy_inputschema_key_and_empty_schema():
    out = to_anthropic_tool({"name": "noop", "inputSchema": {}})
    assert out["input_schema"] == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert out["description"] == ""
    with pytest.raises(ValueError):
        to_anthropic_tool({"description": "nameless"})


def test_assert_strict_catches_violations():
    with pytest.raises(AssertionError):
        assert_strict(
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            }
        )
    with pytest.raises(AssertionError):
        assert_strict({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    with pytest.raises(AssertionError):
        assert_strict(
            {
                "type": "object",
                "properties": {"a": {"type": "string", "default": "x"}},
                "required": ["a"],
                "additionalProperties": False,
            }
        )
