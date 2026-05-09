from __future__ import annotations

from typing import Any


def unwrap_remote_value(value: Any) -> Any:
    """Convert CDP RemoteObject-shaped values to plain Python values."""
    if value is None:
        return None
    if isinstance(value, list):
        return [unwrap_remote_value(item) for item in value]
    if isinstance(value, dict):
        if _looks_like_remote_object(value):
            if "value" in value:
                return unwrap_remote_value(value["value"])
            if "unserializableValue" in value:
                return value["unserializableValue"]
            return None
        return {key: unwrap_remote_value(item) for key, item in value.items()}
    return value


def unwrap_evaluate_result(result: Any) -> Any:
    if isinstance(result, dict) and _looks_like_evaluate_result(result):
        return unwrap_remote_value(result["result"])
    return unwrap_remote_value(result)


def _looks_like_remote_object(value: dict[str, Any]) -> bool:
    return "type" in value and (
        "value" in value
        or "unserializableValue" in value
        or "objectId" in value
        or "description" in value
        or "subtype" in value
    )


def _looks_like_evaluate_result(value: dict[str, Any]) -> bool:
    result = value.get("result")
    return isinstance(result, dict) and _looks_like_remote_object(result)
