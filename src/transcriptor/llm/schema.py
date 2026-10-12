"""Esquemas JSON estrictos a partir de modelos Pydantic, aceptados por todos los proveedores."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from transcriptor.llm.base import LLMInvalidOutputError

T = TypeVar("T", bound=BaseModel)

_DROP = {"title", "default", "examples", "format"}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Esquema sin $ref, con ``additionalProperties: false`` y todas las propiedades requeridas."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: resolve(v) for k, v in node.items() if k not in _DROP}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(raw)


def parse_json_output(text: str, schema: type[T]) -> T:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[cleaned.find("{") :]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise LLMInvalidOutputError("La respuesta no contiene JSON")
    try:
        return schema.model_validate(json.loads(cleaned[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMInvalidOutputError(f"JSON inválido: {exc}") from exc
