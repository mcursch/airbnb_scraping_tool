"""Database engine factory and table creation helpers."""

from sqlalchemy import event
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from db.models import Base


def get_engine(database_url: str | None = None) -> Engine:
    """Return a SQLAlchemy engine for *database_url*.

    If *database_url* is not provided, it is read from the application config
    (which reads DATABASE_URL from the environment / .env file).

    For SQLite databases, foreign-key constraints and WAL journal mode are
    enabled on every new connection via an event listener.
    """
    if database_url is None:
        from config import settings

        database_url = settings.database_url

    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url, connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")

        return engine

    return create_engine(database_url)


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
