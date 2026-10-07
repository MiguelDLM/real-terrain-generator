"""Unit tests for OpenTopography AAIGrid parser and Provider implementation."""

from pathlib import Path
from unittest.mock import patch, MagicMock
import urllib.error
import numpy as np
import pytest

from core.geo import BoundingBox, bbox_from_center
from core.dem_sources.opentopography import (
    OpenTopographyProvider,
    parse_aaigrid,
    SUPPORTED_DATASETS,
)

SAMPLE_AAIGRID_TEXT = """
NCOLS 4
NROWS 3
XLLCORNER -16.650000
YLLCORNER 28.250000
CELLSIZE 0.000833333333333
NODATA_VALUE -9999
1500.2 1520.4 1540.8 1560.1
1600.0 1625.5 1650.0 1680.2
1700.8 1730.0 1760.4 1795.0
"""

SAMPLE_AAIGRID_WITH_NODATA = """
NCOLS 3
NROWS 2
XLLCORNER 0.0
YLLCORNER 0.0
CELLSIZE 0.01
NODATA_VALUE -9999
100.0 -9999 300.0
400.0 500.0 600.0
"""


def test_parse_aaigrid_valid():
    grid, header = parse_aaigrid(SAMPLE_AAIGRID_TEXT)
    assert header["NCOLS"] == 4
    assert header["NROWS"] == 3
    assert grid.shape == (3, 4)
    assert grid.dtype == np.float32
    assert pytest.approx(grid[0, 0], abs=1e-2) == 1500.2
    assert pytest.approx(grid[2, 3], abs=1e-2) == 1795.0


def test_parse_aaigrid_nodata_replacement():
    grid, header = parse_aaigrid(SAMPLE_AAIGRID_WITH_NODATA)
    assert grid.shape == (2, 3)
    # No values should be -9999
    assert not np.any(grid == -9999.0)
    # The nodata value should be replaced with the minimum valid elevation (100.0)
    assert pytest.approx(grid[0, 1], abs=1e-2) == 100.0


def test_parse_aaigrid_invalid_header():
    with pytest.raises(ValueError, match="missing NCOLS"):
        parse_aaigrid("FOO 10\nBAR 20\n1 2 3")


def test_opentopography_provider_unsupported_dataset():
    with pytest.raises(ValueError, match="Unsupported DEM type"):
        OpenTopographyProvider(api_key="valid_key", dem_type="NONEXISTENT")


def test_opentopography_provider_missing_key():
    provider = OpenTopographyProvider(api_key="", dem_type="COP30")
    bbox = bbox_from_center(28.27, -16.64, 2.0, 2.0)
    with pytest.raises(ValueError, match="requires an API Key"):
        provider.fetch_elevation_grid(bbox)


def test_opentopography_provider_successful_fetch(tmp_path: Path):
    provider = OpenTopographyProvider(api_key="test_api_key_123", dem_type="COP30")

    mock_resp = MagicMock()
    mock_resp.read.return_value = SAMPLE_AAIGRID_TEXT.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        bbox = bbox_from_center(28.27, -16.64, 2.0, 2.0)
        grid = provider.fetch_elevation_grid(
            bbox=bbox,
            target_resolution=32,
            cache_dir=tmp_path,
        )

    assert grid.shape == (32, 32)
    assert pytest.approx(grid.min_elevation_m, abs=1.0) == 1500.2
    assert pytest.approx(grid.max_elevation_m, abs=1.0) == 1795.0
    assert "Copernicus" in grid.provider_name
    assert provider.requires_api_key


def test_opentopography_provider_auth_failure():
    provider = OpenTopographyProvider(api_key="invalid_token", dem_type="COP30")

    http_err = urllib.error.HTTPError(
        url="https://portal.opentopography.org/API/globaldem",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=MagicMock(read=lambda: b"Invalid API_Key specified"),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        bbox = bbox_from_center(28.27, -16.64, 2.0, 2.0)
        with pytest.raises(RuntimeError, match="Authentication Failed"):
            provider.fetch_elevation_grid(bbox)
