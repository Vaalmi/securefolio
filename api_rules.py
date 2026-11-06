"""The rules every operation of our API follows, checked against its OpenAPI
schema: an operation id and a one-line description. An MCP tool's schema also
takes no optional inputs, including inside nested models (`optional_fields`)."""

from typing import Any


def references(node: Any) -> list[str]:
    """The names of the models a part of the schema refers to."""
    if isinstance(node, dict):
        found = [node["$ref"].split("/")[-1]] if "$ref" in node else []
        return found + [name for value in node.values() for name in references(value)]
    if isinstance(node, list):
        return [name for value in node for name in references(value)]
    return []


def optional_fields(models: dict[str, Any], name: str, seen: set[str]) -> list[str]:
    """The optional fields of a model and of every model inside it."""
    if name in seen:
        return []
    seen.add(name)
    properties = models[name].get("properties", {})
    optional = set(properties) - set(models[name].get("required", []))
    found = [f"{name}.{field} is optional" for field in sorted(optional)]
    for inner in dict.fromkeys(
        references(properties) + references(models[name].get("oneOf", []))
    ):
        found += optional_fields(models, inner, seen)
    return found


def tool_input_breaks(name: str, schema: dict[str, Any]) -> list[str]:
    """Where an MCP tool's input schema has an optional field."""
    models = {**schema.get("$defs", {}), name: schema}
    return optional_fields(models, name, set())


def rule_breaks(schema: dict[str, Any]) -> list[str]:
    """Every place the API breaks the rules, or nothing when it keeps them."""
    breaks = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            name = f"{method.upper()} {path}"
            description = operation.get("description", "")
            if not operation.get("operationId"):
                breaks.append(f"{name} has no operation id")
            if not description or "\n" in description:
                breaks.append(f"{name} needs a one-line description")
    return breaks
