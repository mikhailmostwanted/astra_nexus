from __future__ import annotations

from typing import Any


async def evaluate_value(tab: Any, expression: str, await_promise: bool = False) -> Any:
    diagnostics = await evaluate_with_diagnostics(tab, expression, await_promise=await_promise)
    if diagnostics.get("exception") is not None:
        raise RuntimeError(f"JavaScript evaluate failed: {diagnostics['exception']}")
    return diagnostics["value"]


async def evaluate_with_diagnostics(
    tab: Any,
    expression: str,
    await_promise: bool = False,
) -> dict[str, Any]:
    raw_result: Any = None
    exception: dict[str, str] | None = None
    method = "tab.evaluate"
    try:
        raw_result = await tab.evaluate(
            expression,
            await_promise=await_promise,
            return_by_value=True,
        )
    except TypeError as exc:
        message = str(exc)
        if "return_by_value" not in message and "await_promise" not in message:
            exception = _exception_payload(exc)
        else:
            try:
                raw_result = await tab.evaluate(expression)
            except Exception as fallback_exc:
                exception = _exception_payload(fallback_exc)
    except Exception as exc:
        exception = _exception_payload(exc)

    error = _extract_evaluate_error(raw_result)
    if error is not None:
        exception = {
            "type": type(error).__name__,
            "message": _stringify_error(error),
        }

    value = None if exception else unwrap_remote_value(raw_result)
    if value is None and exception is None and hasattr(tab, "send"):
        fallback = await _evaluate_via_cdp(tab, expression, await_promise=await_promise)
        method = fallback["method"]
        raw_result = fallback["raw_result"]
        exception = fallback["exception"]
        value = None if exception else unwrap_remote_value(raw_result)

    return {
        "value": value,
        "method": method,
        "raw_result_type": type(raw_result).__name__,
        "raw_result_repr": _safe_repr(raw_result),
        "exception": exception,
    }


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
        if _looks_like_key_value_pairs(value):
            return {
                str(item[0]): unwrap_remote_value(item[1])
                for item in value
                if isinstance(item, (list, tuple)) and len(item) == 2
            }
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


def _looks_like_key_value_pairs(value: list[Any]) -> bool:
    if not value:
        return False
    return all(
        isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str)
        for item in value
    )


async def _evaluate_via_cdp(
    tab: Any,
    expression: str,
    *,
    await_promise: bool,
) -> dict[str, Any]:
    try:
        from nodriver import cdp

        raw_result = await tab.send(
            cdp.runtime.evaluate(
                expression=expression,
                user_gesture=True,
                await_promise=await_promise,
                return_by_value=True,
                allow_unsafe_eval_blocked_by_csp=True,
            )
        )
    except Exception as exc:
        return {
            "method": "cdp.runtime.evaluate",
            "raw_result": None,
            "exception": _exception_payload(exc),
        }
    error = _extract_evaluate_error(raw_result)
    return {
        "method": "cdp.runtime.evaluate",
        "raw_result": raw_result,
        "exception": (
            {
                "type": type(error).__name__,
                "message": _stringify_error(error),
            }
            if error is not None
            else None
        ),
    }


def _exception_payload(exc: Exception) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
    }


def _safe_repr(value: Any, *, limit: int = 8000) -> str:
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"
