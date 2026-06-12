"""Database engine factory and table creation helpers."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from db.models import Base


def get_engine(database_url: str | None = None) -> Engine:
    """Return a SQLAlchemy engine for *database_url*.

    If *database_url* is not provided, it is read from the application config
    (which reads DATABASE_URL from the environment / .env file).
    """
    if database_url is None:
        from config import settings

        database_url = settings.database_url

    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        # Enable WAL mode and enforce foreign-key constraints for SQLite
        connect_args = {"check_same_thread": False}

    return create_engine(database_url, connect_args=connect_args)


def create_all(engine: Engine | None = None) -> None:
    """Create all tables defined in the ORM metadata if they do not yet exist."""
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)


def init_db(database_url: str | None = None) -> Engine:
    """Convenience entry-point: create engine, create all tables, return engine.

    Example
    -------
    >>> from db.repo import init_db
    >>> init_db()          # creates scanner.sqlite with all five tables
    """
    engine = get_engine(database_url)
    create_all(engine)
    return engine
