"""Fetches a URL, summarizes + tags it (LLM) and saves it as a bookmark.

`capture()` only fetches and analyzes; `run()` then hands a `Bookmark` to
NoteWriter, the only vault writer. A failed fetch is not an error: the raw
link is saved with the user's note plus a line saying what went wrong.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..llm_client import LLMError
from ..models import Bookmark
from ..prompts import LINK_SUMMARY_SYSTEM_PROMPT
from .base import Tool

if TYPE_CHECKING:
    from .note_writer import NoteWriter

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 10.0
MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 8000
SUMMARY_MAX_TOKENS = 400
MAX_TAGS = 5
USER_AGENT = "Mozilla/5.0 (compatible; SecondBrainBot/0.1; personal bookmark fetcher)"
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_NOISE_TAGS = [
    "script", "style", "noscript", "template", "svg", "iframe",
    "nav", "header", "footer", "aside", "form",
]


class FetchError(Exception):
    """The page could not be fetched or is not something we can read."""


@dataclass
class CaptureResult:
    url: str
    title: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    fetched: bool = False
    error: str | None = None


def _validate_url(url: str) -> str:
    """Return the stripped URL if it is http(s) with a host, else raise ValueError."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"not a valid http(s) URL: {url!r}")
    return url


def _normalize_tags(raw) -> list[str]:
    """Clean LLM tags into Obsidian-safe ones: lowercase, hyphenated, unique, max 5."""
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = re.sub(r"\s+", "-", item.strip().lstrip("#").lower())
        tag = re.sub(r"[^\w\-/]", "", tag).strip("-/")
        # Obsidian ignores purely numeric tags.
        if tag and not tag.isdigit() and tag not in tags:
            tags.append(tag)
        if len(tags) == MAX_TAGS:
            break
    return tags


def _parse_analysis(text: str) -> tuple[str, list[str]]:
    """Read {"summary", "tags"} from the LLM reply; fall back to the raw text as summary."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            summary = data.get("summary")
            summary = _collapse(summary) if isinstance(summary, str) else ""
            return summary, _normalize_tags(data.get("tags"))
    return text, []


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _meta(soup: BeautifulSoup, attr: str, value: str) -> str:
    tag = soup.find("meta", attrs={attr: value})
    content = tag.get("content") if tag else None
    return _collapse(content) if isinstance(content, str) else ""


@dataclass
class _Page:
    title: str
    description: str
    text: str


def _extract(html: str) -> _Page:
    """Pull title, meta description and readable main text out of an HTML page."""
    soup = BeautifulSoup(html, "html.parser")

    title = _meta(soup, "property", "og:title")
    if not title and soup.title and soup.title.string:
        title = _collapse(soup.title.string)

    description = _meta(soup, "property", "og:description") or _meta(
        soup, "name", "description"
    )

    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = _collapse(root.get_text(" "))[:MAX_TEXT_CHARS]

    return _Page(title=title, description=description, text=text)


class LinkCapturer(Tool):
    name = "link_capturer"
    description = (
        "Save a link as a bookmark: fetch the page, summarize and tag it, and store it in "
        "the user's vault. Use whenever the user shares a URL."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The http(s) URL the user shared"},
            "note": {
                "type": "string",
                "description": "The user's own words sent with the link, kept verbatim",
            },
            "source": {
                "type": "string",
                "description": "Where the link came from if the user said so, e.g. instagram",
            },
        },
        "required": ["url"],
    }

    def __init__(
        self, llm, note_writer: NoteWriter, http_client: httpx.Client | None = None
    ) -> None:
        self.llm = llm
        self.note_writer = note_writer
        self.http = http_client or httpx.Client(
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def run(self, args: dict) -> str:
        """Capture the link, save it as a bookmark and return a one-line receipt."""
        url = args.get("url")
        if not isinstance(url, str):
            raise ValueError("'url' must be a string")
        note = args.get("note") if isinstance(args.get("note"), str) else ""
        source = args.get("source") if isinstance(args.get("source"), str) else ""

        result = self.capture(url)

        body = [note.strip()] if note.strip() else []
        if not result.fetched:
            body.append(f"> Sayfa alınamadı: {result.error}")
        bookmark = Bookmark(
            title=result.title,
            content="\n\n".join(body),
            tags=result.tags,
            url=result.url,
            summary=result.summary,
            source=source.strip() or None,
        )
        path = self.note_writer.save_bookmark(bookmark)

        if not result.fetched:
            return f"Saved raw link → {path} · could not fetch page ({result.error})"
        receipt = f"Saved bookmark → {path}"
        if result.tags:
            receipt += " · tags: " + ", ".join(result.tags)
        if not result.summary:
            receipt += " · no summary available"
        return receipt

    def capture(self, url: str) -> CaptureResult:
        """Fetch + summarize. Only an invalid URL raises; everything else falls back."""
        url = _validate_url(url)
        try:
            html, content_type = self._fetch(url)
        except FetchError as err:
            logger.info("Fetch failed for %s: %s", url, err)
            return CaptureResult(url=url, title=url, fetched=False, error=str(err))

        if content_type == "text/plain":
            page = _Page(title="", description="", text=_collapse(html)[:MAX_TEXT_CHARS])
        else:
            page = _extract(html)
        title = page.title or url

        summary, tags = self._analyze(title, page.text) if page.text else ("", [])
        return CaptureResult(
            url=url, title=title, summary=summary or page.description, tags=tags, fetched=True
        )

    def _fetch(self, url: str) -> tuple[str, str]:
        """GET the page; return (text, media type). Raise FetchError on any problem."""
        try:
            with self.http.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise FetchError(f"HTTP {response.status_code}")
                media_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if media_type not in ALLOWED_CONTENT_TYPES:
                    raise FetchError(f"unsupported content type: {media_type or 'unknown'}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_BYTES:
                        break
                encoding = response.encoding or "utf-8"
        except httpx.TimeoutException as err:
            raise FetchError("timed out") from err
        except httpx.HTTPError as err:
            raise FetchError(f"network error: {type(err).__name__}") from err
        return bytes(body[:MAX_BYTES]).decode(encoding, errors="replace"), media_type

    def _analyze(self, title: str, text: str) -> tuple[str, list[str]]:
        """Ask the LLM for (summary, tags) in one call; ("", []) if it fails."""
        # The page must not be able to close the <page> wrapper and escape it.
        title, text = (s.replace("</page>", "") for s in (title, text))
        prompt = f"<page>\nTitle: {title}\n\n{text}\n</page>"
        try:
            result = self.llm.complete(
                prompt, system=LINK_SUMMARY_SYSTEM_PROMPT, max_tokens=SUMMARY_MAX_TOKENS
            )
        except LLMError as err:
            logger.warning("Summary failed: %s", err)
            return "", []
        return _parse_analysis(result.text)
