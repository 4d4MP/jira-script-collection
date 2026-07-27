"""PAT discovery.

The token lives in the environment and stays there: it is never prompted for,
never persisted, and never rendered. Everything here returns presence and source,
never the value — except :attr:`Credentials.token`, which exists solely to be
handed to the HTTP client.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ConfigurationError
from .kb import KnowledgeBase, load_kb


@dataclass(frozen=True)
class Credentials:
    """A PAT and the environment variable it came from."""

    token: str
    source_env: str

    def __repr__(self) -> str:  # pragma: no cover - defensive, but cheap
        return f"Credentials(source_env={self.source_env!r}, token=<redacted>)"

    __str__ = __repr__


def read_pat(
    env: Mapping[str, str] | None = None,
    kb: KnowledgeBase | None = None,
) -> Credentials | None:
    """Return the PAT from the environment, or ``None`` if nothing is set."""
    environ: Mapping[str, str] = os.environ if env is None else env
    knowledge = kb or load_kb()
    for name in [knowledge.token_env, *knowledge.token_env_fallbacks]:
        value = environ.get(name, "").strip()
        if value:
            return Credentials(token=value, source_env=name)
    return None


def require_pat(
    env: Mapping[str, str] | None = None,
    kb: KnowledgeBase | None = None,
) -> Credentials:
    """Return the PAT, or explain how to provide one."""
    credentials = read_pat(env, kb)
    if credentials is not None:
        return credentials
    knowledge = kb or load_kb()
    raise ConfigurationError(
        f"{knowledge.token_env} is not set. Export your Trackspace Personal Access "
        f"Token before running: export {knowledge.token_env}=... "
        f"(generate one at {knowledge.pat_profile_url})"
    )


def auth_status(
    env: Mapping[str, str] | None = None,
    kb: KnowledgeBase | None = None,
) -> tuple[bool, str]:
    """``(present, label)`` for the header line. The label never contains the token."""
    credentials = read_pat(env, kb)
    knowledge = kb or load_kb()
    if credentials is None:
        return False, f"missing ({knowledge.token_env} not set)"
    return True, f"present (from {credentials.source_env})"
