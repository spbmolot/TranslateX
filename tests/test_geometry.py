"""Tests for geometry utility functions."""
import pytest
from utils.geometry import (
    easyocr_bbox_to_rect,
    scale_rect,
    region_relative_rect,
    clamp_rect,
)


def test_easyocr_bbox_to_rect_basic():
    bbox = [[10, 20], [110, 20], [110, 50], [10, 50]]
    x, y, w, h = easyocr_bbox_to_rect(bbox)
    assert x == 10
    assert y == 20
    assert w == 100
    assert h == 30


def test_easyocr_bbox_to_rect_non_axis_aligned():
    # Slightly rotated — should use min/max bounds
    bbox = [[12, 21], [108, 19], [110, 52], [8, 50]]
    x, y, w, h = easyocr_bbox_to_rect(bbox)
    assert x == 8
    assert y == 19
    assert w == 110 - 8
    assert h == 52 - 19


def test_scale_rect_1x():
    assert scale_rect(10, 20, 100, 50, 1.0, 1.0) == (10, 20, 100, 50)


def test_scale_rect_hidpi():
    # 150% DPI: physical px → logical px, divide by 1.5
    x, y, w, h = scale_rect(300, 450, 600, 300, 1.5, 1.5)
    assert x == 200
    assert y == 300
    assert w == 400
    assert h == 200


def test_region_relative_rect():
    x, y, w, h = region_relative_rect(500, 400, 100, 30, 300, 200)
    assert x == 200
    assert y == 200
    assert w == 100
    assert h == 30


def test_clamp_rect_within_bounds():
    assert clamp_rect(10, 10, 50, 30, 200, 100) == (10, 10, 50, 30)


def test_clamp_rect_overflow():
    x, y, w, h = clamp_rect(180, 80, 50, 40, 200, 100)
    assert x == 180
    assert y == 80
    assert w == 20   # clamped: 200 - 180
    assert h == 20   # clamped: 100 - 80


def test_clamp_rect_negative_origin():
    x, y, w, h = clamp_rect(-5, -5, 50, 30, 200, 100)
    assert x == 0
    assert y == 0
