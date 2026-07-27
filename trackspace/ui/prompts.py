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
from typing import Any, Final, TypeVar

import questionary
from prompt_toolkit.keys import Keys
from questionary import Choice, Style

from ..errors import ConfigurationError
from .theme import color_disabled, unicode_ok

T = TypeVar("T")

# Hex values rather than ANSI names, for the same reason as trackspace.ui.theme:
# they must read on a white terminal as well as a black one. Kept in step with
# STYLES there — blue for the accent, green for a confirmed answer, mid grey for
# anything secondary.
_STYLE = Style(
    []
    if color_disabled()
    else [
        ("qmark", "fg:#0087af bold"),
        ("question", "bold"),
        ("answer", "fg:#0087af bold"),
        ("pointer", "fg:#0087af bold"),
        ("highlighted", "fg:#0087af bold"),
        ("selected", "fg:#008700"),
        ("separator", "fg:#808080"),
        ("instruction", "fg:#808080"),
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


# ---- going back ------------------------------------------------------------
class _Back:
    """The answer meaning "take me back one level".

    Compared by identity against the single :data:`BACK` instance, and falsy so
    ``if not answer`` reads correctly at a call site.
    """

    def __repr__(self) -> str:
        return "BACK"

    def __bool__(self) -> bool:
        return False


#: Returned by :func:`select` and :func:`checkbox` when the user presses ←.
BACK: Final[_Back] = _Back()


def is_back(answer: Any) -> bool:
    """True when a menu answer means "go back", including a missing answer.

    A prompt that returns nothing usable is treated as a step backwards rather
    than passed on: questionary has been seen to hand back a value that was never
    among the choices, and a menu is the safest place to land.
    """
    return answer is BACK or answer is None


def choice_values(choices: Sequence[Choice | str]) -> list[Any]:
    return [choice.value if isinstance(choice, Choice) else choice for choice in choices]


def validated(answer: Any, choices: Sequence[Choice | str]) -> Any:
    """The answer if it is one we offered, otherwise ``BACK``."""
    if answer is BACK:
        return BACK
    values = choice_values(choices)
    return answer if any(answer == value for value in values) else BACK


def _bind_back(question: Any) -> None:
    """Make ← exit the prompt with :data:`BACK`.

    Only bound on menus. In a text prompt ← still moves the cursor, which is what
    anyone typing a date expects.
    """

    def _go_back(event: Any) -> None:
        event.app.exit(result=BACK)

    question.application.key_bindings.add(Keys.Left, eager=True)(_go_back)


def _instruction(base: str | None, allow_back: bool) -> str | None:
    if not allow_back:
        return base
    hint = "← back" if unicode_ok() else "left = back"
    return f"{base}  ·  {hint}" if base else f"({hint})"


# ---- prompts ---------------------------------------------------------------
def select(
    message: str,
    choices: Sequence[Choice | str],
    *,
    default: Any = None,
    instruction: str | None = None,
    allow_back: bool = False,
) -> Any:
    """Arrow-key menu. Returns the chosen value, or ``BACK``.

    ``BACK`` covers both "the user pressed ←" and "questionary did not give us
    one of the choices" — see :func:`is_back`.
    """
    require_tty()
    question = questionary.select(
        message,
        choices=list(choices),
        default=default,
        style=_STYLE,
        instruction=_instruction(instruction, allow_back),
        qmark="›",
        use_shortcuts=False,
    )
    if allow_back:
        _bind_back(question)
    return validated(question.unsafe_ask(), choices)


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
    allow_back: bool = False,
) -> list[Any] | _Back:
    """Multi-select. Returns the chosen values, or ``BACK`` if ← was pressed."""
    require_tty()
    question = questionary.checkbox(
        message,
        choices=list(choices),
        style=_STYLE,
        instruction=_instruction(instruction, allow_back),
        qmark="›",
    )
    if allow_back:
        _bind_back(question)
    answer: Any = question.unsafe_ask()
    if isinstance(answer, _Back):
        return BACK
    return list(answer) if answer else []


__all__ = [
    "BACK",
    "Choice",
    "checkbox",
    "choice_values",
    "confirm",
    "is_back",
    "require_tty",
    "select",
    "text",
    "validate_date",
    "validate_issue_key",
    "validate_nonempty",
    "validate_positive_int",
    "validate_time",
    "validated",
]
