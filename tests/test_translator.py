"""Tests for TranslationJobThread — uses mocks for external APIs."""
from unittest.mock import MagicMock, patch
import pytest

from core.cache import TranslationCache
from core.ocr import TextBlock
from core.translator import TranslationJobThread, TranslatedBlock


def _make_block(text: str) -> TextBlock:
    return TextBlock(
        text=text,
        bbox=[[0, 0], [100, 0], [100, 20], [0, 20]],
        confidence=0.9,
    )


def _run_job(blocks, provider="google", deepl_key="", cache=None):
    """Helper: run TranslationJobThread synchronously for testing."""
    if cache is None:
        cache = TranslationCache(max_size=10, ttl_seconds=3600)
    job = TranslationJobThread(
        blocks=blocks,
        target_lang="ru",
        source_lang="auto",
        provider=provider,
        deepl_api_key=deepl_key,
        cache=cache,
    )
    results = []
    job.translation_complete.connect(results.append)
    job.run()
    return results[0] if results else []


@patch("core.translator.TranslationJobThread._translate_google")
def test_cache_hit_avoids_api(mock_translate):
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    cache.put("Hello", "ru", "Привет")
    blocks = [_make_block("Hello")]
    result = _run_job(blocks, cache=cache)
    mock_translate.assert_not_called()
    assert result[0].translated == "Привет"


@patch("core.translator.TranslationJobThread._translate_google")
def test_cache_miss_calls_api(mock_translate):
    mock_translate.return_value = ["Мир"]
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    blocks = [_make_block("World")]
    result = _run_job(blocks, cache=cache)
    mock_translate.assert_called_once_with(["World"])
    assert result[0].translated == "Мир"


@patch("core.translator.TranslationJobThread._translate_google")
def test_api_error_returns_original(mock_translate):
    mock_translate.side_effect = RuntimeError("API down")
    blocks = [_make_block("Test")]
    result = _run_job(blocks)
    assert result[0].translated == "Test"


@patch("core.translator.TranslationJobThread._translate_google")
def test_result_stored_in_cache(mock_translate):
    mock_translate.return_value = ["Тест"]
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    blocks = [_make_block("Test")]
    _run_job(blocks, cache=cache)
    # Second call: should hit cache
    mock_translate.reset_mock()
    _run_job(blocks, cache=cache)
    mock_translate.assert_not_called()


@patch("core.translator.TranslationJobThread._translate_google")
def test_api_error_does_not_poison_cache(mock_translate):
    """Фейл перевода НЕ должен кэшироваться: после восстановления API
    следующий тик обязан снова дёрнуть перевод, а не отдать оригинал из кэша."""
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    blocks = [_make_block("Hello")]

    # 1-й прогон: API лежит → показываем оригинал
    mock_translate.side_effect = RuntimeError("API down")
    result = _run_job(blocks, cache=cache)
    assert result[0].translated == "Hello"

    # 2-й прогон: API ожил → должен быть вызван снова и вернуть перевод
    mock_translate.side_effect = None
    mock_translate.return_value = ["Привет"]
    result = _run_job(blocks, cache=cache)
    mock_translate.assert_called()
    assert result[0].translated == "Привет"


@patch("core.translator.TranslationJobThread._translate_google")
def test_partial_failure_caches_only_successes(mock_translate):
    """None в ответе батча = fail конкретного блока: оригинал показан,
    в кэш не попал; успешные соседи закэшированы."""
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    blocks = [_make_block("One"), _make_block("Two")]
    mock_translate.return_value = ["Один", None]
    result = _run_job(blocks, cache=cache)
    assert result[0].translated == "Один"
    assert result[1].translated == "Two"  # fallback на оригинал
    assert cache.get("One", "ru", "auto", "google") == "Один"
    assert cache.get("Two", "ru", "auto", "google") is None


def test_cache_key_separates_providers():
    """Смена провайдера Google→DeepL не должна отдавать чужие переводы."""
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    cache.put("Hello", "ru", "Привет (google)", "auto", "google")
    assert cache.get("Hello", "ru", "auto", "deepl") is None
    assert cache.get("Hello", "ru", "auto", "google") == "Привет (google)"


@patch("core.translator.TranslationJobThread._google_one")
def test_parallel_translate_preserves_order(mock_one):
    """PERF-004: параллельный перевод обязан сохранять порядок блоков."""
    mock_one.side_effect = lambda text: f"<{text}>"
    job = TranslationJobThread(
        blocks=[], target_lang="ru", source_lang="auto",
        provider="google", deepl_api_key="",
        cache=TranslationCache(max_size=10, ttl_seconds=3600),
    )
    texts = [f"line {i}" for i in range(10)]
    assert job._translate_google(texts) == [f"<line {i}>" for i in range(10)]
