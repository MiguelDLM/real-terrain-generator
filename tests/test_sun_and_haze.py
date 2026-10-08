"""Unit tests for the sun position and atmospheric haze helpers."""

import math
import pytest
from core.geo import solar_position, haze_extinction_per_m


def test_noon_sun_faces_the_equator():
    # Northern mid-latitude: noon sun due south; southern: due north
    el, az = solar_position(40.0, 172, 12.0)
    assert az == pytest.approx(180.0, abs=0.5)
    assert el == pytest.approx(90.0 - 40.0 + 23.44, abs=0.5)   # June solstice
    el, az = solar_position(-40.0, 172, 12.0)
    assert az == pytest.approx(0.0, abs=0.5) or az == pytest.approx(360.0, abs=0.5)
    assert el == pytest.approx(90.0 - 40.0 - 23.44, abs=0.5)


def test_morning_sun_is_east_and_evening_west():
    _, az_am = solar_position(16.8, 80, 8.0)
    _, az_pm = solar_position(16.8, 80, 16.0)
    assert 45.0 < az_am < 135.0
    assert 225.0 < az_pm < 315.0


def test_equinox_sunrise_is_on_the_horizon():
    el, az = solar_position(30.0, 80, 6.0)       # ~March equinox
    assert el == pytest.approx(0.0, abs=2.0)
    assert az == pytest.approx(90.0, abs=3.0)


def test_haze_extinction_follows_koschmieder():
    # At the visibility distance the contrast drops to 2%
    sigma = haze_extinction_per_m(20.0)
    assert math.exp(-sigma * 20000.0) == pytest.approx(0.02, rel=0.01)
    assert haze_extinction_per_m(40.0) == pytest.approx(sigma / 2.0)
