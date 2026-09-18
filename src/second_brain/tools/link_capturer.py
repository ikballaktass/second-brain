"""Fetches a URL, summarizes it (LLM), hands off to NoteWriter as a bookmark."""
from __future__ import annotations
from .base import Tool


class LinkCapturer(Tool):
    name = "link_capturer"

    def __init__(self, llm=None) -> None:
        self.llm = llm

    def run(self, args: dict):
        raise NotImplementedError
