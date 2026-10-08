"""Terminal glyphs, gauges, and the ORION palette."""

from __future__ import annotations

import os
import sys
import time

from rich.text import Text
from rich.theme import Theme

CLOCKS = "🕛🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚"
BLOCKS = "▁▂▃▄▅▆▇█"


def _utf8_ok() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "█░🕛▸●○◆─┃".encode(encoding)
    except Exception:
        return False
    return True


def _light_background() -> bool:
    """True when the console background is white, so body text can invert."""
    fgbg = os.environ.get("COLORFGBG", "")
    if fgbg:
        try:
            background = int(fgbg.split(";")[-1])
        except ValueError:
            background = -1
        if background >= 7:
            return True
    if os.name != "nt":
        return False
    try:
        import ctypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", ctypes.c_short),
                ("Top", ctypes.c_short),
                ("Right", ctypes.c_short),
                ("Bottom", ctypes.c_short),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", ctypes.c_ushort),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not ctypes.windll.kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return False
        background = (info.wAttributes >> 4) & 0x0F
        return background in {7, 15}
    except Exception:
        return False


FANCY = _utf8_ok()
LIGHT = _light_background()

if LIGHT:
    TEXT = "bold #001433"
    MUTED = "#1b2a4a"
    ACCENT = "bold #12c400"
    GREEN = "bold #067a28"
    NAVY = "bold #001a66"
    RED = "bold #8b0000"
    URL = "bold #001a80"
    EMPTY = "#c5d4f5"
    BADGE = "bold #ffffff on #00175e"
    BADGE_ACCENT = "bold #00175e on #12c400"
else:
    TEXT = "bold #ffffff"
    MUTED = "#ffffff"
    ACCENT = "bold #39ff14"
    GREEN = "bold #00c853"
    NAVY = "bold #3d5bd9"
    RED = "bold #c8102e"
    URL = "bold #3d5bd9"
    EMPTY = "#0b1f4d"
    BADGE = "bold #ffffff on #00175e"
    BADGE_ACCENT = "bold #00175e on #39ff14"


def theme() -> Theme:
    return Theme(
        {
            "accent": ACCENT,
            "green": GREEN,
            "navy": NAVY,
            "red": RED,
            "text": TEXT,
            "muted": MUTED,
            "url": URL,
        }
    )


def clock(moment: float | None = None) -> str:
    if not FANCY:
        return "|/-\\"[int((moment if moment is not None else time.monotonic()) * 8) % 4]
    stamp = time.monotonic() if moment is None else moment
    return CLOCKS[int(stamp * 8) % len(CLOCKS)]


def fill_char() -> str:
    return "█" if FANCY else "#"


def empty_char() -> str:
    return "░" if FANCY else "-"


def gauge(ratio: float, width: int = 16) -> Text:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    text = Text()
    text.append(fill_char() * filled, style=ACCENT)
    text.append(empty_char() * (width - filled), style=EMPTY)
    text.append(f" {int(round(ratio * 100)):3d}%", style=TEXT)
    return text


def meter(ratio: float, width: int = 12, style: str = "") -> Text:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    text = Text()
    text.append(fill_char() * filled, style=style or ACCENT)
    text.append(empty_char() * (width - filled), style=EMPTY)
    return text


def sparkline(values: list[float], style: str = "") -> Text:
    paint = style or ACCENT
    text = Text()
    if not values:
        text.append("─" if FANCY else "-", style=NAVY)
        return text
    low = min(values)
    high = max(values)
    if not FANCY:
        text.append("*" * len(values), style=paint)
        return text
    if high == low:
        text.append(BLOCKS[3] * len(values), style=paint)
        return text
    for value in values:
        index = int((value - low) / (high - low) * (len(BLOCKS) - 1))
        text.append(BLOCKS[index], style=paint)
    return text


def logo() -> Text:
    art = r"""
  ___  ____  ___ ___  _   _
 / _ \|  _ \|_ _/ _ \| \ | |
| | | | |_) || | | | |  \| |
| |_| |  _ < | | |_| | |\  |
 \___/|_| \_\___\___/|_| \_|
""".strip("\n")
    text = Text()
    lines = art.splitlines()
    for index, line in enumerate(lines):
        text.append(line + "\n", style=TEXT if index % 2 == 0 else NAVY)
    text.append("  PUBLIC SOURCE MESH", style=ACCENT)
    text.append("   ·   ", style=TEXT)
    text.append("OPEN SEARCH TERMINAL", style=TEXT)
    return text


def wordmark() -> Text:
    text = Text()
    text.append(" ORION ", style=BADGE)
    text.append(" PUBLIC SOURCE MESH ", style=TEXT)
    text.append(" v1.0 ", style=BADGE_ACCENT)
    return text


def status_style(status: str) -> str:
    return {
        "ready": NAVY,
        "hit": ACCENT,
        "miss": RED,
        "blocked": RED,
        "error": RED,
        "skip": MUTED,
        "wait": ACCENT,
    }.get(status, TEXT)


def status_glyph(status: str, moment: float | None = None) -> str:
    if status == "wait":
        return clock(moment)
    if not FANCY:
        return {
            "ready": "*",
            "hit": "+",
            "miss": "-",
            "blocked": "!",
            "error": "x",
            "skip": ".",
        }.get(status, "*")
    return {
        "ready": "◆",
        "hit": "●",
        "miss": "○",
        "blocked": "!",
        "error": "×",
        "skip": "·",
    }.get(status, "◆")


def cat_style(category: str) -> str:
    return {
        "meta": ACCENT,
        "core": ACCENT,
        "people": RED,
        "social": NAVY,
        "web": TEXT,
        "records": GREEN,
        "maps": NAVY,
        "live": ACCENT,
    }.get(category, TEXT)


def tier_style(tier: str) -> str:
    return {
        "free": GREEN,
        "preview": NAVY,
        "paid": RED,
        "live": ACCENT,
    }.get(tier, TEXT)
