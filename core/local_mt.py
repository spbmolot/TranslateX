"""
Быстрый локальный перевод: OPUS-MT через CTranslate2 (только CPU, без torch).

Одна модель = одна языковая пара (~80 МБ, ~250 МБ ОЗУ). Если прямой пары
нет, но есть обе половины через английский — переводим в два шага.
Работает без интернета; текст с экрана не покидает компьютер.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import models

logger = logging.getLogger(__name__)


class LocalModelMissing(Exception):
    """Нужная модель/движок не установлены — их надо скачать в Настройках."""


# Диапазоны письменностей для угадывания исходного языка при source=auto.
_SCRIPT_LANGS: List[Tuple[str, List[Tuple[int, int]]]] = [
    ("ja", [(0x3040, 0x30FF)]),                 # кана — однозначно японский
    ("ko", [(0xAC00, 0xD7AF), (0x1100, 0x11FF)]),
    ("zh", [(0x4E00, 0x9FFF)]),
    ("ru", [(0x0400, 0x052F)]),
    ("ar", [(0x0600, 0x06FF)]),
]


def guess_source_lang(text: str, default: str = "en") -> str:
    """Грубое определение языка по письменности. Латиница → default (en):
    различать европейские языки без модели не пытаемся."""
    letters = [ord(c) for c in text if c.isalpha()]
    if not letters:
        return default
    for lang, ranges in _SCRIPT_LANGS:
        hits = sum(1 for cp in letters if any(lo <= cp <= hi for lo, hi in ranges))
        # Для японского достаточно заметной доли каны среди иероглифов
        threshold = 0.2 if lang == "ja" else 0.5
        if hits / len(letters) >= threshold:
            return lang
    return default


_SENT_SPLIT = re.compile(r"(?<=[.!?…。！？])\s+")


def split_sentences(text: str) -> List[str]:
    """Малые модели теряют хвост многофразового входа («Hey! What's up?» →
    «Привет!»). Переводим по предложениям и склеиваем обратно."""
    parts = [p for p in _SENT_SPLIT.split(text.strip()) if p]
    return parts or [text]


class _PairModel:
    def __init__(self, src: str, tgt: str):
        comp = models.opus_model(src, tgt)
        if not comp.is_installed():
            raise LocalModelMissing(
                f"быстрая модель {src}→{tgt} не установлена")
        import ctranslate2
        import sentencepiece as spm
        d = comp.directory()
        threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        self._translator = ctranslate2.Translator(
            str(d), device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=threads)
        self._src_sp = spm.SentencePieceProcessor(model_file=str(d / "source.spm"))
        self._tgt_sp = spm.SentencePieceProcessor(model_file=str(d / "target.spm"))
        logger.info(f"OPUS-MT {src}→{tgt} загружена ({threads} потоков)")

    def translate(self, sentences: List[str]) -> List[str]:
        tokens = [self._src_sp.encode(s, out_type=str) + ["</s>"] for s in sentences]
        results = self._translator.translate_batch(
            tokens, beam_size=2, max_batch_size=16,
            max_decoding_length=256, repetition_penalty=1.1)
        return [self._tgt_sp.decode(r.hypotheses[0]) for r in results]


_lock = threading.Lock()
_loaded: Dict[Tuple[str, str], _PairModel] = {}


def route(src: str, tgt: str, root: Optional[Path] = None) -> Optional[List[Tuple[str, str]]]:
    """Маршрут перевода по УСТАНОВЛЕННЫМ моделям: прямая пара или через
    английский. None — нужных моделей нет."""
    if models.opus_model(src, tgt, root).is_installed():
        return [(src, tgt)]
    if src != "en" and tgt != "en" and (
        models.opus_model(src, "en", root).is_installed()
        and models.opus_model("en", tgt, root).is_installed()
    ):
        return [(src, "en"), ("en", tgt)]
    return None


def _model(src: str, tgt: str) -> _PairModel:
    key = (src, tgt)
    m = _loaded.get(key)
    if m is None:
        m = _loaded[key] = _PairModel(src, tgt)
    return m


def translate(texts: List[str], source_lang: str, target_lang: str) -> List[Optional[str]]:
    """Перевод списка блоков. Бросает LocalModelMissing, если модели нет.
    Блок, чей язык совпал с целевым, возвращается как есть."""
    tgt = (target_lang or "ru").lower()[:2]
    out: List[Optional[str]] = [None] * len(texts)
    # Группируем блоки по исходному языку (при auto он свой у каждого блока)
    by_src: Dict[str, List[int]] = {}
    for i, text in enumerate(texts):
        src = (source_lang or "auto").lower()[:2]
        if src == "au":
            src = guess_source_lang(text)
        if src == tgt:
            out[i] = text
            continue
        by_src.setdefault(src, []).append(i)

    with _lock:
        for src, indexes in by_src.items():
            path = route(src, tgt)
            if path is None:
                raise LocalModelMissing(
                    f"быстрая модель {src}→{tgt} не установлена")
            # Плоский список предложений всех блоков — один батч на шаг
            spans: List[Tuple[int, int]] = []
            sentences: List[str] = []
            for i in indexes:
                parts = split_sentences(texts[i])
                spans.append((len(sentences), len(parts)))
                sentences.extend(parts)
            for a, b in path:
                sentences = _model(a, b).translate(sentences)
            for i, (start, count) in zip(indexes, spans):
                joined = " ".join(s.strip() for s in sentences[start:start + count])
                out[i] = joined.strip() or None
    return out


def unload() -> None:
    """Освободить память (смена провайдера, выход)."""
    with _lock:
        _loaded.clear()
