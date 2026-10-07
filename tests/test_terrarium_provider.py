"""Unit tests for AWSTerrariumProvider stitching, decoding, and grid generation."""

from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest
from core.geo import BoundingBox, bbox_from_center
from core.dem_sources.aws_terrarium import AWSTerrariumProvider
from core.png_io import decode_png_rgb


def _make_dummy_terrarium_png(height_val_m: float = 500.0) -> bytes:
    """Create a minimal 256x256 PNG encoded with a constant elevation."""
    # (R * 256 + G + B / 256) = 32768 + height_val_m
    total = int(32768 + height_val_m)
    r = total // 256
    g = total % 256
    b = 0

    # Create uncompressed PNG bytes manually
    import struct, zlib
    raw_scanlines = bytearray()
    row = bytes([r, g, b] * 256)
    for _ in range(256):
        raw_scanlines.append(0)  # filter none
        raw_scanlines.extend(row)

    compressed = zlib.compress(bytes(raw_scanlines))
    
    ihdr = struct.pack(">IIBBBBB", 256, 256, 8, 2, 0, 0, 0)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    idat_crc = struct.pack(">I", zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF)
    iend_crc = struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I4s", len(ihdr), b"IHDR") + ihdr + ihdr_crc
        + struct.pack(">I4s", len(compressed), b"IDAT") + compressed + idat_crc
        + struct.pack(">I4s", 0, b"IEND") + iend_crc
    )


def test_terrarium_provider_fetching_and_resampling(tmp_path: Path):
    dummy_tile = _make_dummy_terrarium_png(height_val_m=3718.0)  # Peak height of Teide

    provider = AWSTerrariumProvider()

    with patch.object(provider, "_fetch_tile_bytes", return_value=dummy_tile):
        bbox = bbox_from_center(center_lat=28.27, center_lon=-16.64, width_km=2.0, height_km=2.0)
        grid = provider.fetch_elevation_grid(
            bbox=bbox,
            zoom=12,
            target_resolution=128,
            cache_dir=tmp_path,
        )

    assert grid.shape == (128, 128)
    assert pytest.approx(grid.min_elevation_m, abs=1.0) == 3718.0
    assert pytest.approx(grid.max_elevation_m, abs=1.0) == 3718.0
    assert grid.width_m > 1500.0
    assert grid.height_m > 1500.0
    assert grid.provider_name == "AWS Terrain Tiles (Terrarium)"
    assert not provider.requires_api_key
