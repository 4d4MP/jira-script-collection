"""Shared library for the Trackspace tool collection.

Trackspace is the internal name for the Jira Data Center instance at
``https://trackspace.lhsystems.com``. "Jira" appears only when naming the
underlying REST API.

Everything instance-specific — endpoints, field ids, JQL, page sizes, timeouts —
lives in ``/kb`` and is reached through :mod:`trackspace.kb`.
"""

from .auth import Credentials, auth_status, read_pat, require_pat
from .client import TrackspaceClient
from .errors import (
    ApiError,
    AuthError,
    ConfigurationError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TrackspaceError,
    TransportError,
)
from .kb import KnowledgeBase, load_kb

__all__ = [
    "ApiError",
    "AuthError",
    "ConfigurationError",
    "Credentials",
    "ForbiddenError",
    "KnowledgeBase",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "TrackspaceClient",
    "TrackspaceError",
    "TransportError",
    "auth_status",
    "load_kb",
    "read_pat",
    "require_pat",
]
