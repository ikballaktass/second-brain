"""Shared test defaults.

recall is on in the real config, but most tests fake the LLM without an embedder and
must not load the real model or write .index/ into the repo. So tests run with recall
off unless they switch it on themselves, and any index goes under tmp_path.

calendar is on in the real config too, but building it loads the real Google token
from secrets/. Tests run with calendar off unless they switch it on with a fake.
"""
import pytest

from second_brain.config import Config

DEFAULT_MANIFEST = dict(Config.manifest)  # captured before any test patches it


@pytest.fixture(autouse=True)
def _test_defaults(monkeypatch, tmp_path):
    monkeypatch.setitem(Config.manifest, "recall", False)
    monkeypatch.setitem(Config.manifest, "calendar", False)
    monkeypatch.setenv("INDEX_PATH", str(tmp_path / "index"))
