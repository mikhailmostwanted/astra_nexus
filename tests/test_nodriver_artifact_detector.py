from __future__ import annotations

from astra_nexus.brain.nodriver.artifact_detector import detect_artifacts_from_html


def test_artifact_detector_accepts_chatgpt_file_card() -> None:
    html = """
    <article data-message-author-role="assistant">
      <div data-testid="file-card" class="file-card">
        <span class="filename">strategy.docx</span>
        <a
          href="https://chatgpt.com/backend-api/files/file-123/download"
          download="strategy.docx"
          aria-label="Download strategy.docx"
        >Download</a>
      </div>
    </article>
    """

    result = detect_artifacts_from_html(html)

    assert result.selected is not None
    assert result.selected.filename == "strategy.docx"
    assert result.selected.extension == "docx"
    assert result.selected.download_url == "https://chatgpt.com/backend-api/files/file-123/download"
    assert result.debug.detected_filename == "strategy.docx"
    assert result.debug.detected_extension == "docx"
    assert result.debug.candidates


def test_artifact_detector_does_not_accept_plain_external_link_as_file() -> None:
    html = """
    <article data-message-author-role="assistant">
      <p>Полезная ссылка:
        <a href="https://example.com/report.pdf">report.pdf</a>
      </p>
    </article>
    """

    result = detect_artifacts_from_html(html)

    assert result.selected is None
    assert result.candidates == []
    assert result.rejected
    assert result.rejected[0].rejection_reason == "ordinary_link_without_download_context"
