"""
Translation worker.
Supports Google Translate (via deep-translator, free) and DeepL API.
Runs in a QThread to avoid blocking the event loop.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PyQt5.QtCore import QThread, pyqtSignal

from core.cache import TranslationCache
from core.ocr import TextBlock

logger = logging.getLogger(__name__)


@dataclass
class TranslatedBlock:
    original: str
    translated: str
    bbox: List[List[int]]  # same format as TextBlock.bbox
    confidence: float


# Диапазоны Unicode для определения языка по тексту.
# Используем для skip'а блоков, которые уже на target-языке (нечего переводить).
_LANG_RANGES = {
    "ru": [(0x0400, 0x04FF), (0x0500, 0x052F)],          # Cyrillic + Cyrillic Suppl.
    "uk": [(0x0400, 0x04FF)],
    "be": [(0x0400, 0x04FF)],
    "en": [(0x0041, 0x005A), (0x0061, 0x007A)],          # Basic Latin letters
    "de": [(0x0041, 0x007A), (0x00C0, 0x00FF)],
    "fr": [(0x0041, 0x007A), (0x00C0, 0x00FF)],
    "es": [(0x0041, 0x007A), (0x00C0, 0x00FF)],
    "it": [(0x0041, 0x007A), (0x00C0, 0x00FF)],
    "ja": [(0x3040, 0x309F), (0x30A0, 0x30FF), (0x4E00, 0x9FFF)],
    "zh": [(0x4E00, 0x9FFF)],
    "ko": [(0xAC00, 0xD7AF)],
}


def is_already_in_lang(text: str, lang: str, threshold: float = 0.6) -> bool:
    """
    Возвращает True, если ≥threshold-доли буквенных символов текста принадлежат
    диапазону Unicode целевого языка. Цифры/пробелы/пунктуация игнорируются.
    """
    ranges = _LANG_RANGES.get((lang or "").lower()[:2])
    if not ranges:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    matched = 0
    for c in letters:
        cp = ord(c)
        for lo, hi in ranges:
            if lo <= cp <= hi:
                matched += 1
                break
    return (matched / len(letters)) >= threshold


class TranslationJobThread(QThread):
    """
    One-shot thread for translating a batch of TextBlocks.
    """
    translation_complete = pyqtSignal(object)  # list[TranslatedBlock]

    def __init__(
        self,
        blocks: List[TextBlock],
        target_lang: str,
        source_lang: str,
        provider: str,
        deepl_api_key: str,
        cache: TranslationCache,
        parent=None,
    ):
        super().__init__(parent)
        self._blocks = blocks
        self._target = target_lang
        self._source = source_lang
        self._provider = provider
        self._deepl_key = deepl_api_key
        self._cache = cache

    def run(self) -> None:
        results = []
        texts_to_translate: List[Tuple[int, str]] = []  # (index, text)

        # Check cache first
        for i, block in enumerate(self._blocks):
            cached = self._cache.get(block.text, self._target)
            if cached is not None:
                results.append((i, cached))
            else:
                texts_to_translate.append((i, block.text))

        # Batch translate cache misses
        if texts_to_translate:
            translations = self._translate_batch(
                [t for _, t in texts_to_translate]
            )
            for (idx, original), translation in zip(texts_to_translate, translations):
                self._cache.put(original, self._target, translation)
                results.append((idx, translation))

        # Re-sort by original index and build output
        results.sort(key=lambda x: x[0])
        translated_blocks = []
        for i, (_, translation) in enumerate(results):
            block = self._blocks[i]
            translated_blocks.append(TranslatedBlock(
                original=block.text,
                translated=translation,
                bbox=block.bbox,
                confidence=block.confidence,
            ))

        self.translation_complete.emit(translated_blocks)

    def _translate_batch(self, texts: List[str]) -> List[str]:
        """Translate a list of texts, returning translations in same order."""
        if not texts:
            return []

        try:
            if self._provider == "deepl" and self._deepl_key:
                return self._translate_deepl(texts)
            else:
                return self._translate_google(texts)
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return texts  # Fallback: return originals

    def _translate_google(self, texts: List[str]) -> List[str]:
        from deep_translator import GoogleTranslator
        source = self._source if self._source != "auto" else "auto"
        translator = GoogleTranslator(source=source, target=self._target)
        results = []
        for text in texts:
            try:
                translated = translator.translate(text)
                results.append(translated or text)
            except Exception as e:
                logger.warning(f"Google translate failed for {text!r}: {e}")
                results.append(text)
        return results

    def _translate_deepl(self, texts: List[str]) -> List[str]:
        import deepl
        client = deepl.Translator(self._deepl_key)
        source = self._source.upper() if self._source != "auto" else None
        target = self._map_deepl_lang(self._target)
        try:
            result = client.translate_text(texts, source_lang=source, target_lang=target)
            if isinstance(result, list):
                return [r.text for r in result]
            return [result.text]
        except Exception as e:
            logger.error(f"DeepL batch translate failed: {e}")
            return texts

    def _map_deepl_lang(self, lang: str) -> str:
        """Map ISO language codes to DeepL target language codes."""
        mapping = {
            "ru": "RU", "en": "EN-US", "de": "DE", "fr": "FR",
            "es": "ES", "it": "IT", "ja": "JA", "zh": "ZH",
            "ko": "KO", "pt": "PT-PT", "nl": "NL", "pl": "PL",
        }
        return mapping.get(lang.lower(), lang.upper())
