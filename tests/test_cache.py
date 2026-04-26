"""Tests for TranslationCache."""
import time
import pytest
from core.cache import TranslationCache


def test_put_and_get():
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    cache.put("Hello", "ru", "Привет")
    assert cache.get("Hello", "ru") == "Привет"


def test_normalization():
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    cache.put("  Hello  ", "ru", "Привет")
    assert cache.get("hello", "ru") == "Привет"
    assert cache.get("HELLO", "ru") == "Привет"


def test_ttl_expiry():
    cache = TranslationCache(max_size=10, ttl_seconds=1)
    cache.put("Hello", "ru", "Привет")
    # Manually backdate the timestamp
    key = ("hello", "ru")
    text, _ = cache._data[key]
    cache._data[key] = (text, time.time() - 2)
    assert cache.get("Hello", "ru") is None


def test_lru_eviction():
    cache = TranslationCache(max_size=3, ttl_seconds=3600)
    cache.put("a", "ru", "A")
    cache.put("b", "ru", "B")
    cache.put("c", "ru", "C")
    cache.put("d", "ru", "D")  # should evict "a"
    assert cache.get("a", "ru") is None
    assert cache.get("d", "ru") == "D"


def test_cache_miss_returns_none():
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    assert cache.get("nonexistent", "ru") is None


def test_different_target_langs():
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    cache.put("Hello", "ru", "Привет")
    cache.put("Hello", "de", "Hallo")
    assert cache.get("Hello", "ru") == "Привет"
    assert cache.get("Hello", "de") == "Hallo"
    assert cache.get("Hello", "fr") is None
