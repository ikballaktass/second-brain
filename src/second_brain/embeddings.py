"""Local multilingual sentence embeddings (cross-cutting).

Runs on the CPU with sentence-transformers, so note text never leaves the machine
and no API key is needed. The default model handles Turkish and 50+ other languages.
The model is downloaded once (~470 MB, to HF_HOME) and loaded lazily on first use,
so importing this module or starting the bot with recall off costs nothing.
"""
from __future__ import annotations

import threading
from typing import Literal

from .config import Config

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
BATCH_SIZE = 32

Kind = Literal["document", "query"]


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device
        self._model = None
        self._lock = threading.Lock()  # one load, and one encode at a time

    @classmethod
    def from_config(cls) -> Embedder:
        return cls(Config.get("EMBEDDING_MODEL", DEFAULT_MODEL))

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def _prefix(self, kind: Kind) -> str:
        # E5-family models are trained with these prefixes; symmetric models need none.
        if "e5" in self.model_name.lower():
            return "query: " if kind == "query" else "passage: "
        return ""

    def embed(self, texts: list[str], kind: Kind = "document") -> list[list[float]]:
        """Unit-length vectors (cosine-ready), one per text, in input order."""
        if not texts:
            return []
        prefix = self._prefix(kind)
        with self._lock:
            vectors = self._load().encode(
                [prefix + t for t in texts],
                batch_size=BATCH_SIZE,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return vectors.tolist()
