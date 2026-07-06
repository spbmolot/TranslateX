"""
Translation worker.
Supports Google Translate (via deep-translator, free) and DeepL API.
Runs in a QThread to avoid blocking the event loop.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PyQt5.QtCore import QThread, pyqtSignal

from core.cache import TranslationCache
from core.ocr import TextBlock

logger = logging.getLogger(__name__)

# PERF-011: deep_translator.google зовёт модульный requests.get на каждый
# перевод — каждый вызов платит TCP+TLS handshake (~150-400мс). Session.get
# сигнатурно совместим с requests.get, поэтому подменяем модульную ссылку
# на Session с connection pooling — соединение к translate.google.com
# переиспользуется между вызовами и потоками (Session потокобезопасен).
_keepalive_lock = threading.Lock()
_keepalive_installed = False


def _install_keepalive_session() -> None:
    global _keepalive_installed
    if _keepalive_installed:
        return
    with _keepalive_lock:
        if _keepalive_installed:
            return
        try:
            import requests
            import deep_translator.google as _dtg
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=4, pool_maxsize=8
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            _dtg.requests = session
            _keepalive_installed = True
            logger.debug("Keep-alive session installed for deep_translator")
        except Exception as e:
            logger.debug(f"Keep-alive session not installed: {e}")


@dataclass
class TranslatedBlock:
    original: str
    translated: str
    bbox: List[List[int]]  # same format as TextBlock.bbox
    confidence: float
    # Цвет фона под блоком (r, g, b) для inpaint-отрисовки; копируется
    # из TextBlock.bg_rgb. None → overlay рисует fallback-обводку.
    bg_rgb: Optional[Tuple[int, int, int]] = None


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
        n = len(self._blocks)
        # None = перевода нет (fail или ещё не переведено). Фейлы НЕ кэшируем:
        # раньше при сбое сети оригинал записывался в кэш как «перевод» и
        # блок оставался непереведённым весь TTL (1 час) даже после
        # восстановления связи.
        translations: List[Optional[str]] = [None] * n
        to_translate: List[Tuple[int, str]] = []  # (index, text)

        # Check cache first
        for i, block in enumerate(self._blocks):
            cached = self._cache.get(
                block.text, self._target, self._source, self._provider
            )
            if cached is not None:
                translations[i] = cached
            else:
                to_translate.append((i, block.text))

        # Batch translate cache misses
        if to_translate:
            fresh = self._translate_batch([t for _, t in to_translate])
            failed = 0
            for (idx, original), translation in zip(to_translate, fresh):
                if translation is None:
                    failed += 1
                    continue  # показываем оригинал, кэш не трогаем — retry на следующем тике
                self._cache.put(
                    original, self._target, translation,
                    self._source, self._provider,
                )
                translations[idx] = translation
            if failed:
                logger.warning(
                    f"{failed}/{len(to_translate)} block(s) failed to translate "
                    "(originals shown, not cached — will retry next tick)"
                )

        translated_blocks = [
            TranslatedBlock(
                original=block.text,
                translated=tr if tr is not None else block.text,
                bbox=block.bbox,
                confidence=block.confidence,
                bg_rgb=getattr(block, "bg_rgb", None),
            )
            for block, tr in zip(self._blocks, translations)
        ]

        self.translation_complete.emit(translated_blocks)

    def _translate_batch(self, texts: List[str]) -> List[Optional[str]]:
        """
        Translate a list of texts, preserving order.
        Per-item None = перевод не удался (не кэшировать, показать оригинал).
        """
        if not texts:
            return []

        try:
            if self._provider == "deepl" and self._deepl_key:
                return self._translate_deepl(texts)
            else:
                return self._translate_google(texts)
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return [None] * len(texts)

    def _google_one(self, text: str) -> Optional[str]:
        """Перевод одной строки. Никогда не бросает; None = fail.
        Каждый вызов создаёт свой GoogleTranslator: translate() мутирует
        внутренний _url_params, инстанс нельзя разделять между потоками.
        Создание объекта дёшево; дорогой TLS-handshake решён keep-alive
        Session'ом (см. _install_keepalive_session)."""
        from deep_translator import GoogleTranslator
        try:
            translator = GoogleTranslator(source=self._source or "auto",
                                          target=self._target)
            translated = translator.translate(text)
            return translated if translated else None
        except Exception as e:
            logger.warning(f"Google translate failed for {text!r}: {e}")
            return None

    def _translate_google(self, texts: List[str]) -> List[Optional[str]]:
        _install_keepalive_session()
        # PERF-004: cache-miss блоки переводим параллельно. HTTP-запросы
        # I/O-bound, GIL не мешает; 4 воркера * keep-alive session — 5 блоков
        # идут ~за время одного запроса вместо суммы всех.
        if len(texts) == 1:
            return [self._google_one(texts[0])]
        with ThreadPoolExecutor(max_workers=min(4, len(texts))) as pool:
            return list(pool.map(self._google_one, texts))

    def _translate_deepl(self, texts: List[str]) -> List[Optional[str]]:
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
            return [None] * len(texts)

    def _map_deepl_lang(self, lang: str) -> str:
        """Map ISO language codes to DeepL target language codes."""
        mapping = {
            "ru": "RU", "en": "EN-US", "de": "DE", "fr": "FR",
            "es": "ES", "it": "IT", "ja": "JA", "zh": "ZH",
            "ko": "KO", "pt": "PT-PT", "nl": "NL", "pl": "PL",
        }
        return mapping.get(lang.lower(), lang.upper())
