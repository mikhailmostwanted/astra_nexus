import asyncio

from astra_nexus.brain.nodriver.evaluate import (
    evaluate_value,
    unwrap_evaluate_result,
    unwrap_remote_value,
)


class FakeTab:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def evaluate(
        self,
        expression: str,
        *,
        await_promise: bool = False,
        return_by_value: bool = False,
    ) -> object:
        self.calls.append(
            {
                "expression": expression,
                "await_promise": await_promise,
                "return_by_value": return_by_value,
            }
        )
        return self.result


class RemoteObjectLike:
    type_ = "number"
    subtype = None
    description = None
    deep_serialized_value = None
    unserializable_value = None
    object_id = None

    def __init__(self, value: object) -> None:
        self.value = value


def test_unwrap_remote_value_string() -> None:
    assert unwrap_remote_value({"type": "string", "value": "loading"}) == "loading"


def test_unwrap_remote_value_number() -> None:
    assert unwrap_remote_value({"type": "number", "value": 3}) == 3


def test_unwrap_remote_value_recurses_through_lists_and_dicts() -> None:
    payload = {
        "ready_state": {"type": "string", "value": "complete"},
        "counts": [{"type": "number", "value": 3}],
    }

    assert unwrap_remote_value(payload) == {"ready_state": "complete", "counts": [3]}


def test_unwrap_remote_value_converts_deep_serialized_object_pairs() -> None:
    payload = [
        ["readyState", "complete"],
        ["textareaCount", 1],
        [
            "candidates",
            [
                [
                    ["selectorHint", "#prompt-textarea"],
                    ["isVisible", True],
                ]
            ],
        ],
    ]

    assert unwrap_remote_value(payload) == {
        "readyState": "complete",
        "textareaCount": 1,
        "candidates": [{"selectorHint": "#prompt-textarea", "isVisible": True}],
    }


def test_unwrap_evaluate_result_handles_cdp_result_wrapper() -> None:
    result = {"result": {"type": "string", "value": "complete"}}

    assert unwrap_evaluate_result(result) == "complete"


def test_evaluate_value_uses_return_by_value_and_unwraps_string() -> None:
    tab = FakeTab({"type": "string", "value": "complete"})

    assert asyncio.run(evaluate_value(tab, "document.readyState")) == "complete"
    assert tab.calls == [
        {
            "expression": "document.readyState",
            "await_promise": False,
            "return_by_value": True,
        }
    ]


def test_evaluate_value_unwraps_number_remote_object() -> None:
    tab = FakeTab({"type": "number", "value": 1})

    assert asyncio.run(evaluate_value(tab, "document.querySelectorAll('textarea').length")) == 1


def test_unwrap_remote_value_preserves_falsy_remote_object_value() -> None:
    assert unwrap_remote_value(RemoteObjectLike(0)) == 0


def test_unwrap_remote_value_preserves_falsy_values() -> None:
    payload = [
        ["zero", 0],
        ["falseValue", False],
        ["emptyString", ""],
        ["emptyList", []],
    ]

    assert unwrap_remote_value(payload) == {
        "zero": 0,
        "falseValue": False,
        "emptyString": "",
        "emptyList": [],
    }
