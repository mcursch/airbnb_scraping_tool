"""Tests for LIN-39: structured logging, retry/timeout, SIGINT cancellation, and flags.

All external dependencies are mocked so these tests run completely offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from airbnb_scraping_tool.db.models import Base, ExtractionLog, ListingSnapshot, RawScrape, SearchRun
from airbnb_scraping_tool.db.repo import Repo
from airbnb_scraping_tool.extraction.extractor import ExtractionResult, Extractor
from airbnb_scraping_tool.schemas import ListingExtraction, RawPayload, SearchQuery
from airbnb_scraping_tool.scrapers.base import ScrapeProvider


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_listing_extraction(idx: int = 1) -> ListingExtraction:
    return ListingExtraction(
        source_listing_id=f"id-{idx}",
        name=f"Listing {idx}",
        url=f"https://airbnb.com/rooms/{idx}",
        nightly_price=80.0,
        currency="USD",
    )


def _make_raw_payload(text: str, idx: int = 0) -> RawPayload:
    return RawPayload(
        source="airbnb",
        url=f"https://airbnb.com/rooms/{idx}",
        payload=text,
        content_hash=_hash(text),
    )


class _FakeScraper(ScrapeProvider):
    source = "airbnb"

    def __init__(self, payloads: list[RawPayload]) -> None:
        self._payloads = payloads

    def search(self, query: SearchQuery) -> list[RawPayload]:
        return self._payloads


class _FakeExtractor(Extractor):
    def __init__(self, results: list[ExtractionResult], delay: float = 0.0) -> None:
        super().__init__(client=None, model="stub")
        self._results = list(results)
        self._delay = delay

    def extract(self, source: str, url: str, payload: str) -> ExtractionResult:
        if self._delay > 0:
            time.sleep(self._delay)
        if not self._results:
            raise RuntimeError("_FakeExtractor ran out of results")
        return self._results.pop(0)


def _make_pipeline(engine, scrapers, extractor, log_dir: str):
    """Build a Pipeline wired to an in-memory engine and a specific log_dir."""
    import config as config_mod
    from pipeline import Pipeline

    orig_log_dir = config_mod.settings.log_dir
    config_mod.settings.log_dir = log_dir

    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    pipeline = Pipeline(
        scrapers=scrapers,
        extractor=extractor,
        repo=Repo(),
        session_factory=SessionFactory,
    )

    # Restore setting after creation (it's read during run(), so we patch it
    # there via tmp_path fixture instead — see individual tests)
    config_mod.settings.log_dir = orig_log_dir
    return pipeline, SessionFactory


# ---------------------------------------------------------------------------
# JSON-lines log file tests
# ---------------------------------------------------------------------------


class TestRunLogging:
    """After a run, logs/<run_id>.jsonl exists and every line is valid JSON."""

    def test_log_file_created(self, tmp_path: Path) -> None:
        import config as config_mod
        from pipeline import Pipeline

        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        SF = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        raw = _make_raw_payload("log-test-1", idx=1)
        result = ExtractionResult(listing=_make_listing_extraction(1), status="ok")
        extractor = _FakeExtractor([result])
        pipeline = Pipeline(
            scrapers=[_FakeScraper([raw])],
            extractor=extractor,
            repo=Repo(),
            session_factory=SF,
        )

        log_dir = str(tmp_path / "logs")
        orig = config_mod.settings.log_dir
        config_mod.settings.log_dir = log_dir
        try:
            run_id = pipeline.run(SearchQuery(area="Test", sources=["airbnb"]))
        finally:
            config_mod.settings.log_dir = orig

        log_file = Path(log_dir) / f"{run_id}.jsonl"
        assert log_file.exists(), f"Expected log file at {log_file}"

    def test_log_lines_are_valid_json_with_required_keys(self, tmp_path: Path) -> None:
        import config as config_mod
        from pipeline import Pipeline

        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        SF = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        raw = _make_raw_payload("log-test-2", idx=2)
        result = ExtractionResult(listing=_make_listing_extraction(2), status="ok")
        extractor = _FakeExtractor([result])
        pipeline = Pipeline(
            scrapers=[_FakeScraper([raw])],
            extractor=extractor,
            repo=Repo(),
            session_factory=SF,
        )

        log_dir = str(tmp_path / "logs")
        orig = config_mod.settings.log_dir
        config_mod.settings.log_dir = log_dir
        try:
            run_id = pipeline.run(SearchQuery(area="Test", sources=["airbnb"]))
        finally:
            config_mod.settings.log_dir = orig

        log_file = Path(log_dir) / f"{run_id}.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) > 0, "Log file should contain at least one line"

        for line in lines:
            entry = json.loads(line)  # raises if invalid JSON
            assert "ts" in entry, f"Missing 'ts' in: {entry}"
            assert "level" in entry, f"Missing 'level' in: {entry}"
            assert "msg" in entry, f"Missing 'msg' in: {entry}"

    def test_dry_run_creates_log_file(self, tmp_path: Path) -> None:
        import config as config_mod
        from pipeline import Pipeline

        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        SF = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        pipeline = Pipeline(
            scrapers=[_FakeScraper([])],
            extractor=_FakeExtractor([]),
            repo=Repo(),
            session_factory=SF,
        )

        log_dir = str(tmp_path / "logs")
        orig = config_mod.settings.log_dir
        config_mod.settings.log_dir = log_dir
        try:
            result = pipeline.run(SearchQuery(area="Test", sources=["airbnb"]), dry_run=True)
        finally:
            config_mod.settings.log_dir = orig

        assert result is None, "dry_run should return None"
        log_files = list(Path(log_dir).glob("dry-*.jsonl"))
        assert len(log_files) == 1, "dry run should create exactly one log file"


# ---------------------------------------------------------------------------
# SIGINT cancellation test
# ---------------------------------------------------------------------------


class TestSigintCancellation:
    """Sending SIGINT mid-pipeline sets run.status='cancelled' and preserves completed work."""

    @pytest.mark.timeout(10)
    def test_sigint_cancels_run_and_preserves_completed_snapshots(self, tmp_path: Path) -> None:
        import config as config_mod
        from pipeline import Pipeline

        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        SF = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        # Three payloads; the extractor sleeps 0.15s per call so SIGINT (at 0.05s)
        # arrives while the first extraction is in progress.  PEP 475 means the
        # sleep completes, the first record is fully stored, and the cancel flag
        # prevents processing of the remaining records.
        payloads = [_make_raw_payload(f"sigint-payload-{i}", idx=i) for i in range(3)]
        results = [
            ExtractionResult(listing=_make_listing_extraction(i), status="ok")
            for i in range(3)
        ]
        extractor = _FakeExtractor(results, delay=0.15)

        pipeline = Pipeline(
            scrapers=[_FakeScraper(payloads)],
            extractor=extractor,
            repo=Repo(),
            session_factory=SF,
        )

        # Send SIGINT from a daemon thread after 0.05 s — well before the first
        # extraction sleep finishes.
        def _send_sigint() -> None:
            time.sleep(0.05)
            os.kill(os.getpid(), signal.SIGINT)

        t = threading.Thread(target=_send_sigint, daemon=True)
        t.start()

        log_dir = str(tmp_path / "logs")
        orig = config_mod.settings.log_dir
        config_mod.settings.log_dir = log_dir
        try:
            run_id = pipeline.run(SearchQuery(area="Test", sources=["airbnb"]))
        finally:
            config_mod.settings.log_dir = orig

        t.join(timeout=2.0)

        assert run_id is not None
        with SF() as sess:
            run = sess.get(SearchRun, run_id)
            assert run is not None
            assert run.status == "cancelled", f"Expected 'cancelled', got '{run.status}'"

            snapshots = sess.scalars(
                select(ListingSnapshot).where(ListingSnapshot.run_id == run_id)
            ).all()
            assert len(snapshots) >= 1, (
                f"Expected at least one completed snapshot before cancellation, got {len(snapshots)}"
            )


# ---------------------------------------------------------------------------
# --dry-run flag test
# ---------------------------------------------------------------------------


class TestDryRun:
    """--dry-run produces no DB rows and exits 0."""

    def test_dry_run_no_db_rows(self, tmp_path: Path) -> None:
        import config as config_mod
        from pipeline import Pipeline

        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        SF = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        payloads = [_make_raw_payload(f"dry-{i}", idx=i) for i in range(2)]
        extractor = _FakeExtractor([])  # no results needed — dry run never calls extract

        pipeline = Pipeline(
            scrapers=[_FakeScraper(payloads)],
            extractor=extractor,
            repo=Repo(),
            session_factory=SF,
        )

        log_dir = str(tmp_path / "logs")
        orig = config_mod.settings.log_dir
        config_mod.settings.log_dir = log_dir
        try:
            result = pipeline.run(
                SearchQuery(area="Test", sources=["airbnb"]),
                dry_run=True,
            )
        finally:
            config_mod.settings.log_dir = orig

        assert result is None, "dry_run should return None (no run_id)"

        with SF() as sess:
            runs = sess.scalars(select(SearchRun)).all()
            raw_scrapes = sess.scalars(select(RawScrape)).all()
            listings = sess.scalars(select(ListingSnapshot)).all()

        assert len(runs) == 0, f"Expected 0 SearchRun rows, got {len(runs)}"
        assert len(raw_scrapes) == 0, f"Expected 0 RawScrape rows, got {len(raw_scrapes)}"
        assert len(listings) == 0, f"Expected 0 ListingSnapshot rows, got {len(listings)}"

    def test_dry_run_cli_exits_0(self, tmp_path: Path) -> None:
        """cli scan --dry-run exits 0 and prints the dry-run message."""
        import config as config_mod
        from click.testing import CliRunner
        from cli import scan
        from unittest import mock

        log_dir = str(tmp_path / "logs")
        orig = config_mod.settings.log_dir
        config_mod.settings.log_dir = log_dir

        runner = CliRunner()
        # Patch _build_scrapers so no real scrapers are needed, and we bypass
        # the Anthropic import path (--dry-run shouldn't need a client).
        with mock.patch("cli._build_scrapers", return_value=[]):
            try:
                result = runner.invoke(scan, ["Test Area", "--dry-run"])
            finally:
                config_mod.settings.log_dir = orig

        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
        assert "Dry run complete" in result.output


# ---------------------------------------------------------------------------
# --no-extract flag test
# ---------------------------------------------------------------------------


class TestNoExtract:
    """--no-extract persists RawScrape rows with status='pending' and no ExtractionLog rows."""

    def test_no_extract_raw_scrapes_pending_no_extraction_logs(self, tmp_path: Path) -> None:
        import config as config_mod
        from pipeline import Pipeline

        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        SF = sessionmaker(bind=eng, autoflush=False, autocommit=False)

        payloads = [_make_raw_payload(f"no-extract-{i}", idx=i) for i in range(3)]
        # Extractor should never be called; pass an empty one that raises if called.
        extractor = _FakeExtractor([])

        pipeline = Pipeline(
            scrapers=[_FakeScraper(payloads)],
            extractor=extractor,
            repo=Repo(),
            session_factory=SF,
        )

        log_dir = str(tmp_path / "logs")
        orig = config_mod.settings.log_dir
        config_mod.settings.log_dir = log_dir
        try:
            run_id = pipeline.run(
                SearchQuery(area="Test", sources=["airbnb"]),
                no_extract=True,
            )
        finally:
            config_mod.settings.log_dir = orig

        assert run_id is not None

        with SF() as sess:
            raw_scrapes = sess.scalars(
                select(RawScrape).where(RawScrape.run_id == run_id)
            ).all()
            extraction_logs = sess.scalars(select(ExtractionLog)).all()
            snapshots = sess.scalars(
                select(ListingSnapshot).where(ListingSnapshot.run_id == run_id)
            ).all()

        assert len(raw_scrapes) == 3, f"Expected 3 RawScrape rows, got {len(raw_scrapes)}"
        for rs in raw_scrapes:
            assert rs.status == "pending", (
                f"Expected status='pending', got '{rs.status}' for {rs.url}"
            )
        assert len(extraction_logs) == 0, (
            f"Expected 0 ExtractionLog rows, got {len(extraction_logs)}"
        )
        assert len(snapshots) == 0, (
            f"Expected 0 ListingSnapshot rows (no extraction), got {len(snapshots)}"
        )
