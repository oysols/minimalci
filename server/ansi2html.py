import re
from typing import Any

from flask import escape


ANSI_CODES = {
    "30": "black",
    "31": "red",
    "32": "#00ff00",  # green
    "33": "yellow",
    "34": "#3388ff",  # blue
    "35": "magenta",
    "36": "cyan",
    "37": "white",
    "0": "white",  # reset
}


def replace(m: Any) -> str:
    if m.group(2) != "m":
        return ""
    colors = m.group(1).split(";")
    font = '<font color="{}">'
    for color in reversed(colors):
        class_name = ANSI_CODES.get(color)
        if class_name:
            return font.format(class_name)
    return ""


def escaped(line: str) -> str:
    escaped = escape(line)
    modified_line, number_of_subs = re.subn(
        r"\x1b\[([;\d]*)([a-z])",
        replace,
        escaped,
    )
    modified_line += "</font>" * number_of_subs
    return modified_line
