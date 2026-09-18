"""Tasks with priority."""
from __future__ import annotations
from .base import Tool


class TaskManager(Tool):
    name = "task_manager"

    def __init__(self, vault=None) -> None:
        self.vault = vault

    def run(self, args: dict):
        raise NotImplementedError
