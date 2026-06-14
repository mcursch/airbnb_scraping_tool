"""Tests for geocode.reverse_geocode — fully offline (httpx is mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from geocode import reverse_geocode, _format_area


def _mock_client(json_payload, status_code: int = 200) -> httpx.Client:
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = json_payload
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=resp
        )
    client.get.return_value = resp
    return client


class TestFormatArea:
    def test_city_and_country(self):
        assert _format_area({"address": {"city": "Lisbon", "country": "Portugal"}}) == "Lisbon, Portugal"

    def test_town_fallback(self):
        assert _format_area({"address": {"town": "Sintra", "country": "Portugal"}}) == "Sintra, Portugal"

    def test_locality_only(self):
        assert _format_area({"address": {"city": "Lisbon"}}) == "Lisbon"

    def test_display_name_fallback(self):
        out = _format_area({"display_name": "Rua X, Alfama, Lisbon, Portugal"})
        assert out == "Rua X, Portugal"

    def test_empty_returns_none(self):
        assert _format_area({}) is None
        assert _format_area("not a dict") is None


class TestReverseGeocode:
    def test_happy_path(self):
        client = _mock_client({"address": {"city": "Lisbon", "country": "Portugal"}})
        assert reverse_geocode(38.72, -9.14, http_client=client) == "Lisbon, Portugal"

    def test_sends_user_agent_and_coords(self):
        client = _mock_client({"address": {"city": "Lisbon", "country": "Portugal"}})
        reverse_geocode(38.72, -9.14, http_client=client)
        call = client.get.call_args
        assert call.kwargs["headers"]["User-Agent"].startswith("roomradar/")
        assert call.kwargs["params"]["lat"] == 38.72
        assert call.kwargs["params"]["lon"] == -9.14

    def test_requests_english_place_names(self):
        client = _mock_client({"address": {"city": "Tokyo", "country": "Japan"}})
        reverse_geocode(35.68, 139.65, http_client=client)
        assert client.get.call_args.kwargs["params"]["accept-language"] == "en"

    def test_http_error_returns_none(self):
        client = _mock_client({}, status_code=503)
        assert reverse_geocode(0.0, 0.0, http_client=client) is None

    def test_network_error_returns_none(self):
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = httpx.ConnectError("boom")
        assert reverse_geocode(0.0, 0.0, http_client=client) is None
