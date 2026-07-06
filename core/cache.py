"""
Translation cache with LRU eviction and disk persistence.
Thread-safe for concurrent reads; writes are serialized via a lock.
"""
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Optional, Tuple

from core.paths import cache_path, user_data_dir

logger = logging.getLogger(__name__)

_CACHE_FILE = cache_path()


def _normalize(text: str) -> str:
    """Normalize text for cache key stability across minor OCR variation."""
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


class TranslationCache:
    def __init__(self, max_size: int = 500, ttl_seconds: int = 3600):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._data: OrderedDict[Tuple[str, ...], Tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.load()

    def _key(self, text: str, target_lang: str,
             source_lang: str = "auto", provider: str = "google") -> Tuple[str, ...]:
        # Ключ включает source и provider: перевод en→ru у Google и DeepL
        # различается, и смена провайдера/источника не должна отдавать
        # устаревшие записи другого провайдера.
        return (
            _normalize(text),
            target_lang.lower(),
            (source_lang or "auto").lower(),
            (provider or "google").lower(),
        )

    def get(self, text: str, target_lang: str,
            source_lang: str = "auto", provider: str = "google") -> Optional[str]:
        key = self._key(text, target_lang, source_lang, provider)
        with self._lock:
            if key not in self._data:
                return None
            translation, ts = self._data[key]
            if time.time() - ts > self._ttl:
                del self._data[key]
                return None
            # LRU: move to end
            self._data.move_to_end(key)
            return translation

    def put(self, text: str, target_lang: str, translation: str,
            source_lang: str = "auto", provider: str = "google") -> None:
        key = self._key(text, target_lang, source_lang, provider)
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (translation, time.time())
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def save(self) -> None:
        try:
            user_data_dir()  # гарантируем существование %APPDATA%\TranslateX
            serializable = {
                "||".join(k): list(v)
                for k, v in self._data.items()
            }
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
            logger.debug(f"Cache saved: {len(self._data)} entries")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

    def load(self) -> None:
        if not _CACHE_FILE.exists():
            return
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            for composite_key, value in raw.items():
                parts = composite_key.split("||")
                # 4 части: text||target||source||provider. Записи старого
                # 2-компонентного формата отбрасываем (разовый сброс кэша
                # при обновлении — дешевле, чем неверные попадания).
                if len(parts) != 4:
                    continue
                translation, ts = value
                if now - ts <= self._ttl:
                    self._data[tuple(parts)] = (translation, ts)
            logger.debug(f"Cache loaded: {len(self._data)} entries")
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
