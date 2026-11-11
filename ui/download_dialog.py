"""
Диалог загрузки локальных моделей: прогресс, отмена, докачка при повторе.
"""
from __future__ import annotations

import logging
import threading
from typing import List

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox, QProgressDialog

from core import models

logger = logging.getLogger(__name__)


class _Worker(QThread):
    progress = pyqtSignal(str, int, int)   # название, скачано МБ*10, всего МБ*10
    failed = pyqtSignal(str)

    def __init__(self, components: List[models.Component], parent=None):
        super().__init__(parent)
        self._components = components
        self.cancel = threading.Event()
        self.ok = False

    def run(self) -> None:
        try:
            for comp in self._components:
                def cb(done: int, total: int, _title=comp.title) -> None:
                    # int-сигнал Qt 32-битный: байты гигабайтных файлов в него
                    # не влезают, передаём десятые доли мегабайта.
                    self.progress.emit(_title, done // 100_000, max(1, total // 100_000))
                models.install(comp, cb, self.cancel)
            self.ok = True
        except models.DownloadCancelled:
            logger.info("Загрузка моделей отменена пользователем")
        except Exception as e:
            logger.warning(f"Загрузка моделей не удалась: {e}")
            self.failed.emit(str(e))


def download_components(parent, components: List[models.Component]) -> bool:
    """Скачивает недостающие компоненты с модальным прогрессом.
    True — всё установлено. Прерванная загрузка докачивается при повторе."""
    missing = [c for c in components if not c.is_installed()]
    if not missing:
        return True

    dialog = QProgressDialog("Подготовка…", "Отмена", 0, 1000, parent)
    dialog.setWindowTitle("TranslateX — загрузка моделей")
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumWidth(420)

    worker = _Worker(missing, dialog)
    errors: List[str] = []

    def on_progress(title: str, done: int, total: int) -> None:
        dialog.setMaximum(total)
        dialog.setValue(min(done, total))
        dialog.setLabelText(
            f"{title}\n{done / 10:.0f} из {total / 10:.0f} МБ")

    worker.progress.connect(on_progress)
    worker.failed.connect(errors.append)
    worker.finished.connect(dialog.accept)
    dialog.canceled.connect(worker.cancel.set)
    worker.start()
    dialog.exec_()
    worker.cancel.set()   # на случай закрытия окна крестиком
    worker.wait()

    if errors:
        QMessageBox.warning(
            parent, "TranslateX — загрузка моделей",
            "Не удалось скачать: " + errors[0] +
            "\n\nУже скачанная часть сохранена — при повторе загрузка продолжится.")
    return worker.ok
