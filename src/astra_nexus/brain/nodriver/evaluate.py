from __future__ import annotations

from typing import Any


async def evaluate_value(tab: Any, expression: str, await_promise: bool = False) -> Any:
    try:
        result = await tab.evaluate(
            expression,
            await_promise=await_promise,
            return_by_value=True,
        )
    except TypeError as exc:
        message = str(exc)
        if "return_by_value" not in message and "await_promise" not in message:
            raise
        result = await tab.evaluate(expression)

    error = _extract_evaluate_error(result)
    if error is not None:
        raise RuntimeError(f"JavaScript evaluate failed: {error}")
    return unwrap_remote_value(result)


def unwrap_remote_value(value: Any) -> Any:
    """Convert CDP RemoteObject-shaped values to plain Python values."""
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2:
        remote_object, error = value
        if error is not None:
            return {"error": _stringify_error(error)}
        return unwrap_remote_value(remote_object)
    if isinstance(value, list):
        return [unwrap_remote_value(item) for item in value]
    if isinstance(value, dict):
        error = _extract_evaluate_error(value)
        if error is not None:
            return {"error": _stringify_error(error)}
        if "result" in value and isinstance(value["result"], dict):
            return unwrap_remote_value(value["result"])
        if _looks_like_remote_object(value):
            if "value" in value:
                return unwrap_remote_value(value["value"])
            if "deepSerializedValue" in value:
                return unwrap_remote_value(value["deepSerializedValue"])
            if "deep_serialized_value" in value:
                return unwrap_remote_value(value["deep_serialized_value"])
            if "unserializableValue" in value:
                return value["unserializableValue"]
            if "unserializable_value" in value:
                return value["unserializable_value"]
            return None
        if _looks_like_deep_serialized_value(value):
            return unwrap_remote_value(value.get("value"))
        return {key: unwrap_remote_value(item) for key, item in value.items()}
    if _looks_like_exception_details(value):
        return {"error": _stringify_error(value)}
    if _has_remote_object_attributes(value):
        if hasattr(value, "value"):
            remote_value = value.value
            if remote_value is not None:
                return unwrap_remote_value(remote_value)
        deep_serialized_value = getattr(value, "deep_serialized_value", None)
        if deep_serialized_value is not None:
            return unwrap_remote_value(deep_serialized_value)
        unserializable_value = getattr(value, "unserializable_value", None)
        if unserializable_value is not None:
            return unserializable_value
        return None
    if _has_deep_serialized_value_attributes(value):
        return unwrap_remote_value(getattr(value, "value", None))
    return value


def unwrap_evaluate_result(result: Any) -> Any:
    return unwrap_remote_value(result)


def _looks_like_remote_object(value: dict[str, Any]) -> bool:
    return ("type" in value or "type_" in value) and (
        "value" in value
        or "unserializableValue" in value
        or "unserializable_value" in value
        or "deepSerializedValue" in value
        or "deep_serialized_value" in value
        or "objectId" in value
        or "object_id" in value
        or "description" in value
        or "subtype" in value
    )


def _looks_like_deep_serialized_value(value: dict[str, Any]) -> bool:
    return ("type" in value or "type_" in value) and "value" in value


def _has_remote_object_attributes(value: Any) -> bool:
    return hasattr(value, "type_") and (
        hasattr(value, "value")
        or hasattr(value, "deep_serialized_value")
        or hasattr(value, "unserializable_value")
        or hasattr(value, "object_id")
    )


def _has_deep_serialized_value_attributes(value: Any) -> bool:
    return hasattr(value, "type_") and hasattr(value, "value")


def _looks_like_exception_details(value: Any) -> bool:
    if isinstance(value, dict):
        return "exceptionId" in value or "exception_id" in value
    return hasattr(value, "exception_id") or hasattr(value, "text")


def _extract_evaluate_error(value: Any) -> Any | None:
    if isinstance(value, tuple) and len(value) == 2:
        return value[1]
    if isinstance(value, dict):
        return value.get("exceptionDetails") or value.get("exception_details")
    if _looks_like_exception_details(value):
        return value
    return None


def _stringify_error(error: Any) -> str:
    text = getattr(error, "text", None)
    if text:
        return str(text)
    description = getattr(getattr(error, "exception", None), "description", None)
    if description:
        return str(description)
    if isinstance(error, dict):
        return str(error.get("text") or error.get("description") or error)
    return str(error)
