"""
Translation cache with LRU eviction and disk persistence.
Thread-safe for concurrent reads; writes are serialized via a lock.
"""
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_APPDATA = Path(os.environ.get("APPDATA", "~")).expanduser() / "TranslateX"
_CACHE_FILE = _APPDATA / "cache.json"


def _normalize(text: str) -> str:
    """Normalize text for cache key stability across minor OCR variation."""
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


class TranslationCache:
    def __init__(self, max_size: int = 500, ttl_seconds: int = 3600):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._data: OrderedDict[Tuple[str, str], Tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.load()

    def _key(self, text: str, target_lang: str) -> Tuple[str, str]:
        return (_normalize(text), target_lang.lower())

    def get(self, text: str, target_lang: str) -> Optional[str]:
        key = self._key(text, target_lang)
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

    def put(self, text: str, target_lang: str, translation: str) -> None:
        key = self._key(text, target_lang)
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (translation, time.time())
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def save(self) -> None:
        try:
            _APPDATA.mkdir(parents=True, exist_ok=True)
            serializable = {
                f"{k[0]}||{k[1]}": list(v)
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
                parts = composite_key.split("||", 1)
                if len(parts) != 2:
                    continue
                text_key, lang = parts
                translation, ts = value
                if now - ts <= self._ttl:
                    self._data[(text_key, lang)] = (translation, ts)
            logger.debug(f"Cache loaded: {len(self._data)} entries")
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
