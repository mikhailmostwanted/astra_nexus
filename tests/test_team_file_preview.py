from __future__ import annotations

import inspect

from astra_nexus.team import file_preview as file_preview_module


def test_file_preview_cli_runs_fake_provider_with_attachment(tmp_path, capsys) -> None:
    file_path = tmp_path / "brief.md"
    file_path.write_text("Файл для preview", encoding="utf-8")

    exit_code = file_preview_module.main(["--file", str(file_path), "проверь файл"])

    output = capsys.readouterr().out
    source = inspect.getsource(file_preview_module)
    assert exit_code == 0
    assert "[Файл] brief.md: extracted" in output
    assert "extracted_chars:" in output
    assert "preview:" in output
    assert "Файл для preview" in output
    assert "проверь файл" in output
    assert "fake:final_composer" not in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
