"""
HistoryPanel — диалог истории переводов TranslateX.

Подписывается на сигнал translation_ready AppController через метод
add_entries() и показывает список последних N уникальных переводов
в формате «оригинал → перевод» с timestamp'ом.

Функции:
- Clear     — очистить историю
- Export    — сохранить в .txt (через QFileDialog)
- Close     — скрыть панель (не закрывать QApplication)

Панель non-modal: остаётся поверх окон, не блокирует рамку.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QKeySequence
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Максимум записей в памяти; при превышении вытесняем старые.
_MAX_ENTRIES = 500


class HistoryPanel(QDialog):
    """Окно «История переводов»."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("История переводов — TranslateX")
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint
        )
        self.resize(640, 480)

        # (timestamp_iso, original, translated) — дедуп по (original, translated)
        self._entries: Deque[Tuple[str, str, str]] = deque(maxlen=_MAX_ENTRIES)
        self._seen_keys: set[Tuple[str, str]] = set()

        self._build_ui()

        # Esc — скрыть панель (не завершать приложение)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.hide)

    # ── UI ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Поле фильтра
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Фильтр:"))
        self._filter_edit = QLineEdit(self)
        self._filter_edit.setPlaceholderText("по оригиналу или переводу…")
        self._filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_edit, 1)
        root.addLayout(filter_row)

        # Список записей
        self._list = QListWidget(self)
        self._list.setAlternatingRowColors(True)
        # QFont принимает ОДНО имя семейства, не CSS-стек. Список альтернатив
        # задаём через setFamilies(); styleHint — общий fallback на моноширинный.
        mono = QFont()
        mono.setFamilies(["Consolas", "Courier New", "monospace"])
        mono.setStyleHint(QFont.Monospace)
        self._list.setFont(mono)
        root.addWidget(self._list, 1)

        # Счётчик
        self._counter_label = QLabel("0 записей", self)
        self._counter_label.setStyleSheet("color: #888;")
        root.addWidget(self._counter_label)

        # Кнопки
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._btn_clear = QPushButton("Очистить", self)
        self._btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self._btn_clear)

        self._btn_export = QPushButton("Экспорт в .txt", self)
        self._btn_export.clicked.connect(self._on_export)
        btn_row.addWidget(self._btn_export)

        self._btn_close = QPushButton("Закрыть", self)
        self._btn_close.clicked.connect(self.hide)
        btn_row.addWidget(self._btn_close)

        root.addLayout(btn_row)

    # ── Public API ─────────────────────────────────────────────────────

    def add_entries(self, translated_blocks, _region) -> None:
        """
        Слот для сигнала AppController.translation_ready(list, QRect).
        Принимает список TranslatedBlock'ов, добавляет новые уникальные
        записи в историю. Region не используется, но обязателен по signature.

        Без @pyqtSlot — декоратор pyqtSlot(object, object) несовместим
        с типизированным сигналом pyqtSignal(object, QRect) и приводит
        к TypeError при connect.
        """
        if not translated_blocks:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        added = 0
        for b in translated_blocks:
            try:
                original = (getattr(b, "original", None) or "").strip()
                translated = (getattr(b, "translated", None) or "").strip()
            except Exception:
                continue
            if not original or not translated or original == translated:
                continue
            key = (original, translated)
            if key in self._seen_keys:
                continue
            # Если deque заполнен — append вытеснит самую старую запись.
            # Снимаем её ключ из _seen_keys, иначе set растёт неограниченно
            # и вытесненная пара уже никогда не сможет добавиться повторно.
            if len(self._entries) == self._entries.maxlen:
                old_ts, old_orig, old_tr = self._entries[0]
                self._seen_keys.discard((old_orig, old_tr))
            self._seen_keys.add(key)
            self._entries.append((ts, original, translated))
            added += 1
        if added:
            self._rebuild_list()

    def entries(self) -> List[Tuple[str, str, str]]:
        """Read-only снимок истории (для тестов/экспорта)."""
        return list(self._entries)

    # ── Handlers ───────────────────────────────────────────────────────

    def _apply_filter(self, _text: str) -> None:
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        needle = self._filter_edit.text().strip().lower()
        self._list.clear()
        for ts, original, translated in self._entries:
            if needle and needle not in original.lower() and needle not in translated.lower():
                continue
            # Короткий preview, чтобы строки не разъезжались
            line = f"[{ts}]  {original}  →  {translated}"
            item = QListWidgetItem(line)
            item.setToolTip(f"{original}\n\n→\n\n{translated}")
            self._list.addItem(item)
        self._counter_label.setText(
            f"{self._list.count()} показано / {len(self._entries)} всего"
        )

    def _on_clear(self) -> None:
        if not self._entries:
            return
        reply = QMessageBox.question(
            self,
            "Очистить историю",
            f"Удалить {len(self._entries)} записей безвозвратно?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._entries.clear()
            self._seen_keys.clear()
            self._rebuild_list()
            logger.info("Translation history cleared by user")

    def _on_export(self) -> None:
        if not self._entries:
            QMessageBox.information(self, "Экспорт", "История пуста.")
            return
        default_name = datetime.now().strftime("translatex-history-%Y%m%d-%H%M.txt")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт истории",
            default_name,
            "Текстовые файлы (*.txt);;Все файлы (*.*)",
        )
        if not path_str:
            return
        try:
            path = Path(path_str)
            with path.open("w", encoding="utf-8") as f:
                f.write("# TranslateX — история переводов\n")
                f.write(f"# Экспортировано: {datetime.now().isoformat(timespec='seconds')}\n")
                f.write(f"# Записей: {len(self._entries)}\n\n")
                for ts, original, translated in self._entries:
                    f.write(f"[{ts}]\n{original}\n→\n{translated}\n\n")
            logger.info(f"History exported to {path} ({len(self._entries)} entries)")
            QMessageBox.information(
                self, "Экспорт",
                f"Сохранено {len(self._entries)} записей в:\n{path}",
            )
        except Exception as e:
            logger.exception(f"Export failed: {e}")
            QMessageBox.critical(self, "Ошибка экспорта", str(e))
