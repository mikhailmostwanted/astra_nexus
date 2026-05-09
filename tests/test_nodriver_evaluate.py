from astra_nexus.brain.nodriver.evaluate import unwrap_evaluate_result, unwrap_remote_value


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


def test_unwrap_evaluate_result_handles_cdp_result_wrapper() -> None:
    result = {"result": {"type": "string", "value": "complete"}}

    assert unwrap_evaluate_result(result) == "complete"
