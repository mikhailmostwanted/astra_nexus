from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

TELEGRAM_HTML_PARSE_MODE = "HTML"
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_INTERNAL_CHUNK_LIMIT = 3800


@dataclass(frozen=True)
class TelegramRenderedChunk:
    text: str
    parse_mode: str | None = TELEGRAM_HTML_PARSE_MODE


@dataclass(frozen=True)
class TelegramRenderResult:
    source: str
    chunks: tuple[TelegramRenderedChunk, ...]

    @property
    def parse_mode(self) -> str | None:
        return self.chunks[0].parse_mode if self.chunks else None


def render_answer_for_telegram(
    text: str,
    *,
    structured_answer: dict | None = None,
    chunk_limit: int = TELEGRAM_INTERNAL_CHUNK_LIMIT,
) -> TelegramRenderResult:
    structured_answer = structured_answer if isinstance(structured_answer, dict) else {}
    html = str(structured_answer.get("html") or "").strip()
    if html:
        converter = ChatGPTHTMLToTelegram()
        blocks = converter.convert(html)
        source_links = _dedupe_links(
            [
                *_links_from_structured_answer(structured_answer),
                *converter.source_links,
            ]
        )
        if source_links:
            blocks.append(_source_links_block(source_links))
        chunks = tuple(
            TelegramRenderedChunk(chunk, TELEGRAM_HTML_PARSE_MODE)
            for chunk in split_telegram_html_blocks(blocks, chunk_limit=chunk_limit)
        )
        if chunks:
            return TelegramRenderResult(source="structured_html", chunks=chunks)

    plain_chunks = tuple(
        TelegramRenderedChunk(chunk, None)
        for chunk in split_plain_text(str(text or ""), chunk_limit=chunk_limit)
    )
    return TelegramRenderResult(source="plain_text", chunks=plain_chunks)


def split_plain_text(
    text: str,
    *,
    chunk_limit: int = TELEGRAM_INTERNAL_CHUNK_LIMIT,
) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []
    paragraphs = re.split(r"\n{2,}", normalized)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > chunk_limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_plain_block(paragraph, chunk_limit=chunk_limit))
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= chunk_limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def split_telegram_html_blocks(
    blocks: Iterable[str],
    *,
    chunk_limit: int = TELEGRAM_INTERNAL_CHUNK_LIMIT,
) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in blocks:
        block = str(block or "").strip()
        if not block:
            continue
        if _telegram_plain_length(block) > chunk_limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_oversized_html_block(block, chunk_limit=chunk_limit))
            continue
        candidate = f"{current}\n\n{block}" if current else block
        if _telegram_plain_length(candidate) <= chunk_limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block
    if current:
        chunks.append(current)
    return chunks


def strip_telegram_html(value: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(str(value or ""))
    return parser.text()


class ChatGPTHTMLToTelegram(HTMLParser):
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
    paragraph_tags = {"p"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.list_stack: list[dict[str, int | str]] = []
        self.pre_depth = 0
        self.pre_buffer: list[str] = []
        self.skip_depth = 0
        self.source_links: list[dict[str, str]] = []

    def convert(self, html: str) -> list[str]:
        self.feed(html)
        self.close()
        self._flush_block()
        return self.blocks

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = _attrs_dict(attrs)
        if self.skip_depth:
            self.skip_depth += 1
            return
        if _is_source_chip(tag, attrs_dict):
            self._capture_source_link(attrs_dict)
            self.skip_depth = 1
            return
        if tag in {"script", "style", "noscript", "svg", "canvas", "button", "nav", "menu"}:
            self.skip_depth = 1
            return
        if self.pre_depth:
            self.pre_depth += 1
            return
        if tag == "pre":
            self._flush_block()
            self.pre_depth = 1
            self.pre_buffer = []
            return
        if tag in self.paragraph_tags:
            self._flush_block()
            return
        if tag in self.heading_tags:
            self._flush_block()
            self._append("<b>")
            return
        if tag in {"strong", "b"}:
            self._append("<b>")
            return
        if tag in {"em", "i"}:
            self._append("<i>")
            return
        if tag == "code":
            self._append("<code>")
            return
        if tag == "a":
            href = attrs_dict.get("href", "").strip()
            if href:
                self._append(f'<a href="{escape(href, quote=True)}">')
            return
        if tag in {"ul", "ol"}:
            self._flush_block()
            self.list_stack.append({"tag": tag, "counter": 1})
            return
        if tag == "li":
            self._flush_block()
            self._append(self._list_prefix())
            return
        if tag == "blockquote":
            self._flush_block()
            self._append("<blockquote>")
            return
        if tag == "br":
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if self.pre_depth:
            if tag == "pre" and self.pre_depth == 1:
                text = "".join(self.pre_buffer).strip("\n")
                self.pre_depth = 0
                self.pre_buffer = []
                self.blocks.append(f"<pre><code>{escape(text)}</code></pre>")
                return
            self.pre_depth = max(0, self.pre_depth - 1)
            return
        if tag in self.heading_tags:
            self._append("</b>")
            self._flush_block()
            return
        if tag in self.paragraph_tags:
            self._flush_block()
            return
        if tag in {"strong", "b"}:
            self._append("</b>")
            return
        if tag in {"em", "i"}:
            self._append("</i>")
            return
        if tag == "code":
            self._append("</code>")
            return
        if tag == "a":
            self._append("</a>")
            return
        if tag == "li":
            self._flush_block()
            if self.list_stack and self.list_stack[-1]["tag"] == "ol":
                self.list_stack[-1]["counter"] = int(self.list_stack[-1]["counter"]) + 1
            return
        if tag in {"ul", "ol"}:
            self._flush_block()
            if self.list_stack:
                self.list_stack.pop()
            return
        if tag == "blockquote":
            self._append("</blockquote>")
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.pre_depth:
            self.pre_buffer.append(data)
            return
        self._append_text(data)

    def _append_text(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            if self.current and not self.current[-1].endswith((" ", "\n")):
                self.current.append(" ")
            return
        if not self.current or self.current[-1].endswith((" ", "\n")):
            text = text.lstrip()
        self.current.append(escape(text))

    def _append(self, value: str) -> None:
        self.current.append(value)

    def _flush_block(self) -> None:
        block = "".join(self.current).strip()
        if block and not _empty_list_marker(block):
            self.blocks.append(block)
        self.current = []

    def _list_prefix(self) -> str:
        if not self.list_stack:
            return "• "
        current = self.list_stack[-1]
        if current["tag"] == "ol":
            return f"{int(current['counter'])}. "
        return "• "

    def _capture_source_link(self, attrs: dict[str, str]) -> None:
        href = attrs.get("href", "").strip()
        if href:
            self.source_links.append({"href": href, "label": _source_label_from_href(href)})


class _HTMLTextExtractor(HTMLParser):
    block_tags = {"p", "div", "section", "article", "li", "blockquote", "pre"}
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.block_tags or tag in self.heading_tags:
            self._break()
        if tag == "li":
            self.parts.append("- ")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in self.block_tags or tag in self.heading_tags:
            self._break()

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts).replace("\r\n", "\n")
        lines = [" ".join(line.split()) for line in text.splitlines()]
        compact = "\n".join(lines)
        compact = re.sub(r"\n{3,}", "\n\n", compact)
        return compact.strip()

    def _break(self) -> None:
        current = "".join(self.parts)
        if current and not current.endswith("\n\n"):
            self.parts.append("\n\n")


def _split_oversized_html_block(block: str, *, chunk_limit: int) -> list[str]:
    if block.startswith("<pre><code>"):
        text = strip_telegram_html(block)
        chunks: list[str] = []
        current = ""
        for line in text.splitlines():
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= chunk_limit - 40:
                current = candidate
            else:
                if current:
                    chunks.append(f"<pre><code>{escape(current)}</code></pre>")
                current = line
        if current:
            chunks.append(f"<pre><code>{escape(current)}</code></pre>")
        return chunks
    plain = strip_telegram_html(block)
    return [escape(chunk) for chunk in _split_long_plain_block(plain, chunk_limit=chunk_limit)]


def _split_long_plain_block(text: str, *, chunk_limit: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(word) > chunk_limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                word[index : index + chunk_limit] for index in range(0, len(word), chunk_limit)
            )
            continue
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= chunk_limit:
            current = candidate
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks


def _empty_list_marker(block: str) -> bool:
    plain = strip_telegram_html(block).strip()
    return bool(plain == "•" or re.fullmatch(r"\d+\.", plain))


def _telegram_plain_length(html: str) -> int:
    return len(strip_telegram_html(html))


def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key.lower(): value or "" for key, value in attrs}


def _is_source_chip(tag: str, attrs: dict[str, str]) -> bool:
    haystack = " ".join(
        [
            attrs.get("class", ""),
            attrs.get("data-testid", ""),
            attrs.get("aria-label", ""),
            attrs.get("role", ""),
        ]
    ).lower()
    pattern = (
        r"(^|\s|[-_])"
        r"(source-chip|sourcechip|citation|cite|reference|attribution|web-source)"
        r"(\s|[-_]|$)"
    )
    if re.search(pattern, haystack):
        return True
    return tag == "a" and attrs.get("data-source-chip", "").lower() == "true"


def _links_from_structured_answer(structured_answer: dict) -> list[dict[str, str]]:
    raw_links = structured_answer.get("sourceLinks")
    if not isinstance(raw_links, list):
        return []
    links: list[dict[str, str]] = []
    for item in raw_links:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        if not href:
            continue
        label = str(item.get("label") or "").strip() or _source_label_from_href(href)
        if _looks_like_source_chip_label(label):
            label = _source_label_from_href(href)
        links.append({"href": href, "label": label})
    return links


def _dedupe_links(links: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for link in links:
        href = str(link.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        label = str(link.get("label") or "").strip() or _source_label_from_href(href)
        deduped.append({"href": href, "label": label})
    return deduped


def _source_links_block(links: list[dict[str, str]]) -> str:
    lines = ["<b>Источники</b>"]
    for index, link in enumerate(links, start=1):
        href = escape(link["href"], quote=True)
        label = escape(link["label"])
        lines.append(f'{index}. <a href="{href}">{label}</a>')
    return "\n".join(lines)


def _source_label_from_href(href: str) -> str:
    parsed = urlparse(href)
    if parsed.netloc:
        return parsed.netloc
    return href


def _looks_like_source_chip_label(value: str) -> bool:
    normalized = " ".join(str(value or "").split())
    return bool(
        re.match(r"^[^\n]{1,60}\s+\+\s+\d+$", normalized) or re.match(r"^\+\s*\d+$", normalized)
    )
