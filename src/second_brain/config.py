"""Configuration and secret management (cross-cutting).

Secrets come only from the environment / .env — NEVER from the vault.
`manifest` is the fail-closed module switch: a disabled module is absent
everywhere (tools, prompts, sync), not merely hidden.
"""
from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    manifest: dict[str, bool] = {
        "capture": True,
        "journal": True,
        "calendar": False,   # Phase 2
        "proactive": False,  # Phase 3
        "recall": False,     # Phase 4
    }

    @staticmethod
    def secret(key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise RuntimeError(f"Missing secret: {key}")
        return val

    @staticmethod
    def get(key: str, default: str | None = None) -> str | None:
        return os.getenv(key, default)

    @classmethod
    def enabled(cls, module: str) -> bool:
        return cls.manifest.get(module, False)
