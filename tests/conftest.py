"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

from airbnb_scraping_tool.db.models import Base as AirbnbBase
from airbnb_scraping_tool.db.repo import Repo
import db.models as root_db_models
from db.repo import create_all, get_engine

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def in_memory_engine():
    """SQLite in-memory engine with all tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    AirbnbBase.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(in_memory_engine):
    """A single SQLAlchemy session bound to the in-memory engine."""
    Session = sessionmaker(bind=in_memory_engine, autoflush=False, autocommit=False)
    with Session() as s:
        yield s


@pytest.fixture()
def repo():
    return Repo()


# ---------------------------------------------------------------------------
# Root-level db fixtures (used by test_db.py, test_airbnb_scraper.py, etc.)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine with all root-level db tables created.

    Foreign-key enforcement and WAL mode are enabled so the engine behaves
    like a production SQLite database.
    """
    engine = get_engine("sqlite:///:memory:")
    create_all(engine)

    # Verify every expected table was created.
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


@pytest.fixture()
def in_memory_session():
    """SQLAlchemy session backed by an in-memory SQLite database.

    Uses the root-level ``db.models.Base`` so all five tables are available.
    Foreign keys are *not* enforced here to allow RawScrape rows without a
    run_id (useful for unit tests that focus on persistence logic).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    root_db_models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


@pytest.fixture()
def page1_payload() -> str:
    return (FIXTURES_DIR / "airbnb_page1.json").read_text()


@pytest.fixture()
def page2_payload() -> str:
    return (FIXTURES_DIR / "airbnb_page2.json").read_text()
