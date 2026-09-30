"""Fetches a URL, extracts its text and summarizes it (LLM).

Produces a `CaptureResult` that the bookmark flow (#9) hands to NoteWriter.
This tool never writes to the vault itself. A failed fetch is not an error:
the result still carries the raw URL plus a note on what went wrong, so the
link can be saved as-is.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..llm_client import LLMError
from ..prompts import LINK_SUMMARY_SYSTEM_PROMPT
from .base import Tool

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 10.0
MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 8000
SUMMARY_MAX_TOKENS = 300
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
    fetched: bool = False
    error: str | None = None


def _validate_url(url: str) -> str:
    """Return the stripped URL if it is http(s) with a host, else raise ValueError."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"not a valid http(s) URL: {url!r}")
    return url


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
        "Fetch a web page from a URL and summarize it. Use when the user shares a link "
        "to save or read later."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The http(s) URL the user shared"},
        },
        "required": ["url"],
    }

    def __init__(self, llm, http_client: httpx.Client | None = None) -> None:
        self.llm = llm
        self.http = http_client or httpx.Client(
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def run(self, args: dict) -> str:
        """Capture the link and return a one-line receipt for the orchestrator."""
        url = args.get("url")
        if not isinstance(url, str):
            raise ValueError("'url' must be a string")
        result = self.capture(url)
        if not result.fetched:
            return f"Could not fetch {result.url} ({result.error}); keep the raw link."
        if not result.summary:
            return f"Fetched '{result.title}' but no summary is available."
        return f"{result.title}: {result.summary}"

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

        summary = self._summarize(title, page.text) if page.text else ""
        return CaptureResult(
            url=url, title=title, summary=summary or page.description, fetched=True
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

    def _summarize(self, title: str, text: str) -> str:
        """Ask the LLM for a one-paragraph summary; return "" if it fails."""
        # The page must not be able to close the <page> wrapper and escape it.
        title, text = (s.replace("</page>", "") for s in (title, text))
        prompt = f"<page>\nTitle: {title}\n\n{text}\n</page>"
        try:
            result = self.llm.complete(
                prompt, system=LINK_SUMMARY_SYSTEM_PROMPT, max_tokens=SUMMARY_MAX_TOKENS
            )
        except LLMError as err:
            logger.warning("Summary failed: %s", err)
            return ""
        return result.text.strip()
