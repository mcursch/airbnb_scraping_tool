"""
Tests for extraction/batch.py

All tests run offline — both the Anthropic Messages client and the
Message Batches client are monkeypatched so no real API calls are made.

Acceptance criteria verified:
- batch_extract with 25 records submits exactly one batch request and writes
  25 ExtractionLog rows on success.
- batch_extract with 5 records (below the default threshold of 20) delegates
  to the synchronous extractor without calling the Batches API.
- --batch CLI flag forces the batch path even for a single record.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, ExtractionLog, RawScrape
from extraction.batch import batch_extract
from schemas.listing import ExtractedListing, ListingExtraction

# ---------------------------------------------------------------------------
# Constants / fixtures dir
# ---------------------------------------------------------------------------

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
DEFAULT_THRESHOLD = 20


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_extracted_listing(idx: int = 0) -> ExtractedListing:
    return ExtractedListing(
        source_listing_id=str(10000 + idx),
        name=f"Test Listing {idx}",
        nightly_price=float(50 + idx),
        currency="USD",
    )


def _listing_extraction_json(idx: int = 0) -> str:
    """Return a JSON string that ListingExtraction.model_validate_json() accepts."""
    return json.dumps(
        {
            "listings": [
                {
                    "source_listing_id": str(10000 + idx),
                    "name": f"Test Listing {idx}",
                    "nightly_price": float(50 + idx),
                    "currency": "USD",
                }
            ]
        }
    )


def _make_batch_result(
    custom_id: str,
    idx: int = 0,
    *,
    error: bool = False,
    cache_read_input_tokens: int = 0,
) -> MagicMock:
    """Build a fake individual batch result object."""
    result_obj = MagicMock()
    result_obj.custom_id = custom_id

    if error:
        result_obj.result.type = "errored"
        result_obj.result.error = MagicMock()
        result_obj.result.error.__str__ = lambda self: "Simulated batch error"
    else:
        result_obj.result.type = "succeeded"

        # Build a fake content block
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = _listing_extraction_json(idx)

        # Build a fake message with usage
        message = MagicMock()
        message.content = [text_block]
        message.usage.input_tokens = 300
        message.usage.output_tokens = 150
        message.usage.cache_read_input_tokens = cache_read_input_tokens

        result_obj.result.message = message

    return result_obj


def _make_batch_client(
    scrape_ids: list[int],
    *,
    error_ids: set[int] | None = None,
) -> MagicMock:
    """Return a mock Anthropic client wired up for the Message Batches API.

    The mock's ``beta.messages.batches`` sub-namespace is configured so:
    - ``create()`` returns a fake batch object with ``processing_status="ended"``
      (already done; no polling required)
    - ``retrieve()`` also returns ``processing_status="ended"``
    - ``results()`` yields one result per scrape_id
    """
    if error_ids is None:
        error_ids = set()

    fake_batch = MagicMock()
    fake_batch.id = "msgbatch_test_001"
    fake_batch.processing_status = "ended"

    batch_results = [
        _make_batch_result(
            str(sid), idx=i, error=(sid in error_ids)
        )
        for i, sid in enumerate(scrape_ids)
    ]

    mock_client = MagicMock()
    mock_client.beta.messages.batches.create.return_value = fake_batch
    mock_client.beta.messages.batches.retrieve.return_value = fake_batch
    mock_client.beta.messages.batches.results.return_value = iter(batch_results)

    return mock_client


def _make_sync_client() -> MagicMock:
    """Return a mock client for the synchronous extraction path."""
    from tests.test_batch import _make_extracted_listing
    from schemas.listing import ListingExtraction

    mock_usage = MagicMock()
    mock_usage.input_tokens = 300
    mock_usage.output_tokens = 150
    mock_usage.cache_read_input_tokens = 0
    mock_usage.cache_creation_input_tokens = 300

    mock_response = MagicMock()
    mock_response.parsed = ListingExtraction(listings=[_make_extracted_listing()])
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.messages.parse.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session() -> Session:
    """Fresh in-memory SQLite session per test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _make_raw_scrapes(session: Session, n: int) -> list[RawScrape]:
    """Create and flush *n* RawScrape rows backed by the sample fixture."""
    payload = _load_fixture("sample_airbnb.json")
    scrapes = [
        RawScrape(source="airbnb", payload=payload, status="pending")
        for _ in range(n)
    ]
    session.add_all(scrapes)
    session.flush()
    return scrapes


# ---------------------------------------------------------------------------
# Test: 25 records → single batch request, 25 ExtractionLog rows
# ---------------------------------------------------------------------------


class TestBatchPathAboveThreshold:
    """25 records → exactly one batch submission, 25 ExtractionLog rows."""

    def test_submits_exactly_one_batch_request(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 25)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        mock_client.beta.messages.batches.create.assert_called_once()

    def test_25_records_write_25_extraction_log_rows(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 25)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        results = batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        logs = db_session.query(ExtractionLog).all()
        assert len(logs) == 25

    def test_all_25_logs_have_success_status(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 25)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        logs = db_session.query(ExtractionLog).all()
        statuses = [log.status for log in logs]
        assert all(s == "success" for s in statuses), f"Non-success statuses: {statuses}"

    def test_returns_25_listing_extraction_objects(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 25)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        results = batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        assert len(results) == 25
        assert all(isinstance(r, ListingExtraction) for r in results)

    def test_all_raw_scrapes_marked_extracted(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 25)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        for scrape in scrapes:
            db_session.refresh(scrape)
            assert scrape.status == "extracted", f"Scrape {scrape.id} status={scrape.status}"

    def test_batch_requests_contain_all_record_payloads(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 25)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        create_call = mock_client.beta.messages.batches.create.call_args
        submitted_requests = create_call.kwargs.get("requests") or create_call.args[0]
        assert len(submitted_requests) == 25

        # Each custom_id must match a known scrape id
        submitted_ids = {req["custom_id"] for req in submitted_requests}
        expected_ids = {str(rs.id) for rs in scrapes}
        assert submitted_ids == expected_ids

    def test_polls_until_ended(self, db_session: Session) -> None:
        """If the first retrieve returns in_progress, keeps polling."""
        scrapes = _make_raw_scrapes(db_session, 25)
        scrape_ids = [rs.id for rs in scrapes]

        in_progress_batch = MagicMock()
        in_progress_batch.id = "msgbatch_test_001"
        in_progress_batch.processing_status = "in_progress"

        ended_batch = MagicMock()
        ended_batch.id = "msgbatch_test_001"
        ended_batch.processing_status = "ended"

        batch_results = [
            _make_batch_result(str(sid), idx=i) for i, sid in enumerate(scrape_ids)
        ]

        mock_client = MagicMock()
        mock_client.beta.messages.batches.create.return_value = in_progress_batch
        mock_client.beta.messages.batches.retrieve.return_value = ended_batch
        mock_client.beta.messages.batches.results.return_value = iter(batch_results)

        with patch("extraction.batch._POLL_INTERVAL_SECONDS", 0):
            batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        mock_client.beta.messages.batches.retrieve.assert_called_once_with("msgbatch_test_001")


# ---------------------------------------------------------------------------
# Test: 5 records (below threshold) → synchronous path, no Batches API
# ---------------------------------------------------------------------------


class TestSyncDelegationBelowThreshold:
    """5 records (< 20 threshold) → sync extractor called, Batches API NOT called."""

    def test_below_threshold_does_not_call_batch_api(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 5)
        mock_client = _make_sync_client()

        batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        mock_client.beta.messages.batches.create.assert_not_called()

    def test_below_threshold_calls_synchronous_extractor(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 5)
        mock_client = _make_sync_client()

        results = batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        # Synchronous path calls messages.parse once per record
        assert mock_client.messages.parse.call_count == 5

    def test_exactly_at_threshold_uses_sync_path(self, db_session: Session) -> None:
        """len == threshold is NOT above threshold; sync path expected."""
        scrapes = _make_raw_scrapes(db_session, DEFAULT_THRESHOLD)
        mock_client = _make_sync_client()

        batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        mock_client.beta.messages.batches.create.assert_not_called()
        assert mock_client.messages.parse.call_count == DEFAULT_THRESHOLD

    def test_one_above_threshold_uses_batch_path(self, db_session: Session) -> None:
        """len == threshold + 1 triggers the batch path."""
        n = DEFAULT_THRESHOLD + 1
        scrapes = _make_raw_scrapes(db_session, n)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        batch_extract(scrapes, db_session, threshold=DEFAULT_THRESHOLD, client=mock_client)

        mock_client.beta.messages.batches.create.assert_called_once()

    def test_threshold_reads_from_settings_by_default(self, db_session: Session) -> None:
        """When threshold is not passed, settings.batch_threshold (20) is used."""
        # 5 records should use sync path with default threshold of 20
        scrapes = _make_raw_scrapes(db_session, 5)
        mock_client = _make_sync_client()

        batch_extract(scrapes, db_session, client=mock_client)  # no explicit threshold

        mock_client.beta.messages.batches.create.assert_not_called()
        assert mock_client.messages.parse.call_count == 5


# ---------------------------------------------------------------------------
# Test: --batch CLI flag forces batch path for a single record
# ---------------------------------------------------------------------------


class TestCliBatchFlag:
    """--batch flag forces batch path regardless of threshold."""

    def test_batch_flag_forces_batch_for_single_record(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 1)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        # Simulate the CLI --batch flag: it passes threshold=0 to batch_extract.
        results = batch_extract(scrapes, db_session, threshold=0, client=mock_client)

        mock_client.beta.messages.batches.create.assert_called_once()
        assert len(results) == 1

    def test_batch_flag_threshold_zero_single_record_writes_log(
        self, db_session: Session
    ) -> None:
        scrapes = _make_raw_scrapes(db_session, 1)
        mock_client = _make_batch_client([rs.id for rs in scrapes])

        batch_extract(scrapes, db_session, threshold=0, client=mock_client)

        logs = db_session.query(ExtractionLog).all()
        assert len(logs) == 1
        assert logs[0].status == "success"

    def test_cli_scan_batch_flag_invokes_batch_extract_with_threshold_zero(
        self, db_session: Session
    ) -> None:
        """cli._cmd_scan passes threshold=0 when --batch is set."""
        import argparse
        from cli import _cmd_scan

        scrapes = _make_raw_scrapes(db_session, 1)

        # Patch batch_extract at the cli module level (it's imported there at module top).
        captured_threshold: list[int | None] = []

        def fake_batch_extract(raw_scrapes, session, *, threshold=None, client=None):
            captured_threshold.append(threshold)
            return []

        # Build args that simulate: cli.py scan "area" --batch
        args = argparse.Namespace(
            command="scan",
            area="Test Area",
            checkin=None,
            checkout=None,
            guests=None,
            sources="airbnb",
            batch=True,
            no_extract=False,
            dry_run=False,
        )

        # Patch the module-level names that cli.py imported, plus DB setup so
        # we don't touch the filesystem.
        with (
            patch("cli.batch_extract", side_effect=fake_batch_extract),
            patch("cli.create_engine"),
            patch("cli.Base") as mock_base,
            patch("cli.Session") as mock_session_cls,
            patch("cli.RawScrape") as mock_raw_scrape_cls,
        ):
            mock_base.metadata.create_all = MagicMock()
            # Simulate one pending record so the code reaches batch_extract.
            mock_raw_scrape_cls_instance = MagicMock()
            mock_session_cls.return_value.__enter__ = lambda s: db_session
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            # Have the in-memory session return 1 pending scrape for the query.
            db_session.query = MagicMock(
                return_value=MagicMock(
                    filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=scrapes)))
                )
            )

            _cmd_scan(args)

        # The CLI must pass threshold=0 when --batch is set.
        assert captured_threshold == [0], f"Expected threshold=0, got {captured_threshold}"


# ---------------------------------------------------------------------------
# Test: cache_read_tokens persisted correctly (LIN-153)
# ---------------------------------------------------------------------------


class TestCacheReadTokensPersistence:
    """Batch path must store the Anthropic SDK cache_read_input_tokens value
    in ExtractionLog.cache_read_tokens (not silently drop it due to wrong kwarg).
    """

    def test_nonzero_cache_read_tokens_stored_in_log(self, db_session: Session) -> None:
        """cache_read_input_tokens from the SDK usage object must be persisted as
        cache_read_tokens in the ExtractionLog row."""
        scrapes = _make_raw_scrapes(db_session, 1)
        scrape_id = scrapes[0].id

        result = _make_batch_result(str(scrape_id), idx=0, cache_read_input_tokens=768)
        mock_client = MagicMock()
        fake_batch = MagicMock()
        fake_batch.id = "msgbatch_cache_test"
        fake_batch.processing_status = "ended"
        mock_client.beta.messages.batches.create.return_value = fake_batch
        mock_client.beta.messages.batches.retrieve.return_value = fake_batch
        mock_client.beta.messages.batches.results.return_value = iter([result])

        batch_extract(scrapes, db_session, threshold=0, client=mock_client)

        log = db_session.query(ExtractionLog).one()
        assert log.cache_read_tokens == 768

    def test_zero_cache_read_tokens_stored_in_log(self, db_session: Session) -> None:
        """When cache_read_input_tokens is 0, cache_read_tokens must also be 0."""
        scrapes = _make_raw_scrapes(db_session, 1)
        scrape_id = scrapes[0].id

        result = _make_batch_result(str(scrape_id), idx=0, cache_read_input_tokens=0)
        mock_client = MagicMock()
        fake_batch = MagicMock()
        fake_batch.id = "msgbatch_cache_zero_test"
        fake_batch.processing_status = "ended"
        mock_client.beta.messages.batches.create.return_value = fake_batch
        mock_client.beta.messages.batches.retrieve.return_value = fake_batch
        mock_client.beta.messages.batches.results.return_value = iter([result])

        batch_extract(scrapes, db_session, threshold=0, client=mock_client)

        log = db_session.query(ExtractionLog).one()
        assert log.cache_read_tokens == 0


# ---------------------------------------------------------------------------
# Test: error handling in batch path
# ---------------------------------------------------------------------------


class TestBatchErrorHandling:
    """Errored results in a batch must produce failed ExtractionLog rows."""

    def test_errored_result_writes_failed_log(self, db_session: Session) -> None:
        scrapes = _make_raw_scrapes(db_session, 3)
        scrape_ids = [rs.id for rs in scrapes]
        # Make the middle record errored
        error_ids = {scrape_ids[1]}

        # Use threshold=0 to force the batch path for 3 records
        mock_client = _make_batch_client(scrape_ids, error_ids=error_ids)

        results = batch_extract(
            scrapes, db_session, threshold=0, client=mock_client
        )

        # Only 2 successful results
        assert len(results) == 2

        logs = db_session.query(ExtractionLog).order_by(ExtractionLog.id).all()
        assert len(logs) == 3

        statuses = [log.status for log in logs]
        assert statuses.count("success") == 2
        assert statuses.count("failed") == 1

    def test_empty_payload_excluded_from_batch_with_failed_log(
        self, db_session: Session
    ) -> None:
        """Records whose payload fails pretrim are logged as failed before batch submission."""
        good_scrape = RawScrape(
            source="airbnb",
            payload=_load_fixture("sample_airbnb.json"),
            status="pending",
        )
        bad_scrape = RawScrape(source="airbnb", payload="", status="pending")
        db_session.add_all([good_scrape, bad_scrape])
        db_session.flush()

        # Batch client only has a result for the good scrape (bad was excluded from batch)
        mock_client = _make_batch_client([good_scrape.id])

        results = batch_extract(
            [good_scrape, bad_scrape],
            db_session,
            threshold=0,
            client=mock_client,
        )

        # Only the good scrape yields a result
        assert len(results) == 1

        logs = db_session.query(ExtractionLog).order_by(ExtractionLog.id).all()
        assert len(logs) == 2  # one for good (success), one for bad (failed)

        db_session.refresh(bad_scrape)
        assert bad_scrape.status == "failed"
        db_session.refresh(good_scrape)
        assert good_scrape.status == "extracted"
