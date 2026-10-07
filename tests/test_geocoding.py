"""Unit tests for Geocoding parser and results."""

import json
from unittest.mock import patch, MagicMock
import pytest
from core.geocoding import search_location, GeocodingResult
from core.geo import BoundingBox


MOCK_NOMINATIM_RESPONSE = json.dumps([
    {
        "place_id": 12345,
        "licence": "Data © OpenStreetMap contributors",
        "osm_type": "node",
        "osm_id": 98765,
        "lat": "28.2723",
        "lon": "-16.6425",
        "class": "natural",
        "type": "volcano",
        "importance": 0.85,
        "display_name": "Teide, La Orotava, Santa Cruz de Tenerife, Canarias, España",
        "boundingbox": ["28.25", "28.29", "-16.66", "-16.62"]
    }
]).encode("utf-8")


def test_search_location_parsing():
    mock_resp = MagicMock()
    mock_resp.read.return_value = MOCK_NOMINATIM_RESPONSE
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        results = search_location("Teide")

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, GeocodingResult)
    assert pytest.approx(res.latitude, abs=1e-4) == 28.2723
    assert pytest.approx(res.longitude, abs=1e-4) == -16.6425
    assert "Teide" in res.display_name
    assert isinstance(res.bbox, BoundingBox)
    assert res.bbox.min_lat == 28.25
    assert res.bbox.max_lat == 28.29


def test_search_location_empty_query():
    results = search_location("   ")
    assert results == []
