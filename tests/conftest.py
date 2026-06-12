"""Shared pytest fixtures."""

import pytest
from sqlalchemy import inspect

from db.repo import create_all, get_engine


@pytest.fixture
def db_engine():
    """Create an in-memory SQLite engine with all tables and verify they exist."""
    engine = get_engine("sqlite:///:memory:")
    create_all(engine)

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    expected_tables = {
        "search_runs",
        "raw_scrapes",
        "listings",
        "listing_snapshots",
        "extraction_logs",
    }
    missing = expected_tables - existing_tables
    assert not missing, f"Missing tables after create_all: {missing}"

    yield engine

    engine.dispose()
