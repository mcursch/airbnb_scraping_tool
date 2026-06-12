"""Tests for the acquire→extract→store pipeline (LIN-38).

All external dependencies (scrapers, Anthropic client) are mocked so these
tests run completely offline with no API keys required.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from airbnb_scraping_tool.db.models import Base, Listing, ListingSnapshot, SearchRun
from airbnb_scraping_tool.db.repo import Repo
from airbnb_scraping_tool.extraction.extractor import ExtractionResult, Extractor
from airbnb_scraping_tool.schemas import ListingExtraction, RawPayload, SearchQuery
from airbnb_scraping_tool.scrapers.base import ScrapeProvider


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeScraper(ScrapeProvider):
    """Returns a fixed list of ``RawPayload`` objects."""

    source = "airbnb"

    def __init__(self, payloads: list[RawPayload]) -> None:
        self._payloads = payloads

    def search(self, query: SearchQuery) -> list[RawPayload]:
        return self._payloads


class FakeExtractor(Extractor):
    """Returns a fixed ``ExtractionResult`` without calling the LLM."""

    def __init__(self, results: list[ExtractionResult]) -> None:
        super().__init__(client=None, model="claude-opus-4-8")
        self._results = list(results)

    def extract(self, source: str, url: str, payload: str) -> ExtractionResult:
        if not self._results:
            raise RuntimeError("FakeExtractor ran out of results")
        return self._results.pop(0)


def _make_listing_extraction(**overrides) -> ListingExtraction:
    defaults = dict(
        source_listing_id="abc123",
        name="Cozy Studio",
        url="https://airbnb.com/rooms/abc123",
        nightly_price=80.0,
        currency="USD",
    )
    defaults.update(overrides)
    return ListingExtraction(**defaults)


def _make_raw_payload(payload: str = "raw content", source: str = "airbnb") -> RawPayload:
    return RawPayload(
        source=source,  # type: ignore[arg-type]
        url="https://airbnb.com/rooms/abc123",
        payload=payload,
        content_hash=_hash(payload),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine_and_session():
    """Isolated in-memory SQLite engine + session, independent of config."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    with Session() as sess:
        yield eng, sess
    eng.dispose()


# ---------------------------------------------------------------------------
# Pipeline integration tests
# ---------------------------------------------------------------------------


def _run_pipeline_with_session(engine, query, scrapers, extractor):
    """Run the pipeline but patch SessionLocal to use an in-memory engine."""
    import airbnb_scraping_tool.db.models as models_mod

    # Monkey-patch the SessionLocal in the models module so pipeline.py picks
    # up our in-memory engine.
    from sqlalchemy.orm import sessionmaker as sm

    orig_sl = models_mod.SessionLocal
    orig_engine = models_mod.engine
    orig_init_db = models_mod.init_db

    TestSession = sm(bind=engine, autoflush=False, autocommit=False)
    models_mod.SessionLocal = TestSession
    models_mod.engine = engine
    models_mod.init_db = lambda: None  # already created

    # Also patch in pipeline.py's imports
    import pipeline as pl_mod

    pl_orig_sl = pl_mod.SessionLocal
    pl_orig_init = pl_mod.init_db
    pl_mod.SessionLocal = TestSession
    pl_mod.init_db = lambda: None

    try:
        from pipeline import Pipeline

        pipeline = Pipeline(scrapers=scrapers, extractor=extractor)
        run_id = pipeline.run(query)
        return run_id
    finally:
        models_mod.SessionLocal = orig_sl
        models_mod.engine = orig_engine
        models_mod.init_db = orig_init_db
        pl_mod.SessionLocal = pl_orig_sl
        pl_mod.init_db = pl_orig_init


class TestPipelineScanProducesClosedRun:
    """Running scan against mocked scrapers/extractor produces a closed SearchRun."""

    def test_finished_at_is_set(self):
        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        raw = _make_raw_payload("unique-content-1")
        extraction = ExtractionResult(
            listing=_make_listing_extraction(),
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            status="ok",
        )

        run_id = _run_pipeline_with_session(
            eng,
            query=SearchQuery(area="Lisbon", sources=["airbnb"]),
            scrapers=[FakeScraper([raw])],
            extractor=FakeExtractor([extraction]),
        )

        with Session() as sess:
            run = sess.get(SearchRun, run_id)
            assert run is not None
            assert run.finished_at is not None, "finished_at should be set after scan"
            assert run.status == "done"

    def test_stats_contain_required_keys(self):
        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        raw = _make_raw_payload("unique-content-2")
        extraction = ExtractionResult(
            listing=_make_listing_extraction(),
            input_tokens=200,
            output_tokens=80,
            cache_read_tokens=10,
            status="ok",
        )

        run_id = _run_pipeline_with_session(
            eng,
            query=SearchQuery(area="Lisbon", sources=["airbnb"]),
            scrapers=[FakeScraper([raw])],
            extractor=FakeExtractor([extraction]),
        )

        with Session() as sess:
            run = sess.get(SearchRun, run_id)
            stats = run.stats

        required_keys = {"total_listings", "new", "updated", "dedup_hits", "total_tokens", "estimated_cost_usd"}
        assert required_keys.issubset(stats.keys()), f"Missing stats keys: {required_keys - stats.keys()}"

    def test_stats_values_are_correct(self):
        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        raw = _make_raw_payload("unique-content-3")
        extraction = ExtractionResult(
            listing=_make_listing_extraction(),
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=20,
            status="ok",
        )

        run_id = _run_pipeline_with_session(
            eng,
            query=SearchQuery(area="Lisbon", sources=["airbnb"]),
            scrapers=[FakeScraper([raw])],
            extractor=FakeExtractor([extraction]),
        )

        with Session() as sess:
            run = sess.get(SearchRun, run_id)
            stats = run.stats

        assert stats["total_listings"] == 1
        assert stats["new"] == 1
        assert stats["updated"] == 0
        assert stats["dedup_hits"] == 0
        assert stats["total_tokens"] == 170  # 100 + 50 + 20

    def test_dedup_hit_increments_counter(self):
        """Same content hash sent twice: second is deduped, extractor called once."""
        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        payload_text = "same-content-dedup"
        raw1 = _make_raw_payload(payload_text)
        raw2 = _make_raw_payload(payload_text)  # identical hash

        extraction = ExtractionResult(
            listing=_make_listing_extraction(),
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            status="ok",
        )

        run_id = _run_pipeline_with_session(
            eng,
            query=SearchQuery(area="Lisbon", sources=["airbnb"]),
            scrapers=[FakeScraper([raw1, raw2])],
            extractor=FakeExtractor([extraction]),  # only one result — dedup means only one call
        )

        with Session() as sess:
            run = sess.get(SearchRun, run_id)
            stats = run.stats

        assert stats["dedup_hits"] == 1
        assert stats["total_listings"] == 1

    def test_new_vs_updated(self):
        """First run: listing is new; second run with changed data: listing is updated."""
        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        # First run — new listing
        raw1 = _make_raw_payload("run1-content")
        ext1 = ExtractionResult(
            listing=_make_listing_extraction(source_listing_id="L1", name="Original Name"),
            input_tokens=50,
            output_tokens=20,
            status="ok",
        )
        run1_id = _run_pipeline_with_session(
            eng,
            query=SearchQuery(area="Lisbon", sources=["airbnb"]),
            scrapers=[FakeScraper([raw1])],
            extractor=FakeExtractor([ext1]),
        )

        # Second run — same listing id but name changed, different hash
        raw2 = _make_raw_payload("run2-different-content")
        ext2 = ExtractionResult(
            listing=_make_listing_extraction(source_listing_id="L1", name="Updated Name"),
            input_tokens=50,
            output_tokens=20,
            status="ok",
        )
        run2_id = _run_pipeline_with_session(
            eng,
            query=SearchQuery(area="Lisbon", sources=["airbnb"]),
            scrapers=[FakeScraper([raw2])],
            extractor=FakeExtractor([ext2]),
        )

        with Session() as sess:
            run1 = sess.get(SearchRun, run1_id)
            run2 = sess.get(SearchRun, run2_id)

        assert run1.stats["new"] == 1
        assert run1.stats["updated"] == 0
        assert run2.stats["new"] == 0
        assert run2.stats["updated"] == 1


# ---------------------------------------------------------------------------
# purge-run tests
# ---------------------------------------------------------------------------


class TestPurgeRun:
    """purge-run removes snapshots and orphaned listings."""

    def _setup_run(self, session, repo) -> tuple[int, int]:
        """Create a run with one listing and one snapshot; return (run_id, listing_id)."""
        run = repo.open_run(session, area_query="Test Area")
        session.commit()

        listing, _, _ = repo.upsert_listing(
            session,
            source="airbnb",
            source_listing_id="test-001",
            name="Test Listing",
            url="https://airbnb.com/rooms/test-001",
        )
        session.flush()

        repo.insert_snapshot(session, listing_id=listing.id, run_id=run.id)
        session.commit()

        return run.id, listing.id

    def test_snapshots_deleted(self, session, repo):
        run_id, listing_id = self._setup_run(session, repo)

        # Verify snapshot exists before purge
        snaps_before = session.scalars(
            select(ListingSnapshot).where(ListingSnapshot.run_id == run_id)
        ).all()
        assert len(snaps_before) == 1

        repo.purge_run(session, run_id)
        session.commit()

        snaps_after = session.scalars(
            select(ListingSnapshot).where(ListingSnapshot.run_id == run_id)
        ).all()
        assert len(snaps_after) == 0

    def test_orphaned_listing_deleted(self, session, repo):
        run_id, listing_id = self._setup_run(session, repo)

        repo.purge_run(session, run_id)
        session.commit()

        orphan = session.get(Listing, listing_id)
        assert orphan is None, "Orphaned listing should be removed"

    def test_non_orphan_listing_preserved(self, session, repo):
        """Listing that appears in another run must NOT be deleted."""
        run1_id, listing_id = self._setup_run(session, repo)

        # Create a second run that references the same listing
        run2 = repo.open_run(session, area_query="Test Area")
        session.commit()
        repo.insert_snapshot(session, listing_id=listing_id, run_id=run2.id)
        session.commit()

        # Purge only run1
        repo.purge_run(session, run1_id)
        session.commit()

        # Listing should still exist (run2's snapshot remains)
        survivor = session.get(Listing, listing_id)
        assert survivor is not None, "Listing referenced by another run must survive"

    def test_purge_counts(self, session, repo):
        run_id, _ = self._setup_run(session, repo)
        counts = repo.purge_run(session, run_id)
        session.commit()
        assert counts["snapshots_deleted"] == 1
        assert counts["listings_deleted"] == 1


# ---------------------------------------------------------------------------
# vacuum CLI test
# ---------------------------------------------------------------------------


class TestVacuumCli:
    def test_vacuum_exits_0_and_prints_message(self, tmp_path):
        """vacuum command exits 0 and prints 'VACUUM complete'."""
        from click.testing import CliRunner
        from cli import vacuum

        # Point config to a temporary database so we don't clobber the real one
        import config as config_mod

        db_file = tmp_path / "test.db"
        orig_db_path = config_mod.settings.db_path
        config_mod.settings.db_path = str(db_file)

        # Create the tables so the DB file exists
        from sqlalchemy import create_engine as ce
        eng = ce(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        eng.dispose()

        try:
            runner = CliRunner()
            result = runner.invoke(vacuum)
        finally:
            config_mod.settings.db_path = orig_db_path

        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
        assert "VACUUM complete" in result.output


# ---------------------------------------------------------------------------
# purge-run CLI test
# ---------------------------------------------------------------------------


class TestPurgeRunCli:
    def test_purge_run_cli(self, tmp_path):
        """purge-run CLI removes snapshots and orphaned listings."""
        from unittest import mock

        from click.testing import CliRunner
        from sqlalchemy import create_engine as ce
        from sqlalchemy.orm import sessionmaker as sm

        db_file = tmp_path / "purge_test.db"
        eng = ce(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        TestSL = sm(bind=eng, autoflush=False, autocommit=False)

        # Seed: one run, one listing, one snapshot
        repo = Repo()
        with TestSL() as sess:
            run = repo.open_run(sess, area_query="CLI Test")
            sess.commit()
            run_id = run.id
            listing, _, _ = repo.upsert_listing(
                sess,
                source="airbnb",
                source_listing_id="cli-001",
                name="CLI Test Listing",
                url="https://airbnb.com/rooms/cli-001",
            )
            sess.flush()
            repo.insert_snapshot(sess, listing_id=listing.id, run_id=run_id)
            sess.commit()
            listing_id = listing.id

        # Patch cli.SessionLocal so the command uses our in-memory DB, and
        # patch init_db to skip re-creating tables (already done above).
        from cli import purge_run

        runner = CliRunner()
        with mock.patch("cli.SessionLocal", TestSL), mock.patch("cli.init_db", lambda: None):
            result = runner.invoke(purge_run, [str(run_id)])

        eng.dispose()

        assert result.exit_code == 0, f"Expected exit 0: {result.output}"

        # Verify rows are gone by re-opening the file directly
        eng2 = ce(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
        with sm(bind=eng2)() as sess2:
            snaps = sess2.scalars(select(ListingSnapshot).where(ListingSnapshot.run_id == run_id)).all()
            listings = sess2.scalars(select(Listing).where(Listing.id == listing_id)).all()
        eng2.dispose()

        assert len(snaps) == 0, "Snapshots should be deleted"
        assert len(listings) == 0, "Orphaned listing should be deleted"
