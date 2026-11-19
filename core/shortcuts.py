"""Global shortcut defaults, migration and parsing of Windows key combinations."""
from typing import Tuple

HOTKEY_DEFAULTS = {
    "hotkey_select": "ctrl+alt+f8",
    "hotkey_toggle": "",
    "hotkey_pause": "",
    "hotkey_copy": "",
    "hotkey_fullscreen": "",
}

# An old profile can contain just the two bindings originally shipped in
# config.ini, or all five after a visit to Settings. Preserve custom choices.
_LEGACY_DEFAULTS = {
    "hotkey_select": "ctrl+shift+t",
    "hotkey_toggle": "ctrl+shift+p",
    "hotkey_pause": "ctrl+shift+space",
    "hotkey_copy": "ctrl+shift+c",
    "hotkey_fullscreen": "ctrl+shift+f",
}


def migrate_hotkeys(config):
    if not config.has_section("general"):
        config.add_section("general")
    settings = config["general"]
    if "hotkey_profile" in settings:
        return
    for key, old in _LEGACY_DEFAULTS.items():
        if settings.get(key, "").strip().lower() == old:
            settings[key] = HOTKEY_DEFAULTS[key]
    settings["hotkey_profile"] = "2"


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "alt": MOD_ALT,
    "win": MOD_WIN, "windows": MOD_WIN, "cmd": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "page up": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "page down": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pause": 0x13, "print screen": 0x2C, "printscreen": 0x2C, "prtsc": 0x2C,
    "plus": 0xBB, "minus": 0xBD, "-": 0xBD, "_": 0xBD,
}


def parse_hotkey(text: str) -> Tuple[int, int]:
    """
    'ctrl+shift+t' → (modifiers, virtual_key). Формат прежний (как у
    библиотеки keyboard): сегменты через '+', регистр и пробелы не важны.
    Бросает ValueError с понятным описанием, если строку разобрать нельзя.
    """
    parts = [p.strip().lower() for p in (text or "").split("+")]
    if not parts or any(not p for p in parts):
        raise ValueError("пустое сочетание или лишний '+'")
    mods = 0
    vk = None
    for part in parts:
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
            continue
        if vk is not None:
            raise ValueError("в сочетании может быть только одна основная клавиша")
        if len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())  # VK-коды 0-9 и A-Z совпадают с ASCII
        elif part[0] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        else:
            raise ValueError(f"неизвестная клавиша '{part}'")
    if vk is None:
        raise ValueError("нет основной клавиши (только модификаторы)")
    if not mods and not (0x70 <= vk <= 0x87):
        # Одиночная буква/цифра как глобальный хоткей сделала бы
        # невозможным её набор в любой программе.
        raise ValueError("нужен модификатор (ctrl/alt/shift/win)")
    return mods, vk
