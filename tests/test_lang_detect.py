"""
Регрессионные тесты для is_already_in_lang() из core.translator.
Баг: «программа переводит русский на русский» — фильтр должен отбрасывать
блоки уже на target-языке, чтобы overlay не рисовал поверх русского текста.
"""
from core.translator import is_already_in_lang


# ── Целевой язык: русский ───────────────────────────────────────────────

def test_pure_russian_is_target():
    assert is_already_in_lang("Привет, мир!", "ru") is True


def test_pure_english_is_not_target_ru():
    assert is_already_in_lang("Hello, world!", "ru") is False


def test_mixed_majority_russian():
    # ~78% кириллицы (11/14 букв) → считаем русским при threshold=0.6
    assert is_already_in_lang("Большой привет hi", "ru") is True


def test_mixed_majority_english():
    # ~21% кириллицы (3/14 букв) → НЕ русский, переводим
    assert is_already_in_lang("Hello world мир", "ru") is False


def test_only_digits_or_punct_returns_false():
    # Нет букв вообще — нечего детектить, не считаем «уже на target»
    assert is_already_in_lang("123 456 !!!", "ru") is False
    assert is_already_in_lang("", "ru") is False
    assert is_already_in_lang("   ", "ru") is False


def test_unknown_target_lang_returns_false():
    # Если язык не в _LANG_RANGES — функция не должна падать и не должна
    # отбрасывать блоки (безопаснее перевести, чем не перевести).
    assert is_already_in_lang("Привет", "xx") is False


def test_threshold_boundary():
    # 3 кириллических из 5 букв = 0.6 — ровно порог по умолчанию
    assert is_already_in_lang("абв cd", "ru", threshold=0.6) is True
    # При threshold=0.7 — недотягивает
    assert is_already_in_lang("абв cd", "ru", threshold=0.7) is False


# ── Целевой язык: английский (обратный кейс) ────────────────────────────

def test_english_target_skips_english_text():
    assert is_already_in_lang("Hello, world!", "en") is True


def test_english_target_translates_russian():
    assert is_already_in_lang("Привет, мир", "en") is False


# ── Edge cases ──────────────────────────────────────────────────────────

def test_none_lang_safe():
    # На случай если конфиг кривой — функция не должна падать
    assert is_already_in_lang("текст", "") is False
    assert is_already_in_lang("текст", None) is False  # type: ignore[arg-type]


def test_lang_with_locale_suffix():
    # "ru-RU" должно работать как "ru"
    assert is_already_in_lang("Привет", "ru-RU") is True
    assert is_already_in_lang("Hello", "en-US") is True
