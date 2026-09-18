"""Smoke test — the package imports and wires without a live network."""
import second_brain


def test_version():
    assert second_brain.__version__ == "0.1.0"
