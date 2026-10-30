"""Shared test isolation: no ntfy pushes, no alert state in the real live/state, and module
globals that run.main() may flip (--dry-run, dry runs turning alerts off) restored after each test."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from live import db  # noqa: E402
from live import config as C  # noqa: E402


@pytest.fixture(autouse=True)
def _round3_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "")
    monkeypatch.setattr(C, "ALERTS_PUSH", False)
    monkeypatch.setattr(C, "ALERTS_ENABLED", C.ALERTS_ENABLED)
    monkeypatch.setattr(db, "_disabled", False)
    monkeypatch.setattr(C, "STATE_DIR", tmp_path / "state")
    yield
