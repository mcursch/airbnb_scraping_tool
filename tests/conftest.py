"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from db.models import Base, make_session_factory

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def in_memory_session():
    """SQLAlchemy session backed by an in-memory SQLite database."""
    factory = make_session_factory("sqlite:///:memory:")
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def page1_payload() -> str:
    return (FIXTURES_DIR / "airbnb_page1.json").read_text()


@pytest.fixture()
def page2_payload() -> str:
    return (FIXTURES_DIR / "airbnb_page2.json").read_text()
