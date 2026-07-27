"""Interactive input: a persistent prompt line, arrow-key choices, inline validation.

Every prompt here has a flag equivalent in the tools, so nothing this module does
is ever the only way to reach a behaviour.

Ctrl+C is not swallowed. ``questionary``'s ``ask()`` would quietly return ``None``
on interrupt; ``unsafe_ask()`` lets ``KeyboardInterrupt`` reach the tool's
cancellation handler, which is what prints the summary and exits 130.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, TypeVar

import questionary
from questionary import Choice, Style

from ..errors import ConfigurationError
from .theme import color_disabled

T = TypeVar("T")

_STYLE = Style(
    []
    if color_disabled()
    else [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("answer", "fg:cyan bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green"),
        ("separator", "fg:#666666"),
        ("instruction", "fg:#888888"),
    ]
)


def require_tty() -> None:
    """Interactive mode needs a real terminal; otherwise point at the flags."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ConfigurationError(
            "interactive mode needs a terminal. Pass the equivalent flags instead "
            "(run with --help to see them)."
        )


# ---- validators ------------------------------------------------------------
def validate_date(value: str) -> bool | str:
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return "Use YYYY-MM-DD."
    return True


def validate_time(value: str) -> bool | str:
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return "Use HH:MM."
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return "Hours 00-23, minutes 00-59."
    return True


def validate_positive_int(value: str) -> bool | str:
    text = value.strip()
    if not text.isdigit() or int(text) <= 0:
        return "Enter a whole number greater than zero."
    return True


def validate_nonempty(value: str) -> bool | str:
    return True if value.strip() else "This cannot be empty."


def validate_issue_key(value: str) -> bool | str:
    text = value.strip()
    if "-" not in text or not text.split("-")[-1].isdigit():
        return "Use a Trackspace issue key, e.g. CLOPSSEC-41456."
    return True


# ---- prompts ---------------------------------------------------------------
def select(
    message: str,
    choices: Sequence[Choice | str],
    *,
    default: Any = None,
    instruction: str | None = None,
) -> Any:
    require_tty()
    return questionary.select(
        message,
        choices=list(choices),
        default=default,
        style=_STYLE,
        instruction=instruction,
        qmark="›",
        use_shortcuts=False,
    ).unsafe_ask()


def text(
    message: str,
    *,
    default: str = "",
    validate: Callable[[str], bool | str] | None = None,
) -> str:
    require_tty()
    answer = questionary.text(
        message,
        default=default,
        validate=validate,
        style=_STYLE,
        qmark="›",
    ).unsafe_ask()
    return "" if answer is None else str(answer)


def confirm(message: str, *, default: bool = False) -> bool:
    require_tty()
    return bool(questionary.confirm(message, default=default, style=_STYLE, qmark="›").unsafe_ask())


def checkbox(
    message: str,
    choices: Sequence[Choice | str],
    *,
    instruction: str | None = None,
) -> list[Any]:
    require_tty()
    answer = questionary.checkbox(
        message,
        choices=list(choices),
        style=_STYLE,
        instruction=instruction,
        qmark="›",
    ).unsafe_ask()
    return list(answer or [])


__all__ = [
    "Choice",
    "checkbox",
    "confirm",
    "require_tty",
    "select",
    "text",
    "validate_date",
    "validate_issue_key",
    "validate_nonempty",
    "validate_positive_int",
    "validate_time",
]
