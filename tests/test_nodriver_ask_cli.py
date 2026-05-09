import asyncio

from astra_nexus.brain.nodriver.ask import run
from astra_nexus.brain.nodriver.exceptions import NoDriverPromptBoxNotFoundError


class FailingProvider:
    async def ask(self, agent_id: str, prompt: str, context: dict | None = None):
        raise NoDriverPromptBoxNotFoundError(
            "Поле ввода ChatGPT не найдено.",
            stage="chatgpt.prompt_box.search.started",
            url="https://chatgpt.com/",
            selector="#prompt-textarea",
        )


class OkProvider:
    async def ask(self, agent_id: str, prompt: str, context: dict | None = None):
        class Response:
            content = "Astra Nexus online."

        return Response()


def test_nodriver_ask_cli_prints_structured_error(capsys) -> None:
    exit_code = asyncio.run(run(["Привет"], provider=FailingProvider()))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "status: prompt_box_not_found" in output
    assert "stage: chatgpt.prompt_box.search.started" in output
    assert "message: Поле ввода ChatGPT не найдено." in output


def test_nodriver_ask_cli_prints_answer(capsys) -> None:
    exit_code = asyncio.run(run(["Привет"], provider=OkProvider()))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "status: ok" in output
    assert "Astra Nexus online." in output
