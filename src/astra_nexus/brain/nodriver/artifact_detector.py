from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

SUPPORTED_FILE_EXTENSIONS = {
    "csv",
    "docx",
    "gif",
    "jpeg",
    "jpg",
    "json",
    "md",
    "pdf",
    "png",
    "pptx",
    "txt",
    "webp",
    "xlsx",
    "zip",
}

DOWNLOAD_CONTEXT_RE = re.compile(
    r"download|скач|file|файл|attachment|вложени|document|документ|"
    r"filename|artifact|card",
    re.IGNORECASE,
)
FILENAME_RE = re.compile(
    r"(?P<filename>[\w\u0400-\u04ff .()_\-]{1,140}\."
    r"(?P<extension>csv|docx|gif|jpeg|jpg|json|md|pdf|png|pptx|txt|webp|xlsx|zip))",
    re.IGNORECASE,
)
CHATGPT_DOWNLOAD_RE = re.compile(
    r"chatgpt\.com/.*/(files?|download)|backend-api/.*/files?|"
    r"files\.oaiusercontent\.com|download",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArtifactCandidate:
    candidate_id: str
    kind: str
    tag_name: str
    text: str = ""
    href: str | None = None
    download_attr: str | None = None
    filename: str | None = None
    extension: str | None = None
    selector: str | None = None
    html_snippet: str = ""
    download_url: str | None = None
    button_id: str | None = None
    accepted: bool = True
    rejection_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "tag_name": self.tag_name,
            "text": self.text,
            "href": self.href,
            "download_attr": self.download_attr,
            "filename": self.filename,
            "extension": self.extension,
            "selector": self.selector,
            "html_snippet": self.html_snippet,
            "download_url": self.download_url,
            "button_id": self.button_id,
            "accepted": self.accepted,
            "rejection_reason": self.rejection_reason,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ArtifactDetectionDebug:
    candidates: list[dict[str, Any]]
    rejected_candidates: list[dict[str, Any]]
    html_snippet: str = ""
    visible_text: str = ""
    detected_filename: str | None = None
    detected_extension: str | None = None
    download_url: str | None = None
    download_button_info: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "rejected_candidates": self.rejected_candidates,
            "html_snippet": self.html_snippet,
            "visible_text": self.visible_text,
            "detected_filename": self.detected_filename,
            "detected_extension": self.detected_extension,
            "download_url": self.download_url,
            "download_button_info": self.download_button_info,
        }


@dataclass(frozen=True)
class ArtifactDetectionResult:
    candidates: list[ArtifactCandidate]
    rejected: list[ArtifactCandidate]
    selected: ArtifactCandidate | None
    debug: ArtifactDetectionDebug

    @property
    def has_downloadable_file(self) -> bool:
        return self.selected is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "has_downloadable_file": self.has_downloadable_file,
            "selected": self.selected.as_dict() if self.selected is not None else None,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "rejected_candidates": [candidate.as_dict() for candidate in self.rejected],
            "debug": self.debug.as_dict(),
        }


def detect_artifacts_from_html(
    html: str, *, visible_text: str | None = None
) -> ArtifactDetectionResult:
    parser = _ArtifactHTMLParser()
    parser.feed(str(html or ""))
    text = _compact_text(visible_text if visible_text is not None else parser.visible_text)
    global_filename = _filename_from_text(text) or _filename_from_text(str(html or ""))
    candidates: list[ArtifactCandidate] = []
    rejected: list[ArtifactCandidate] = []
    for index, raw in enumerate(parser.raw_candidates):
        candidate = _candidate_from_raw(
            raw,
            index=index,
            global_filename=global_filename,
        )
        if candidate.accepted:
            candidates.append(candidate)
        else:
            rejected.append(candidate)
    selected = _select_best_candidate(candidates)
    debug = ArtifactDetectionDebug(
        candidates=[candidate.as_dict() for candidate in candidates],
        rejected_candidates=[candidate.as_dict() for candidate in rejected],
        html_snippet=_snippet(html, 4000),
        visible_text=_snippet(text, 4000),
        detected_filename=selected.filename if selected is not None else global_filename,
        detected_extension=selected.extension
        if selected is not None
        else _extension_from_filename(global_filename),
        download_url=selected.download_url if selected is not None else None,
        download_button_info=(
            {
                "candidate_id": selected.candidate_id,
                "button_id": selected.button_id,
                "selector": selected.selector,
                "kind": selected.kind,
                "tag_name": selected.tag_name,
            }
            if selected is not None
            else None
        ),
    )
    return ArtifactDetectionResult(
        candidates=candidates,
        rejected=rejected,
        selected=selected,
        debug=debug,
    )


def artifact_detection_from_probe_payload(payload: Any) -> ArtifactDetectionResult:
    if not isinstance(payload, dict):
        payload = {}
    raw_candidates = _list_of_dicts(payload.get("candidates"))
    raw_rejected = _list_of_dicts(payload.get("rejectedCandidates"))
    candidates = [
        _candidate_from_payload(item, accepted=True)
        for item in raw_candidates
        if _candidate_from_payload(item, accepted=True).accepted
    ]
    rejected = [_candidate_from_payload(item, accepted=False) for item in raw_rejected]
    selected_payload = payload.get("selected")
    selected = (
        _candidate_from_payload(selected_payload, accepted=True)
        if isinstance(selected_payload, dict)
        else _select_best_candidate(candidates)
    )
    debug_payload = payload.get("debug") if isinstance(payload.get("debug"), dict) else {}
    debug = ArtifactDetectionDebug(
        candidates=[candidate.as_dict() for candidate in candidates],
        rejected_candidates=[candidate.as_dict() for candidate in rejected],
        html_snippet=str(debug_payload.get("htmlSnippet") or payload.get("htmlSnippet") or ""),
        visible_text=str(debug_payload.get("visibleText") or payload.get("visibleText") or ""),
        detected_filename=(
            selected.filename
            if selected is not None
            else _str_or_none(debug_payload.get("detectedFilename"))
        ),
        detected_extension=(
            selected.extension
            if selected is not None
            else _str_or_none(debug_payload.get("detectedExtension"))
        ),
        download_url=selected.download_url if selected is not None else None,
        download_button_info=(
            {
                "candidate_id": selected.candidate_id,
                "button_id": selected.button_id,
                "selector": selected.selector,
                "kind": selected.kind,
                "tag_name": selected.tag_name,
            }
            if selected is not None
            else None
        ),
    )
    return ArtifactDetectionResult(
        candidates=candidates,
        rejected=rejected,
        selected=selected,
        debug=debug,
    )


def build_artifact_detector_probe_script() -> str:
    return ARTIFACT_DETECTOR_PROBE_SCRIPT


def _candidate_from_raw(
    raw: dict[str, Any],
    *,
    index: int,
    global_filename: str | None,
) -> ArtifactCandidate:
    attrs = {str(key).lower(): str(value or "") for key, value in raw.get("attrs", {}).items()}
    tag_name = str(raw.get("tag_name") or "").lower()
    text = _compact_text(str(raw.get("text") or ""))
    href = attrs.get("href") or None
    download_attr = attrs.get("download") or None
    haystack = " ".join(
        [
            tag_name,
            text,
            attrs.get("aria-label", ""),
            attrs.get("title", ""),
            attrs.get("class", ""),
            attrs.get("data-testid", ""),
            attrs.get("role", ""),
            download_attr or "",
            href or "",
        ]
    )
    filename = (
        _filename_from_text(download_attr or "")
        or _filename_from_text(text)
        or _filename_from_text(attrs.get("aria-label", ""))
        or _filename_from_text(attrs.get("title", ""))
        or _filename_from_href(href)
        or global_filename
    )
    extension = _extension_from_filename(filename)
    download_url = href if _usable_download_href(href) else None
    has_download_context = bool(DOWNLOAD_CONTEXT_RE.search(haystack))
    has_download_attr = bool(download_attr)
    chatgpt_download = bool(href and CHATGPT_DOWNLOAD_RE.search(href))
    is_clickable = tag_name in {"a", "button"} or attrs.get("role", "").lower() == "button"
    accepted = bool(
        extension
        and is_clickable
        and (has_download_attr or chatgpt_download or has_download_context)
    )
    rejection_reason = None
    if not accepted:
        if tag_name == "a" and href and extension and not has_download_context:
            rejection_reason = "ordinary_link_without_download_context"
        elif not extension:
            rejection_reason = "missing_filename_or_extension"
        elif not is_clickable:
            rejection_reason = "candidate_not_clickable"
        else:
            rejection_reason = "missing_download_context"
    return ArtifactCandidate(
        candidate_id=str(raw.get("candidate_id") or f"html-candidate-{index}"),
        kind=_candidate_kind(haystack, tag_name=tag_name),
        tag_name=tag_name,
        text=text,
        href=href,
        download_attr=download_attr,
        filename=filename,
        extension=extension,
        selector=str(raw.get("selector") or "") or None,
        html_snippet=_snippet(str(raw.get("html") or ""), 1200),
        download_url=download_url,
        button_id=str(raw.get("button_id") or raw.get("candidate_id") or "") or None,
        accepted=accepted,
        rejection_reason=rejection_reason,
        metadata={
            "aria_label": attrs.get("aria-label", ""),
            "title": attrs.get("title", ""),
            "class_name": attrs.get("class", ""),
            "data_testid": attrs.get("data-testid", ""),
            "chatgpt_download_endpoint": chatgpt_download,
        },
    )


def _candidate_from_payload(payload: Any, *, accepted: bool) -> ArtifactCandidate:
    if not isinstance(payload, dict):
        payload = {}
    rejection_reason = _str_or_none(payload.get("rejection_reason") or payload.get("reason"))
    return ArtifactCandidate(
        candidate_id=str(payload.get("candidate_id") or payload.get("candidateId") or ""),
        kind=str(payload.get("kind") or "unknown"),
        tag_name=str(payload.get("tag_name") or payload.get("tagName") or ""),
        text=str(payload.get("text") or ""),
        href=_str_or_none(payload.get("href")),
        download_attr=_str_or_none(payload.get("download_attr") or payload.get("downloadAttr")),
        filename=_str_or_none(payload.get("filename")),
        extension=_str_or_none(payload.get("extension")),
        selector=_str_or_none(payload.get("selector")),
        html_snippet=str(payload.get("html_snippet") or payload.get("htmlSnippet") or ""),
        download_url=_str_or_none(payload.get("download_url") or payload.get("downloadUrl")),
        button_id=_str_or_none(payload.get("button_id") or payload.get("buttonId")),
        accepted=bool(payload.get("accepted", accepted)),
        rejection_reason=rejection_reason,
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def _select_best_candidate(candidates: list[ArtifactCandidate]) -> ArtifactCandidate | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda candidate: (
            0 if candidate.download_url else 1,
            0 if candidate.download_attr else 1,
            0 if candidate.filename else 1,
            len(candidate.text),
        ),
    )[0]


class _ArtifactHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw_candidates: list[dict[str, Any]] = []
        self.text_parts: list[str] = []
        self._open_candidates: list[dict[str, Any]] = []

    @property
    def visible_text(self) -> str:
        return _compact_text(" ".join(self.text_parts))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if self._is_candidate(tag.lower(), attrs_dict):
            self._open_candidates.append(
                {
                    "tag_name": tag.lower(),
                    "attrs": attrs_dict,
                    "text_parts": [],
                    "html": self.get_starttag_text() or "",
                    "candidate_id": f"html-candidate-{len(self.raw_candidates)}",
                }
            )

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open_candidates) - 1, -1, -1):
            candidate = self._open_candidates[index]
            if candidate["tag_name"] != tag.lower():
                continue
            candidate["text"] = _compact_text(" ".join(candidate.pop("text_parts", [])))
            candidate["html"] = f"{candidate.get('html', '')}</{tag}>"
            self.raw_candidates.append(candidate)
            del self._open_candidates[index]
            return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text_parts.append(data)
        for candidate in self._open_candidates:
            candidate["text_parts"].append(data)
            candidate["html"] = f"{candidate.get('html', '')}{data}"

    def _is_candidate(self, tag: str, attrs: dict[str, str]) -> bool:
        haystack = " ".join(
            [
                tag,
                attrs.get("role", ""),
                attrs.get("class", ""),
                attrs.get("data-testid", ""),
                attrs.get("aria-label", ""),
                attrs.get("title", ""),
                attrs.get("download", ""),
                attrs.get("href", ""),
            ]
        )
        return (
            tag in {"a", "button"}
            or attrs.get("role", "").lower() == "button"
            or bool(attrs.get("download"))
            or bool(DOWNLOAD_CONTEXT_RE.search(haystack))
        )


def _candidate_kind(haystack: str, *, tag_name: str) -> str:
    normalized = haystack.lower()
    if "file-card" in normalized or "file card" in normalized:
        return "file_card"
    if "attachment" in normalized or "вложени" in normalized:
        return "attachment"
    if "filename" in normalized:
        return "filename_chip"
    if "download" in normalized or "скач" in normalized:
        return "download_button"
    if tag_name == "a":
        return "download_link"
    if tag_name == "button":
        return "download_button"
    return "candidate"


def _filename_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = FILENAME_RE.search(unquote(str(value)))
    if not match:
        return None
    filename = Path(match.group("filename").strip()).name
    return filename or None


def _filename_from_href(href: str | None) -> str | None:
    if not href:
        return None
    parsed = urlparse(href)
    for source in (parsed.path, parsed.query):
        filename = _filename_from_text(unquote(source))
        if filename:
            return filename
    basename = Path(unquote(parsed.path)).name
    return basename if _extension_from_filename(basename) else None


def _extension_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix if suffix in SUPPORTED_FILE_EXTENSIONS else None


def _usable_download_href(href: str | None) -> bool:
    if not href:
        return False
    normalized = href.strip().lower()
    return not (
        normalized.startswith("#")
        or normalized.startswith("javascript:")
        or normalized.startswith("mailto:")
    )


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _snippet(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


ARTIFACT_DETECTOR_PROBE_SCRIPT = r"""
/* ASTRA_NEXUS_ARTIFACT_DETECTOR_PROBE */
(() => {
  const supportedExtensions = new Set(
    [
      "csv",
      "docx",
      "gif",
      "jpeg",
      "jpg",
      "json",
      "md",
      "pdf",
      "png",
      "pptx",
      "txt",
      "webp",
      "xlsx",
      "zip",
    ]
  );
  const filenamePattern = new RegExp(
    '([\\w\\u0400-\\u04ff .()_-]{1,140}\\.' +
      '(csv|docx|gif|jpeg|jpg|json|md|pdf|png|pptx|txt|webp|xlsx|zip))',
    'i'
  );
  const downloadContextPattern =
    /download|скач|file|файл|attachment|вложени|document|документ|filename|artifact|card/i;
  const chatgptDownloadPattern =
    /chatgpt\.com\/.*\/(files?|download)|backend-api\/.*\/files?|files\.oaiusercontent\.com|download/i;
  const messageRootSelector = [
    '[data-turn="assistant"]',
    '[data-message-author-role="assistant"]',
    'article',
  ].join(', ');
  const candidateSelector = [
    'a[href]',
    'button',
    '[role="button"]',
    '[download]',
    '[data-testid*="file" i]',
    '[data-testid*="download" i]',
    '[data-testid*="attachment" i]',
    '[class*="file" i]',
    '[class*="download" i]',
    '[class*="attachment" i]',
    '[aria-label*="download" i]',
    '[aria-label*="скач" i]',
  ].join(', ');

  function attr(node, name) {
    return node && node.getAttribute ? node.getAttribute(name) || '' : '';
  }

  function normalizeText(value) {
    return String(value || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function snippet(value, limit = 1600) {
    const text = String(value || '');
    return text.length <= limit ? text : `${text.slice(0, limit - 1).trimEnd()}...`;
  }

  function extensionFromFilename(filename) {
    const match = String(filename || '').toLowerCase().match(/\.([a-z0-9]+)$/);
    if (!match) return null;
    return supportedExtensions.has(match[1]) ? match[1] : null;
  }

  function filenameFromText(value) {
    const decoded = decodeURIComponent(String(value || ''));
    const match = decoded.match(filenamePattern);
    if (!match) return null;
    return match[1].split(/[\\/]/).pop();
  }

  function filenameFromHref(href) {
    if (!href) return null;
    return filenameFromText(href);
  }

  function usableHref(href) {
    const normalized = String(href || '').trim().toLowerCase();
    return (
      normalized &&
      !normalized.startsWith('#') &&
      !normalized.startsWith('javascript:') &&
      !normalized.startsWith('mailto:')
    );
  }

  function roleOf(node) {
    const explicit = attr(node, 'data-turn') || attr(node, 'data-message-author-role');
    if (explicit === 'assistant') return 'assistant';
    const child = node.querySelector(
      '[data-turn="assistant"], [data-message-author-role="assistant"]'
    );
    return child ? 'assistant' : '';
  }

  function latestAssistantRoot() {
    const roots = Array.from(document.querySelectorAll(messageRootSelector))
      .filter((node) => roleOf(node) === 'assistant')
      .filter((node, index, array) => array.indexOf(node) === index);
    return roots[roots.length - 1] || null;
  }

  function describeNode(node, index) {
    const tag = (node.tagName || '').toLowerCase();
    const dataTestid = attr(node, 'data-testid');
    const ariaLabel = attr(node, 'aria-label');
    if (dataTestid) return `${tag}[data-testid="${dataTestid}"]`;
    if (ariaLabel) return `${tag}[aria-label="${ariaLabel}"]`;
    return `${tag}:candidate(${index})`;
  }

  function candidateKind(haystack, tagName) {
    const normalized = haystack.toLowerCase();
    if (normalized.includes('file-card') || normalized.includes('file card')) return 'file_card';
    if (normalized.includes('attachment') || normalized.includes('вложени')) return 'attachment';
    if (normalized.includes('filename')) return 'filename_chip';
    if (normalized.includes('download') || normalized.includes('скач')) return 'download_button';
    if (tagName === 'a') return 'download_link';
    if (tagName === 'button') return 'download_button';
    return 'candidate';
  }

  function normalizeCandidate(node, index, fallbackFilename) {
    const tagName = (node.tagName || '').toLowerCase();
    const text = normalizeText(node.innerText || node.textContent || '');
    const href = node.href || attr(node, 'href') || '';
    const downloadAttr = attr(node, 'download');
    const haystack = [
      tagName,
      text,
      href,
      downloadAttr,
      attr(node, 'aria-label'),
      attr(node, 'title'),
      attr(node, 'class'),
      attr(node, 'data-testid'),
      attr(node, 'role'),
    ].join(' ');
    const filename =
      filenameFromText(downloadAttr) ||
      filenameFromText(text) ||
      filenameFromText(attr(node, 'aria-label')) ||
      filenameFromText(attr(node, 'title')) ||
      filenameFromHref(href) ||
      fallbackFilename;
    const extension = extensionFromFilename(filename);
    const hasDownloadContext = downloadContextPattern.test(haystack);
    const hasDownloadAttr = Boolean(downloadAttr);
    const isChatgptDownload = Boolean(href && chatgptDownloadPattern.test(href));
    const isClickable =
      tagName === 'a' ||
      tagName === 'button' ||
      attr(node, 'role').toLowerCase() === 'button';
    const accepted = Boolean(
      extension &&
      isClickable &&
      (hasDownloadAttr || isChatgptDownload || hasDownloadContext)
    );
    let rejectionReason = null;
    if (!accepted) {
      if (tagName === 'a' && href && extension && !hasDownloadContext) {
        rejectionReason = 'ordinary_link_without_download_context';
      } else if (!extension) {
        rejectionReason = 'missing_filename_or_extension';
      } else if (!isClickable) {
        rejectionReason = 'candidate_not_clickable';
      } else {
        rejectionReason = 'missing_download_context';
      }
    }
    const candidateId = `astra-artifact-candidate-${index}`;
    try {
      node.setAttribute('data-astra-artifact-candidate-id', candidateId);
    } catch (_error) {}
    return {
      candidate_id: candidateId,
      candidateId,
      kind: candidateKind(haystack, tagName),
      tag_name: tagName,
      tagName,
      text,
      href: href || null,
      download_attr: downloadAttr || null,
      downloadAttr: downloadAttr || null,
      filename: filename || null,
      extension: extension || null,
      selector: describeNode(node, index),
      html_snippet: snippet(node.outerHTML || '', 1200),
      htmlSnippet: snippet(node.outerHTML || '', 1200),
      download_url: usableHref(href) ? href : null,
      downloadUrl: usableHref(href) ? href : null,
      button_id: candidateId,
      buttonId: candidateId,
      accepted,
      rejection_reason: rejectionReason,
      reason: rejectionReason,
      metadata: {
        aria_label: attr(node, 'aria-label'),
        title: attr(node, 'title'),
        class_name: attr(node, 'class'),
        data_testid: attr(node, 'data-testid'),
        chatgpt_download_endpoint: isChatgptDownload,
      },
    };
  }

  function selectBest(candidates) {
    return candidates.slice().sort((left, right) => {
      const leftScore =
        (left.download_url ? 0 : 10) +
        (left.download_attr ? 0 : 5) +
        (left.filename ? 0 : 2);
      const rightScore =
        (right.download_url ? 0 : 10) +
        (right.download_attr ? 0 : 5) +
        (right.filename ? 0 : 2);
      return leftScore - rightScore;
    })[0] || null;
  }

  const root = latestAssistantRoot();
  const htmlSnippet = root ? snippet(root.outerHTML || '', 4000) : '';
  const visibleText = root ? normalizeText(root.innerText || root.textContent || '') : '';
  const fallbackFilename = filenameFromText(visibleText) || filenameFromText(htmlSnippet);
  const rawCandidates = root ? Array.from(root.querySelectorAll(candidateSelector)) : [];
  const normalized = rawCandidates.map((node, index) =>
    normalizeCandidate(node, index, fallbackFilename)
  );
  const candidates = normalized.filter((candidate) => candidate.accepted);
  const rejectedCandidates = normalized.filter((candidate) => !candidate.accepted);
  const selected = selectBest(candidates);
  const payload = {
    candidates,
    rejectedCandidates,
    selected,
    htmlSnippet,
    visibleText,
    debug: {
      candidates,
      rejectedCandidates,
      htmlSnippet,
      visibleText,
      detectedFilename: selected ? selected.filename : fallbackFilename,
      detectedExtension: selected ? selected.extension : extensionFromFilename(fallbackFilename),
      downloadUrl: selected ? selected.download_url : null,
      downloadButtonInfo: selected ? {
        candidate_id: selected.candidate_id,
        button_id: selected.button_id,
        selector: selected.selector,
        kind: selected.kind,
        tag_name: selected.tag_name,
      } : null,
    },
  };
  return JSON.parse(JSON.stringify(payload));
})()
"""
