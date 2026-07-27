"""Typed access to the knowledge base in ``/kb``.

Tools import facts from here instead of restating them. If a literal about this
Trackspace instance appears in a tool, it belongs in ``kb/trackspace.json``
instead — with a ``source`` recording where it was proven.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from .errors import ConfigurationError

#: ``/kb`` sits beside the ``trackspace`` package at the repository root.
_DEFAULT_KB_DIR = Path(__file__).resolve().parent.parent / "kb"

#: Override for out-of-tree checkouts and tests.
KB_DIR_ENV = "TRACKSPACE_KB_DIR"


def kb_dir() -> Path:
    override = os.environ.get(KB_DIR_ENV)
    return Path(override).expanduser() if override else _DEFAULT_KB_DIR


@dataclass(frozen=True)
class Endpoint:
    """One REST endpoint as recorded in the knowledge base."""

    name: str
    method: str
    path: str
    timeout_s: float
    fixture: str | None

    def suffix(self, api_root: str) -> str:
        """The part of the path below the API root, e.g. ``/issue/{key}/worklog``."""
        return self.path[len(api_root) :] if self.path.startswith(api_root) else self.path

    def render(self, api_root: str, **params: str) -> str:
        """Fill the path template, URL-quoting every value."""
        safe = {k: quote(str(v), safe="") for k, v in params.items()}
        try:
            return self.suffix(api_root).format(**safe)
        except KeyError as exc:  # pragma: no cover - programming error
            raise ConfigurationError(
                f"endpoint {self.name!r} needs path parameter {exc.args[0]!r}"
            ) from exc


class KnowledgeBase:
    """Read-only view over ``kb/trackspace.json`` plus the fixture directory."""

    def __init__(self, data: dict[str, Any], root: Path) -> None:
        self._data = data
        self._root = root

    # ---- instance ----------------------------------------------------------
    @property
    def base_url(self) -> str:
        return cast(str, self._data["instance"]["base_url"])

    @property
    def api_version(self) -> str:
        return cast(str, self._data["instance"]["api_version"])

    @property
    def api_root(self) -> str:
        return cast(str, self._data["instance"]["api_root"])

    @property
    def pat_profile_url(self) -> str:
        return cast(str, self._data["instance"]["pat_profile_url"])

    def api_root_for(self, api_version: str) -> str:
        """The API root for a (possibly overridden) version segment."""
        return f"/rest/api/{api_version}"

    # ---- auth --------------------------------------------------------------
    @property
    def token_env(self) -> str:
        return cast(str, self._data["auth"]["token_env"])

    @property
    def token_env_fallbacks(self) -> list[str]:
        return list(self._data["auth"]["token_env_fallbacks"])

    @property
    def request_headers(self) -> dict[str, str]:
        return dict(self._data["auth"]["request_headers"])

    # ---- endpoints ---------------------------------------------------------
    def endpoint(self, name: str) -> Endpoint:
        try:
            spec = self._data["endpoints"][name]
        except KeyError as exc:
            raise ConfigurationError(f"unknown endpoint {name!r} in the knowledge base") from exc
        return Endpoint(
            name=name,
            method=spec["method"],
            path=spec["path"],
            timeout_s=float(spec.get("timeout_s", 30)),
            fixture=spec.get("fixture"),
        )

    def endpoint_names(self) -> list[str]:
        return sorted(self._data["endpoints"])

    # ---- JQL ---------------------------------------------------------------
    def jql(self, name: str, **params: object) -> str:
        try:
            template = self._data["jql"][name]["template"]
        except (KeyError, TypeError) as exc:
            raise ConfigurationError(f"unknown JQL pattern {name!r} in the knowledge base") from exc
        try:
            return cast(str, template.format(**params))
        except KeyError as exc:  # pragma: no cover - programming error
            raise ConfigurationError(
                f"JQL pattern {name!r} needs parameter {exc.args[0]!r}"
            ) from exc

    # ---- fields ------------------------------------------------------------
    def field_id(self, name: str) -> str:
        """Resolve a knowledge-base field name to the id sent over the wire.

        Only system fields resolve today; ``fields.custom`` is empty because no
        ``customfield_*`` id has ever been observed on this instance.
        """
        fields = self._data["fields"]
        for group in ("system", "custom"):
            spec = fields.get(group, {}).get(name)
            if spec is not None:
                return cast(str, spec["id"])
        raise ConfigurationError(
            f"unknown field {name!r}. Custom field ids for this instance are unrecorded — "
            "add it to kb/trackspace.json with its provenance before using it."
        )

    # ---- pagination and defaults ------------------------------------------
    def page_size(self, endpoint_name: str) -> int:
        sizes = self._data["pagination"]["page_size"]
        return int(sizes[endpoint_name]) if endpoint_name in sizes else 100

    def payload_key(self, endpoint_name: str) -> str:
        return cast(str, self._data["pagination"]["payload_key"][endpoint_name])

    def default(self, name: str) -> Any:
        try:
            return self._data["defaults"][name]
        except KeyError as exc:
            raise ConfigurationError(f"unknown default {name!r} in the knowledge base") from exc

    @property
    def worklog_started_format(self) -> str:
        return cast(str, self._data["worklog_semantics"]["started_format_out"])

    @property
    def raw(self) -> dict[str, Any]:
        """The whole document, for anything the accessors do not cover."""
        return self._data

    # ---- fixtures ----------------------------------------------------------
    @property
    def fixtures_dir(self) -> Path:
        return self._root / "fixtures"

    def fixture(self, name: str) -> Any:
        """Load an offline fixture by file name (``.json`` optional)."""
        filename = name if name.endswith(".json") else f"{name}.json"
        path = self.fixtures_dir / filename
        if not path.is_file():
            raise ConfigurationError(f"no fixture {filename!r} in {self.fixtures_dir}")
        return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _load(root_str: str) -> KnowledgeBase:
    root = Path(root_str)
    path = root / "trackspace.json"
    if not path.is_file():
        raise ConfigurationError(
            f"knowledge base not found at {path}. Run from the repository root or set {KB_DIR_ENV}."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return KnowledgeBase(data, root)


def load_kb(root: Path | None = None) -> KnowledgeBase:
    """Load (and memoise) the knowledge base."""
    return _load(str(root or kb_dir()))
