"""Тесты группировки OCR-строк в абзацы (core/paragraphs.py)."""
import pytest

from core.ocr import TextBlock
from core.paragraphs import group_paragraphs, _join_line_texts


def _block(text, x, y, w, h, conf=0.9):
    return TextBlock(
        text=text,
        bbox=[[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
        confidence=conf,
    )


def _rect(block):
    xs = [p[0] for p in block.bbox]
    ys = [p[1] for p in block.bbox]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


# ── Базовые случаи ──────────────────────────────────────────────────────

def test_empty_and_single_passthrough():
    assert group_paragraphs([]) == []
    b = _block("Hello", 0, 0, 100, 20)
    out = group_paragraphs([b])
    assert len(out) == 1 and out[0].text == "Hello"


def test_two_stacked_lines_merge_into_paragraph():
    """Две строки одного абзаца (зазор ~0.3 высоты) → один блок."""
    b1 = _block("The quick brown fox", 10, 100, 300, 20)
    b2 = _block("jumps over the dog", 10, 126, 280, 20)  # зазор 6px = 0.3h
    out = group_paragraphs([b1, b2])
    assert len(out) == 1
    assert out[0].text == "The quick brown fox jumps over the dog"
    x, y, w, h = _rect(out[0])
    assert (x, y) == (10, 100)
    assert w == 300 and h == 46  # union bbox


def test_hyphenated_line_break_joins_word():
    b1 = _block("automatic transla-", 10, 100, 250, 20)
    b2 = _block("tion of the screen", 10, 125, 240, 20)
    out = group_paragraphs([b1, b2])
    assert len(out) == 1
    assert "translation" in out[0].text


def test_distant_lines_stay_separate():
    """Зазор 2× высоты строки (меню, список) — НЕ абзац."""
    b1 = _block("File", 10, 100, 60, 20)
    b2 = _block("Edit", 10, 160, 60, 20)  # зазор 40px = 2h
    out = group_paragraphs([b1, b2])
    assert len(out) == 2


def test_columns_stay_separate():
    """Две колонки без пересечения по X не склеиваются вертикально."""
    left1 = _block("Left column one", 10, 100, 200, 20)
    left2 = _block("left column two", 10, 126, 200, 20)
    right1 = _block("Right column one", 400, 100, 200, 20)
    right2 = _block("right column two", 400, 126, 200, 20)
    out = group_paragraphs([left1, right1, left2, right2])
    assert len(out) == 2
    texts = sorted(b.text for b in out)
    assert texts[0] == "Left column one left column two"
    assert texts[1] == "Right column one right column two"


def test_split_visual_line_joined_in_x_order():
    """EasyOCR разрезал одну строку на два бокса → склейка по X."""
    b2 = _block("world", 130, 100, 80, 20)   # правый кусок (зазор 10px)
    b1 = _block("Hello", 10, 101, 110, 19)   # левый кусок
    out = group_paragraphs([b2, b1])
    assert len(out) == 1
    assert out[0].text == "Hello world"


def test_far_apart_on_same_row_not_joined():
    """Кнопки на одной строке с большим зазором — отдельные элементы."""
    b1 = _block("OK", 10, 100, 40, 20)
    b2 = _block("Cancel", 300, 100, 80, 20)  # зазор 250px >> 1.2h
    out = group_paragraphs([b1, b2])
    assert len(out) == 2


def test_heading_not_merged_with_body():
    """Заголовок (h=36) и текст (h=16): разница кеглей → раздельно."""
    head = _block("Big Heading", 10, 50, 300, 36)
    body = _block("Body text line", 10, 92, 280, 16)
    out = group_paragraphs([head, body])
    assert len(out) == 2


def test_confidence_is_min_of_members():
    b1 = _block("line one", 10, 100, 200, 20, conf=0.9)
    b2 = _block("line two", 10, 126, 200, 20, conf=0.6)
    out = group_paragraphs([b1, b2])
    assert len(out) == 1
    assert out[0].confidence == pytest.approx(0.6)


def test_three_line_paragraph():
    b1 = _block("Line one of text", 10, 100, 300, 20)
    b2 = _block("line two of text", 10, 126, 290, 20)
    b3 = _block("line three ends.", 10, 152, 250, 20)
    out = group_paragraphs([b1, b2, b3])
    assert len(out) == 1
    assert out[0].text == "Line one of text line two of text line three ends."


# ── _join_line_texts ────────────────────────────────────────────────────

def test_join_skips_empty_and_strips():
    assert _join_line_texts(["  a ", "", "b"]) == "a b"


def test_join_hyphen_requires_letter_before():
    # "5-" — не перенос слова (цифра), дефис сохраняем
    assert _join_line_texts(["item 5-", "10"]) == "item 5- 10"
