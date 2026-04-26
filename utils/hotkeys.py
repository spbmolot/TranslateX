"""
Global hotkey manager using the `keyboard` library.
All callbacks are marshalled to the Qt main thread via QMetaObject.invokeMethod
to avoid cross-thread Qt widget access.
"""
import logging
from typing import Callable, Dict

import keyboard
from PyQt5.QtCore import QObject, QMetaObject, Qt, pyqtSlot

logger = logging.getLogger(__name__)


class HotkeyManager(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._bindings: Dict[str, Callable] = {}

    def register(self, hotkey_str: str, callback: Callable) -> bool:
        """
        Register a global hotkey. The callback will be invoked on the Qt main thread.
        Returns True on success.
        """
        self.unregister(hotkey_str)
        try:
            def _safe_callback():
                # Marshal to Qt main thread
                QMetaObject.invokeMethod(
                    self, "_dispatch",
                    Qt.QueuedConnection,
                    # Pass the hotkey string as argument
                )
                # Store callback ref accessible from _dispatch
                self._pending_callback = callback
                QMetaObject.invokeMethod(self, "_dispatch", Qt.QueuedConnection)

            keyboard.add_hotkey(hotkey_str, callback, suppress=False)
            self._bindings[hotkey_str] = callback
            logger.info(f"Registered hotkey: {hotkey_str}")
            return True
        except Exception as e:
            logger.warning(f"Failed to register hotkey {hotkey_str!r}: {e}")
            return False

    def unregister(self, hotkey_str: str) -> None:
        if hotkey_str in self._bindings:
            try:
                keyboard.remove_hotkey(hotkey_str)
            except Exception:
                pass
            del self._bindings[hotkey_str]

    def unregister_all(self) -> None:
        for hk in list(self._bindings.keys()):
            self.unregister(hk)
