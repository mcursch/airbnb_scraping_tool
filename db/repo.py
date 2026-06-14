"""Repository layer — all write-path helpers for the Short-Stay Market Scanner.

Each function accepts and returns SQLAlchemy model instances defined in
``db/models.py``.  Callers are responsible for session lifecycle (begin /
commit / rollback).  Functions call ``session.flush()`` so that generated
primary keys are available on return without requiring the caller to commit
immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from db.models import Base, ExtractionLog, Listing, ListingSnapshot, RawScrape, SearchRun


# ---------------------------------------------------------------------------
# Engine / table-creation helpers
# ---------------------------------------------------------------------------


def get_engine(database_url: str | None = None) -> Engine:
    """Return a SQLAlchemy engine.

    If *database_url* is not provided the application config (``settings.db_url``)
    is used.  For SQLite databases the engine is configured with WAL mode and
    foreign-key enforcement so tests behave like production.
    """
    url = database_url if database_url is not None else settings.db_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    engine = create_engine(url, connect_args=connect_args, echo=False)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def create_all(engine: Engine | None = None) -> None:
    """Create all ORM-declared tables in *engine* (no-op if they already exist)."""
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)

# ---------------------------------------------------------------------------
# Fields that may change between scrapes and should be refreshed on upsert.
# ``first_seen_at`` is intentionally excluded — it is set once and preserved.
# ---------------------------------------------------------------------------
_MUTABLE_LISTING_FIELDS: tuple[str, ...] = (
    "name",
    "property_type",
    "lat",
    "lon",
    "address_text",
    "bedrooms",
    "beds",
    "baths",
    "max_guests",
    "rating",
    "review_count",
    "amenities",
    "images",
    "url",
    "host_or_brand",
)


# ---------------------------------------------------------------------------
# SearchRun helpers
# ---------------------------------------------------------------------------


def create_search_run(
    session: Session,
    area_query: str,
    *,
    checkin: str | None = None,
    checkout: str | None = None,
    guests: int | None = None,
    sources: list[str] | None = None,
) -> SearchRun:
    """Insert a new :class:`SearchRun` and return it (flushed, not committed).

    ``started_at`` is set to the current UTC time; ``status`` is ``"running"``.
    """
    run = SearchRun(
        area_query=area_query,
        checkin=checkin,
        checkout=checkout,
        guests=guests,
        sources=sources,
        started_at=datetime.utcnow(),
        status="running",
    )
    session.add(run)
    session.flush()
    return run


def close_search_run(
    session: Session,
    run: SearchRun,
    stats: dict[str, Any] | None = None,
) -> SearchRun:
    """Convenience wrapper: finalise *run* with optional *stats*.

    Delegates to :func:`record_run_stats`.
    """
    return record_run_stats(session, run, stats=stats or {})


def record_run_stats(
    session: Session,
    run: SearchRun,
    stats: dict[str, Any],
    finished_at: datetime | None = None,
) -> SearchRun:
    """Update ``SearchRun.stats`` and ``SearchRun.finished_at``.

    Sets ``status`` to ``"completed"`` and stamps ``finished_at`` with the
    current UTC time unless *finished_at* is provided explicitly.

    Returns the mutated *run* instance (flushed, not committed).
    """
    run.stats = stats
    run.finished_at = finished_at if finished_at is not None else datetime.utcnow()
    run.status = "completed"
    session.flush()
    return run


# ---------------------------------------------------------------------------
# Listing helpers
# ---------------------------------------------------------------------------


def upsert_listing(session: Session, listing: "Listing | dict") -> "Listing":
    """Insert or update a :class:`Listing` keyed on ``(source, source_listing_id)``.

    *listing* may be either a :class:`Listing` ORM instance **or** a plain
    ``dict`` whose keys match :class:`Listing` column names (including the
    mandatory ``"source"`` and ``"source_listing_id"`` keys).

    * **Insert path** — the row does not yet exist: ``first_seen_at`` and
      ``last_seen_at`` are set from the supplied values, or to the current UTC
      time when absent.
    * **Update path** — a row already exists: all :data:`_MUTABLE_LISTING_FIELDS`
      are copied from *listing* onto the existing row, and ``last_seen_at`` is
      refreshed to the current UTC time.  ``first_seen_at`` is *not* changed.

    Returns the persisted :class:`Listing` instance (flushed, not committed).
    """
    if isinstance(listing, dict):
        d = dict(listing)
        src = d.pop("source")
        src_id = d.pop("source_listing_id")
        first_seen = d.pop("first_seen_at", None)
        last_seen = d.pop("last_seen_at", None)
        obj = Listing(source=src, source_listing_id=src_id, **d)
        if first_seen is not None:
            obj.first_seen_at = first_seen
        if last_seen is not None:
            obj.last_seen_at = last_seen
        listing = obj

    now = datetime.utcnow()

    existing: Listing | None = (
        session.query(Listing)
        .filter_by(source=listing.source, source_listing_id=listing.source_listing_id)
        .first()
    )

    if existing is None:
        if not listing.first_seen_at:
            listing.first_seen_at = now
        if not listing.last_seen_at:
            listing.last_seen_at = now
        session.add(listing)
        session.flush()
        return listing

    # Update path: refresh mutable fields and bump last_seen_at.
    for field in _MUTABLE_LISTING_FIELDS:
        incoming = getattr(listing, field, None)
        if incoming is not None:
            setattr(existing, field, incoming)
    existing.last_seen_at = now
    session.flush()
    return existing


# ---------------------------------------------------------------------------
# ListingSnapshot helpers
# ---------------------------------------------------------------------------


def create_listing_snapshot(
    session: Session,
    snapshot: ListingSnapshot,
) -> ListingSnapshot:
    """Insert a :class:`ListingSnapshot` row unconditionally.

    A snapshot is created on every search run regardless of whether the parent
    listing was new or already existed.  ``captured_at`` defaults to the
    current UTC time when not already set on *snapshot*.

    Returns the inserted instance (flushed, not committed).
    """
    if snapshot.captured_at is None:
        snapshot.captured_at = datetime.utcnow()
    session.add(snapshot)
    session.flush()
    return snapshot


# ---------------------------------------------------------------------------
# RawScrape helpers
# ---------------------------------------------------------------------------


def create_raw_scrape(
    session: Session,
    source: str,
    url: str,
    payload: str,
    *,
    run_id: int | None = None,
    status: str = "pending",
    page_number: int | None = None,
) -> RawScrape | None:
    """Persist a raw captured payload and return the new :class:`RawScrape` row.

    Returns ``None`` if a row with the same content hash already exists
    (idempotent duplicate skip).
    """
    content_hash = RawScrape.compute_hash(payload)
    # Pre-check avoids a partial rollback that would discard other pending work.
    existing = session.query(RawScrape).filter_by(content_hash=content_hash).first()
    if existing is not None:
        return None
    row = RawScrape(
        run_id=run_id,
        source=source,
        url=url,
        payload=payload,
        content_hash=content_hash,
        status=status,
        page_number=page_number,
    )
    session.add(row)
    session.flush()
    return row


def get_raw_scrapes(
    session: Session,
    *,
    run_id: int | None = None,
    source: str | None = None,
    status: str | None = None,
) -> list[RawScrape]:
    """Return :class:`RawScrape` rows, optionally filtered, ordered by id."""
    q = session.query(RawScrape)
    if run_id is not None:
        q = q.filter(RawScrape.run_id == run_id)
    if source is not None:
        q = q.filter(RawScrape.source == source)
    if status is not None:
        q = q.filter(RawScrape.status == status)
    return q.order_by(RawScrape.id).all()


# ---------------------------------------------------------------------------
# Run-history cost rollup
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Listing read helpers (used by the dashboard)
# ---------------------------------------------------------------------------


def get_listing(session: Session, listing_id: int) -> "Listing | None":
    """Return the :class:`Listing` with *listing_id*, or ``None``."""
    return session.get(Listing, listing_id)


def get_latest_snapshot(session: Session, listing_id: int) -> "ListingSnapshot | None":
    """Return the most-recently-captured snapshot for *listing_id*, or ``None``."""
    return (
        session.query(ListingSnapshot)
        .filter(ListingSnapshot.listing_id == listing_id)
        .order_by(ListingSnapshot.captured_at.desc())
        .first()
    )


def get_listing_with_latest_snapshot(
    session: Session, listing_id: int
) -> "tuple[Listing, ListingSnapshot | None] | None":
    """Return ``(listing, latest_snapshot)`` for *listing_id*, or ``None``.

    Returns ``None`` when the listing does not exist.  Returns
    ``(listing, None)`` when the listing exists but has no snapshots.
    """
    listing = get_listing(session, listing_id)
    if listing is None:
        return None
    snapshot = get_latest_snapshot(session, listing_id)
    return (listing, snapshot)


def insert_snapshot(
    session: Session,
    listing_id: int,
    run_id: "int | None",
    snapshot_data: dict,
) -> "ListingSnapshot":
    """Insert a new :class:`ListingSnapshot` from a data dict and return it.

    ``snapshot_data`` keys map to :class:`ListingSnapshot` columns
    (``nightly_price``, ``currency``, ``total_price``, ``fees``,
    ``availability``, ``captured_at``).  Missing keys default to ``None``
    except ``captured_at`` which defaults to the current UTC time.
    """
    snap = ListingSnapshot(
        listing_id=listing_id,
        run_id=run_id,
        nightly_price=snapshot_data.get("nightly_price"),
        currency=snapshot_data.get("currency"),
        total_price=snapshot_data.get("total_price"),
        fees=snapshot_data.get("fees"),
        availability=snapshot_data.get("availability"),
        captured_at=snapshot_data.get("captured_at") or datetime.utcnow(),
    )
    session.add(snap)
    session.flush()
    return snap


def get_listings_for_run(run_id: int, engine: Engine) -> Any:  # returns pd.DataFrame
    """Return a :class:`pandas.DataFrame` of all listings for *run_id*.

    Each row combines columns from :class:`Listing` (identity and metadata)
    and the corresponding :class:`ListingSnapshot` (price, currency).
    Returns an **empty** DataFrame when the run has no snapshots.
    """
    import pandas as pd

    Session = sessionmaker(bind=engine)
    with Session() as session:
        pairs = (
            session.query(Listing, ListingSnapshot)
            .join(ListingSnapshot, ListingSnapshot.listing_id == Listing.id)
            .filter(ListingSnapshot.run_id == run_id)
            .all()
        )

    if not pairs:
        return pd.DataFrame()

    rows = []
    for listing, snapshot in pairs:
        rows.append(
            {
                "id": listing.id,
                "name": listing.name,
                "source": listing.source,
                "nightly_price": snapshot.nightly_price,
                "rating": listing.rating,
                "property_type": listing.property_type,
                "url": listing.url,
                "currency": snapshot.currency,
                "review_count": listing.review_count,
                "bedrooms": listing.bedrooms,
                "beds": listing.beds,
                "host_or_brand": listing.host_or_brand,
                "address_text": listing.address_text,
                "lat": listing.lat,
                "lon": listing.lon,
            }
        )
    return pd.DataFrame(rows)


def list_search_runs(limit: int = 50, engine: Engine | None = None) -> list[dict[str, Any]]:
    """Return recent :class:`SearchRun` rows as plain dicts, newest first.

    Each dict contains all scalar fields on :class:`SearchRun` so callers do
    not need to access the ORM model directly.

    Parameters
    ----------
    limit:
        Maximum number of rows to return (default 50).
    engine:
        SQLAlchemy engine to use.  Defaults to the engine from
        :func:`get_engine` (i.e. the application-configured database).
        Pass an explicit engine in tests to use an in-memory database.
    """
    if engine is None:
        engine = get_engine()
    Session = sessionmaker(bind=engine)
    with Session() as session:
        runs = (
            session.query(SearchRun)
            .order_by(SearchRun.started_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "area_query": r.area_query,
                "checkin": r.checkin,
                "checkout": r.checkout,
                "guests": r.guests,
                "sources": r.sources,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "status": r.status,
                "stats": r.stats or {},
            }
            for r in runs
        ]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Repo:
    """Object-oriented data-access wrapper used by the pipeline and CLI.

    Complements the module-level functions above; both operate on the same
    ``db.models`` ORM classes.  Every method takes a caller-supplied
    ``Session`` and flushes (but does not commit) so PKs are available on
    return while the caller controls transaction boundaries.

    JSON columns (``amenities``, ``images``, ``fees``, ``sources``, ``stats``)
    are stored as native Python objects — SQLAlchemy's ``JSON`` type handles
    serialisation, so callers pass lists/dicts directly (no ``json.dumps``).
    """

    # ------------------------------------------------------------------ SearchRun
    def open_run(self, session: Session, area_query: str, **kwargs: Any) -> SearchRun:
        """Create and persist a new SearchRun in 'running' status."""
        run = SearchRun(
            area_query=area_query,
            checkin=kwargs.get("checkin"),
            checkout=kwargs.get("checkout"),
            guests=kwargs.get("guests", 1),
            sources=kwargs.get("sources", ["airbnb", "booking"]),
            status="running",
        )
        session.add(run)
        session.flush()
        return run

    def close_run(self, session: Session, run_id: int, status: str = "done") -> None:
        """Set ``finished_at`` and ``status`` on a run."""
        run = session.get(SearchRun, run_id)
        if run is None:
            raise ValueError(f"SearchRun {run_id} not found")
        run.finished_at = _utcnow()
        run.status = status
        session.flush()

    def record_run_stats(self, session: Session, run_id: int, stats: dict[str, Any]) -> None:
        """Persist aggregated stats to ``SearchRun.stats`` (see pipeline for keys)."""
        run = session.get(SearchRun, run_id)
        if run is None:
            raise ValueError(f"SearchRun {run_id} not found")
        run.stats = stats
        session.flush()

    def get_run(self, session: Session, run_id: int) -> SearchRun | None:
        return session.get(SearchRun, run_id)

    # ------------------------------------------------------------------ RawScrape
    def find_by_hash(self, session: Session, content_hash: str) -> RawScrape | None:
        stmt = select(RawScrape).where(RawScrape.content_hash == content_hash).limit(1)
        return session.scalars(stmt).first()

    def mark_scrape_status(
        self, session: Session, raw_scrape_id: int, status: str, error: str | None = None
    ) -> None:
        rs = session.get(RawScrape, raw_scrape_id)
        if rs:
            rs.status = status
            rs.error = error
            session.flush()

    # ------------------------------------------------------------------ Listing
    def upsert_listing(
        self,
        session: Session,
        source: str,
        source_listing_id: str,
        **fields: Any,
    ) -> tuple[Listing, bool, bool]:
        """Insert or update a listing keyed on (source, source_listing_id).

        Returns ``(listing, is_new, was_updated)``.
        """
        stmt = select(Listing).where(
            Listing.source == source,
            Listing.source_listing_id == source_listing_id,
        )
        existing = session.scalars(stmt).first()

        if existing is None:
            listing = Listing(source=source, source_listing_id=source_listing_id, **fields)
            session.add(listing)
            session.flush()
            return listing, True, False

        was_updated = False
        for key, value in fields.items():
            if value is not None and hasattr(existing, key) and getattr(existing, key) != value:
                setattr(existing, key, value)
                was_updated = True
        if was_updated:
            existing.last_seen_at = _utcnow()
            session.flush()
        return existing, False, was_updated

    # ------------------------------------------------------------------ Snapshot
    def insert_snapshot(
        self,
        session: Session,
        listing_id: int,
        run_id: int,
        nightly_price: float | None = None,
        currency: str | None = None,
        total_price: float | None = None,
        fees: dict[str, Any] | None = None,
        availability: bool | str | None = None,
        cleaning_fee: float | None = None,
        service_fee: float | None = None,
        taxes: float | None = None,
        deposit: float | None = None,
        weekly_discount_pct: float | None = None,
        monthly_discount_pct: float | None = None,
        minimum_nights: int | None = None,
    ) -> ListingSnapshot:
        snap = ListingSnapshot(
            listing_id=listing_id,
            run_id=run_id,
            nightly_price=nightly_price,
            currency=currency,
            total_price=total_price,
            fees=fees,
            availability=availability,
            cleaning_fee=cleaning_fee,
            service_fee=service_fee,
            taxes=taxes,
            deposit=deposit,
            weekly_discount_pct=weekly_discount_pct,
            monthly_discount_pct=monthly_discount_pct,
            minimum_nights=minimum_nights,
        )
        session.add(snap)
        session.flush()
        return snap

    # ------------------------------------------------------------------ ExtractionLog
    def log_extraction(
        self,
        session: Session,
        raw_scrape_id: int,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        status: str = "ok",
        error: str | None = None,
        listing_id: int | None = None,
    ) -> ExtractionLog:
        """Record token usage / outcome for one extraction call.

        Feeds the run-history cost rollup (:func:`get_all_runs_with_cost`).
        """
        log = ExtractionLog(
            raw_scrape_id=raw_scrape_id,
            listing_id=listing_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            status=status,
            error=error,
        )
        session.add(log)
        session.flush()
        return log

    # ------------------------------------------------------------------ Purge
    def purge_run(self, session: Session, run_id: int) -> dict[str, int]:
        """Delete all snapshots for *run_id*, then remove orphaned listings.

        A listing is orphaned when it has no remaining snapshots after the
        purge.  Returns ``{"snapshots_deleted": N, "listings_deleted": M}``.
        """
        listing_ids = list(
            session.scalars(
                select(ListingSnapshot.listing_id).where(ListingSnapshot.run_id == run_id)
            ).all()
        )

        snaps_deleted = session.execute(
            delete(ListingSnapshot).where(ListingSnapshot.run_id == run_id)
        ).rowcount

        orphan_ids = [
            lid
            for lid in listing_ids
            if session.scalars(
                select(ListingSnapshot.id).where(ListingSnapshot.listing_id == lid).limit(1)
            ).first()
            is None
        ]

        listings_deleted = 0
        if orphan_ids:
            listings_deleted = session.execute(
                delete(Listing).where(Listing.id.in_(orphan_ids))
            ).rowcount

        session.flush()
        return {"snapshots_deleted": snaps_deleted, "listings_deleted": listings_deleted}


def get_all_runs_with_cost(session: Session) -> list[dict[str, Any]]:
    """Return all :class:`SearchRun` rows ordered by ``started_at`` descending.

    Each dict is enriched with aggregated token counts from
    :class:`ExtractionLog` rows and an estimated cost using the claude-opus-4-8
    prices from ``config.settings``.

    Runs with no extraction logs produce ``estimated_cost_usd = 0.0``.
    """
    input_price = settings.CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK
    output_price = settings.CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK
    cache_read_price = settings.CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK

    rows = (
        session.query(
            SearchRun,
            func.coalesce(func.sum(ExtractionLog.input_tokens), 0).label("total_input"),
            func.coalesce(func.sum(ExtractionLog.output_tokens), 0).label("total_output"),
            func.coalesce(func.sum(ExtractionLog.cache_read_tokens), 0).label("total_cache_read"),
        )
        .outerjoin(RawScrape, RawScrape.run_id == SearchRun.id)
        .outerjoin(ExtractionLog, ExtractionLog.raw_scrape_id == RawScrape.id)
        .group_by(SearchRun.id)
        .order_by(SearchRun.started_at.desc())
        .all()
    )

    results: list[dict[str, Any]] = []
    for run, total_input, total_output, total_cache_read in rows:
        estimated_cost = (
            total_input * input_price
            + total_output * output_price
            + total_cache_read * cache_read_price
        ) / 1_000_000

        listing_count: int = (run.stats or {}).get("listing_count", 0)

        results.append(
            {
                "id": run.id,
                "area_query": run.area_query,
                "started_at": run.started_at,
                "status": run.status,
                "listing_count": listing_count,
                "estimated_cost_usd": estimated_cost,
                "total_input_tokens": int(total_input),
                "total_output_tokens": int(total_output),
                "total_cache_read_tokens": int(total_cache_read),
            }
        )

    return results
