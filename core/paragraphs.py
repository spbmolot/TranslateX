"""
Группировка OCR-строк в абзацы перед переводом.

Зачем: EasyOCR возвращает текст ПОСТРОЧНО. Если переводить каждую строку
отдельно, предложение, разорванное переносом («The quick brown fox /
jumps over the dog»), переводится как два бессвязных фрагмента — именно
это делает перевод «рваным». Яндекс/Google Lens переводят целые
предложения. Этот модуль склеивает соседние строки в логические абзацы
по геометрии, чтобы переводчик получал связный текст.

Двухфазный алгоритм (чистые функции, без Qt):
  Фаза A — склейка кусков ОДНОЙ визуальной строки (EasyOCR иногда режет
           строку на несколько боксов): вертикальное перекрытие ≥50% и
           небольшой горизонтальный зазор.
  Фаза B — стекование строк в абзацы: близкий вертикальный зазор
           (< ~0.8 высоты строки), пересечение по X, похожая высота
           шрифта. Пункты меню/подписи с большим межстрочным интервалом
           НЕ склеиваются — это отдельные элементы UI, не проза.

Пороги вынесены в константы модуля — их легко тюнить по реальным экранам.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.ocr import TextBlock
from utils.geometry import easyocr_bbox_to_rect

# ── Пороги (в долях высоты строки) ──────────────────────────────────────
# Фаза A: максимальный горизонтальный зазор между кусками одной строки.
# Больше — куски считаются разными элементами (колонки, кнопки тулбара).
MAX_INLINE_GAP_RATIO = 1.2
# Фаза A: минимальное вертикальное перекрытие, чтобы считать боксы одной строкой.
MIN_V_OVERLAP_RATIO = 0.5
# Фаза B: максимальный вертикальный зазор между строками абзаца.
# Типографский межстрочный интервал даёт зазор bbox'ов ~0.2-0.6 высоты;
# пункты меню/список стоят реже и не склеиваются.
MAX_LINE_GAP_RATIO = 0.8
# Фаза B: допустимое отношение высот строк (одинаковый кегль ± антиалиасинг).
MIN_HEIGHT_RATIO = 0.6
MAX_HEIGHT_RATIO = 1.7


@dataclass
class _Span:
    """Строка текста (возможно склеенная из нескольких OCR-боксов)."""
    parts: List[Tuple[int, str]]  # (x, text) — сортируем по x при сборке
    x: int
    y: int
    w: int
    h: int
    confidence: float

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def text(self) -> str:
        return " ".join(t for _, t in sorted(self.parts, key=lambda p: p[0]))


def _v_overlap(a: _Span, y: int, h: int) -> int:
    """Длина вертикального перекрытия span'а и отрезка [y, y+h]."""
    return min(a.bottom, y + h) - max(a.y, y)


def _h_gap(a: _Span, x: int, w: int) -> int:
    """Горизонтальный зазор между span'ом и отрезком [x, x+w] (<=0 если пересекаются)."""
    if x >= a.right:
        return x - a.right
    if x + w <= a.x:
        return a.x - (x + w)
    return 0


def _merge_into_lines(blocks: List[TextBlock]) -> List[_Span]:
    """Фаза A: собрать OCR-боксы в визуальные строки."""
    items = []
    for b in blocks:
        x, y, w, h = easyocr_bbox_to_rect(b.bbox)
        items.append((x, y, w, h, b))
    items.sort(key=lambda it: (it[0], it[1]))  # слева направо

    lines: List[_Span] = []
    for x, y, w, h, b in items:
        best: Optional[_Span] = None
        best_gap = None
        for line in lines:
            overlap = _v_overlap(line, y, h)
            if overlap < MIN_V_OVERLAP_RATIO * min(line.h, h):
                continue
            gap = _h_gap(line, x, w)
            if gap > MAX_INLINE_GAP_RATIO * max(line.h, h):
                continue
            if best is None or gap < best_gap:
                best, best_gap = line, gap
        if best is None:
            lines.append(_Span(parts=[(x, b.text)], x=x, y=y, w=w, h=h,
                               confidence=b.confidence))
        else:
            best.parts.append((x, b.text))
            nx = min(best.x, x)
            ny = min(best.y, y)
            best.w = max(best.right, x + w) - nx
            best.h = max(best.bottom, y + h) - ny
            best.x, best.y = nx, ny
            best.confidence = min(best.confidence, b.confidence)
    return lines


@dataclass
class _Paragraph:
    spans: List[_Span] = field(default_factory=list)
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    @property
    def last(self) -> _Span:
        return self.spans[-1]

    def h_ref(self) -> float:
        """Опорная высота строки — медиана высот span'ов."""
        hs = sorted(s.h for s in self.spans)
        return float(hs[len(hs) // 2])

    def add(self, span: _Span) -> None:
        if not self.spans:
            self.x, self.y, self.w, self.h = span.x, span.y, span.w, span.h
        else:
            nx = min(self.x, span.x)
            ny = min(self.y, span.y)
            self.w = max(self.x + self.w, span.right) - nx
            self.h = max(self.y + self.h, span.bottom) - ny
            self.x, self.y = nx, ny
        self.spans.append(span)


def _join_line_texts(texts: List[str]) -> str:
    """Склейка строк абзаца: перенос слова через дефис соединяем без пробела."""
    out = ""
    for t in texts:
        t = t.strip()
        if not t:
            continue
        if not out:
            out = t
        elif out.endswith("-") and len(out) >= 2 and out[-2].isalpha():
            out = out[:-1] + t
        else:
            out = out + " " + t
    return out


def _merge_into_paragraphs(lines: List[_Span]) -> List[_Paragraph]:
    """Фаза B: стекование строк в абзацы сверху вниз."""
    lines = sorted(lines, key=lambda s: (s.y, s.x))
    paragraphs: List[_Paragraph] = []
    for span in lines:
        best: Optional[_Paragraph] = None
        best_gap = None
        for para in paragraphs:
            h_ref = para.h_ref()
            ratio = span.h / h_ref if h_ref > 0 else 1.0
            if not (MIN_HEIGHT_RATIO <= ratio <= MAX_HEIGHT_RATIO):
                continue
            gap = span.y - para.last.bottom
            if not (-0.3 * h_ref <= gap <= MAX_LINE_GAP_RATIO * h_ref):
                continue
            # Требуем пересечение по X с bbox абзаца — колонки не склеиваем.
            if _h_gap(_Span([], para.x, para.y, para.w, para.h, 1.0),
                      span.x, span.w) > 0:
                continue
            if best is None or gap < best_gap:
                best, best_gap = para, gap
        if best is None:
            best = _Paragraph()
            paragraphs.append(best)
        best.add(span)
    return paragraphs


def group_paragraphs(blocks: List[TextBlock]) -> List[TextBlock]:
    """
    Главная точка входа: список построчных TextBlock → список абзацев
    (тоже TextBlock: text = склеенный текст, bbox = объединённый
    прямоугольник, confidence = минимум по строкам).
    """
    if len(blocks) <= 1:
        return list(blocks)
    lines = _merge_into_lines(blocks)
    paragraphs = _merge_into_paragraphs(lines)

    result: List[TextBlock] = []
    for p in paragraphs:
        text = _join_line_texts([s.text() for s in p.spans])
        if not text:
            continue
        bbox = [
            [p.x, p.y],
            [p.x + p.w, p.y],
            [p.x + p.w, p.y + p.h],
            [p.x, p.y + p.h],
        ]
        conf = min(s.confidence for s in p.spans)
        result.append(TextBlock(text=text, bbox=bbox, confidence=conf))
    return result
