"""Verify that each tool's to_api_schema() stays consistent with its input_model.

These tests catch drift between the hand-crafted LLM-facing schema and the
Pydantic model used for runtime validation.  They run over every tool in the
default registry automatically, so new tools are covered without extra work.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from openharness.tools import create_default_tool_registry
from openharness.tools.base import BaseTool

_ALL_TOOLS: list[BaseTool] = create_default_tool_registry().list_tools()


def _required_pydantic_fields(model: type[BaseModel]) -> set[str]:
    return {name for name, f in model.model_fields.items() if f.is_required()}


def _all_pydantic_fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields.keys())


@pytest.mark.parametrize("tool", _ALL_TOOLS, ids=lambda t: t.name)
def test_api_schema_structure(tool: BaseTool) -> None:
    """Schema must have the mandatory top-level keys and a well-formed parameters block."""
    schema = tool.to_api_schema()

    assert schema.get("name") == tool.name, "schema['name'] must equal tool.name"
    assert isinstance(schema.get("description"), str), "schema['description'] must be a string"

    params = schema.get("parameters", {})
    assert params.get("type") == "object", "parameters.type must be 'object'"
    assert isinstance(params.get("properties"), dict), "parameters.properties must be a dict"


@pytest.mark.parametrize("tool", _ALL_TOOLS, ids=lambda t: t.name)
def test_api_schema_required_matches_pydantic(tool: BaseTool) -> None:
    """Every Pydantic required field (no default) must appear in schema's 'required' list."""
    params = tool.to_api_schema().get("parameters", {})
    schema_required = set(params.get("required", []))
    pydantic_required = _required_pydantic_fields(tool.input_model)

    missing = pydantic_required - schema_required
    assert not missing, (
        f"Pydantic required fields not listed in schema 'required': {missing}"
    )


@pytest.mark.parametrize("tool", _ALL_TOOLS, ids=lambda t: t.name)
def test_api_schema_no_ghost_properties(tool: BaseTool) -> None:
    """Every property name in the API schema must match an actual Pydantic field."""
    params = tool.to_api_schema().get("parameters", {})
    schema_props = set(params.get("properties", {}).keys())
    pydantic_fields = _all_pydantic_fields(tool.input_model)

    ghost = schema_props - pydantic_fields
    assert not ghost, (
        f"API schema properties with no matching Pydantic field: {ghost}"
    )


@pytest.mark.parametrize("tool", _ALL_TOOLS, ids=lambda t: t.name)
def test_api_schema_covers_all_pydantic_fields(tool: BaseTool) -> None:
    """Every Pydantic field must appear in the API schema properties."""
    params = tool.to_api_schema().get("parameters", {})
    schema_props = set(params.get("properties", {}).keys())
    pydantic_fields = _all_pydantic_fields(tool.input_model)

    missing = pydantic_fields - schema_props
    assert not missing, (
        f"Pydantic fields missing from API schema properties: {missing}"
    )
