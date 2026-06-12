"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from airbnb_scraping_tool.db.models import Base
from airbnb_scraping_tool.db.repo import Repo


@pytest.fixture()
def in_memory_engine():
    """SQLite in-memory engine with all tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
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
