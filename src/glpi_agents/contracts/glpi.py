"""The GLPI client interface (docs/design/api-and-faults-spec.md).

Only `src/glpi_agents/glpi/` implements this and talks to GLPI (CLAUDE.md R7). The
server's tools depend on this Protocol, never on HTTP.

What the client guarantees to its callers:
  * Legacy REST API only (D-API-1); tokens travel in headers only, never in a URL (T1-5).
  * Session expiry is handled inside: re-login, re-apply the scope (body must be exactly
    `true`, A4), repeat the call (D-API-3 §5.2). Callers never see an expired session.
  * 502s are retried (up to 3, 0.5/1/2 s). A write that gets 504 or times out raises
    WriteOutcomeUnknown; it is never retried blindly (P-UNKNOWN).
  * `search` returns a confirmed answer or raises ReadUnconfirmed: a bare `[]` from
    /search means an expired session (A14, X5), never "none".
  * `list_sub` (a collection GET, where `[]` can be genuine) confirms emptiness with the
    getActiveEntities probe and the active-scope check before returning [] (§5.1).
  * GLPI error text is never put in an exception message as-is: SQL and tokens are
    stripped (A17); the raw text goes only to the masked debug log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional, Protocol, Sequence


# ---- values -------------------------------------------------------------------------

@dataclass(frozen=True)
class Scope:
    entity_id: int
    recursive: bool


@dataclass(frozen=True)
class SessionInfo:
    glpi_user: str                 # the login the token belongs to, e.g. "onboarding.bot"
    profile: str                   # active profile name
    scope: Scope                   # starting scope (A1)


@dataclass(frozen=True)
class Criterion:
    """One /search criterion. `field` is a GLPI search-option id (A13), version-specific,
    taken from the brain. Use `Criterion.exact` for exact, case-insensitive text matches."""

    field: int
    value: Any
    searchtype: Literal["contains", "equals", "notequals", "under", "notunder"] = "contains"
    link: Literal["AND", "OR"] = "AND"

    @staticmethod
    def exact(field: int, text: str) -> "Criterion":
        # Decision: A13/V7 — "contains" is LIKE '%x%'; ^...$ anchors it to an exact match,
        # ignoring case (collation, V8). GLPI escapes `_` but not `%`, so `%` would be a
        # wildcard: implementations must refuse text containing `%` (invalid_value).
        return Criterion(field=field, value=f"^{text}$", searchtype="contains")


@dataclass
class SearchResult:
    totalcount: int
    rows: list[dict[int, Any]]     # keyed by search-option id
    faults_seen: list[str] = field(default_factory=list)


@dataclass
class CallStats:
    calls: int = 0
    writes: int = 0                # POST / PUT / PATCH / DELETE sent (A4.5 write count)
    faults_seen: list[str] = field(default_factory=list)


# ---- errors -------------------------------------------------------------------------

class GlpiError(Exception):
    """Base. `envelope_code` is the closed-list code a tool should use (T1-3)."""

    envelope_code = "internal_error"

    def __init__(self, message: str, *, detail: Optional[str] = None):
        super().__init__(message)
        self.detail = detail       # sanitised extra context, safe to show


class ScopeChangeRefused(GlpiError):
    """changeActiveEntities answered anything but exactly `true` (A4)."""
    envelope_code = "right_missing"


class NotFound(GlpiError):
    """404 ERROR_ITEM_NOT_FOUND on a read by id (A8)."""
    envelope_code = "internal_error"


class RightMissing(GlpiError):
    """403 ERROR_RIGHT_MISSING, or a write refused with GLPI's permission message (A11)."""
    envelope_code = "right_missing"


class WriteRefused(GlpiError):
    """GLPI refused a write for a business reason (400 ERROR_GLPI_ADD/_UPDATE/_DELETE)."""

    def __init__(self, message: str, *, kind: Literal["duplicate", "validation", "other"], detail: Optional[str] = None):
        super().__init__(message, detail=detail)
        self.kind = kind
        # Decision: A17 — a duplicate-key refusal means our check said "none" but GLPI has
        # it: unknown, re-check (T1-3 rule 2).
        self.envelope_code = "exists_unexpectedly" if kind == "duplicate" else "internal_error"


class WriteOutcomeUnknown(GlpiError):
    """The write's response was lost (504 or timeout): it may be committed (X3, P-UNKNOWN)."""
    envelope_code = "write_unknown"


class ReadUnconfirmed(GlpiError):
    """An empty or odd answer could not be confirmed as genuine after the §5.1 checks."""
    envelope_code = "read_unconfirmed"


class BudgetExhausted(GlpiError):
    """The per-tool-call budget of GLPI calls or time ran out (T1-6, §5.3)."""
    envelope_code = "budget_exhausted"


# ---- the client ---------------------------------------------------------------------

class GlpiClient(Protocol):
    def open(self) -> SessionInfo:
        """initSession with the role's user token; returns who we are and the start scope."""

    def close(self) -> None:
        """killSession (no decoy sessions, §5.4)."""

    def set_scope(self, entity_id: int, recursive: bool = False) -> None:
        """changeActiveEntities; raises ScopeChangeRefused unless the body is exactly true."""

    def scope(self) -> Scope:
        """getActiveEntities, verified to be a well-formed object (A6)."""

    def search(
        self,
        itemtype: str,
        criteria: Sequence[Criterion],
        display: Sequence[int],
        *,
        include_trash: bool = False,
        start: int = 0,
        limit: int = 200,
    ) -> SearchResult:
        """GET /search/{itemtype} in the current scope (A12): the listing the UI shows.
        Pages internally until `limit` rows or the end; id (option 2) is always displayed."""

    def get(self, itemtype: str, item_id: int, *, expand_dropdowns: bool = False) -> dict[str, Any]:
        """Read by id: detail only, on a record /search already returned (D-API-2)."""

    def list_sub(self, itemtype: str, item_id: int, sub_itemtype: str) -> list[dict[str, Any]]:
        """GET /{itemtype}/{id}/{sub_itemtype}, e.g. a user's Profile_User rows."""

    def create(self, itemtype: str, fields: Mapping[str, Any]) -> int:
        """POST; returns the new id. Raises WriteRefused / RightMissing / WriteOutcomeUnknown."""

    def update(self, itemtype: str, item_id: int, fields: Mapping[str, Any]) -> None:
        """PUT; raises like create."""

    def delete(self, itemtype: str, item_id: int, *, purge: bool) -> None:
        """DELETE (force_purge when purge); raises like create. Used by apply_approved_change
        (assignment replace) and the test-only cleanup client, never by an agent directly."""

    def active_profile_rights(self) -> dict[str, int]:
        """The active profile's rights by right name (getActiveProfile), for the start-up check."""

    def glpi_version(self) -> str:
        """`version` from getGlpiConfig (A15); the rest of the config is never exposed."""

    def stats(self) -> CallStats:
        """Calls, writes and faults seen so far in this client's life."""
