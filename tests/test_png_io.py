"""Unit tests for PNG encoding/decoding and Terrarium height decoding."""

import numpy as np
import pytest
from core.png_io import encode_png_gray16, decode_png_rgb
from core.dem_sources.aws_terrarium import decode_terrarium_elevation


def test_terrarium_elevation_decoding():
    # Sea level (0m) -> (128, 0, 0)
    # (128 * 256 + 0 + 0/256) - 32768 = 32768 - 32768 = 0.0
    sea_level = np.array([[[128, 0, 0]]], dtype=np.uint8)
    elev_0 = decode_terrarium_elevation(sea_level)
    assert pytest.approx(elev_0[0, 0], abs=1e-3) == 0.0

    # 100 meters -> (128, 100, 0)
    h_100 = np.array([[[128, 100, 0]]], dtype=np.uint8)
    elev_100 = decode_terrarium_elevation(h_100)
    assert pytest.approx(elev_100[0, 0], abs=1e-3) == 100.0

    # Mount Everest (8848m)
    # 32768 + 8848 = 41616 -> 41616 // 256 = 162, 41616 % 256 = 144
    everest = np.array([[[162, 144, 0]]], dtype=np.uint8)
    elev_ev = decode_terrarium_elevation(everest)
    assert pytest.approx(elev_ev[0, 0], abs=1e-3) == 8848.0

    # Below sea level (Dead Sea ~ -430m)
    # 32768 - 430 = 32338 -> 32338 // 256 = 126, 32338 % 256 = 82
    dead_sea = np.array([[[126, 82, 0]]], dtype=np.uint8)
    elev_ds = decode_terrarium_elevation(dead_sea)
    assert pytest.approx(elev_ds[0, 0], abs=1e-3) == -430.0


def test_encode_png_gray16():
    # Create 16-bit test gradient
    img = np.array([
        [0, 16384, 32768],
        [49152, 65535, 1000]
    ], dtype=np.uint16)

    png_bytes = encode_png_gray16(img)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in png_bytes
    assert b"IDAT" in png_bytes
    assert b"IEND" in png_bytes
