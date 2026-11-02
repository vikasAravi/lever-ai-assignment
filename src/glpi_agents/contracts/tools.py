"""Tool inputs: the API between the MCP server and the agents (tier1 spec §2, T1-2).

Conventions:
  * Entity paths are full GLPI paths ("Root entity > Acme > India > Engineering").
    Translating the workbook's "Root > ..." form is the agent's intake job (P-PATH).
  * Tree paths use " > " (e.g. "Floor 5 Lab > Server Room"); a bare name is top level (P-PARENT).
  * `entity` defaults to the session root; outside the subtree -> refused / out_of_scope (T1-1).
  * Write tools take an optional `ref` (e.g. "Users!4") so the decision log, journal and
    report can be joined. It never influences a decision.
  * `ROLE_TOOLSETS` is the design's reference list. The brain's role files are the source
    of truth; a test keeps the two equal, so a drift fails loudly.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")    # unknown arguments are an error, not ignored


# ---- shared -------------------------------------------------------------------------

class SessionInfoIn(_In):
    pass


class ResolveEntityIn(_In):
    path: str


class ListEntitiesIn(_In):
    under: Optional[str] = None


# ---- onboarding: writes and their read twins ---------------------------------------

class EnsureLocationIn(_In):
    entity: str
    path: str
    shared: bool
    ref: Optional[str] = None


class FindLocationsIn(_In):
    entity: Optional[str] = None
    path: Optional[str] = None


class EnsureGroupIn(_In):
    entity: str
    name: str
    shared: bool
    ref: Optional[str] = None


class FindGroupsIn(_In):
    entity: Optional[str] = None
    name: Optional[str] = None


class EnsureUserIn(_In):
    login: str
    entity: str
    profile: str
    shared: bool
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    ref: Optional[str] = None


class FindUsersIn(_In):
    entity: Optional[str] = None
    login: Optional[str] = None


class EnsureSupplierIn(_In):
    entity: str
    name: str
    type: Optional[str] = None
    phone: Optional[str] = None
    ref: Optional[str] = None


class FindSuppliersIn(_In):
    entity: Optional[str] = None
    name: Optional[str] = None


class EnsureCategoryIn(_In):
    entity: str
    path: str
    ref: Optional[str] = None


class FindCategoriesIn(_In):
    entity: Optional[str] = None
    path: Optional[str] = None


class ApplyApprovedChangeIn(_In):
    diff_id: str
    ref: Optional[str] = None


# ---- inventory (Agent B; inputs fixed now so the server can register them) ---------

class FindComputersIn(_In):
    serial: Optional[str] = None
    name: Optional[str] = None


class UpdateComputerIn(_In):
    id: int
    expected_before: dict = Field(default_factory=dict)
    changes: dict = Field(default_factory=dict)
    ref: Optional[str] = None


class SetComputerStateIn(_In):
    id: int
    state: str
    expected_before: dict = Field(default_factory=dict)
    ref: Optional[str] = None


class OpenDiscrepancyTicketIn(_In):
    group_key: str
    title: str
    body: str
    computer_ids: list[int]
    ref: Optional[str] = None


class FindDiscrepancyTicketsIn(_In):
    group_key: Optional[str] = None


TOOL_INPUTS: dict[str, type[_In]] = {
    "session_info": SessionInfoIn,
    "resolve_entity": ResolveEntityIn,
    "list_entities": ListEntitiesIn,
    "ensure_location": EnsureLocationIn,
    "find_locations": FindLocationsIn,
    "ensure_group": EnsureGroupIn,
    "find_groups": FindGroupsIn,
    "ensure_user": EnsureUserIn,
    "find_users": FindUsersIn,
    "ensure_supplier": EnsureSupplierIn,
    "find_suppliers": FindSuppliersIn,
    "ensure_category": EnsureCategoryIn,
    "find_categories": FindCategoriesIn,
    "apply_approved_change": ApplyApprovedChangeIn,
    "find_computers": FindComputersIn,
    "update_computer": UpdateComputerIn,
    "set_computer_state": SetComputerStateIn,
    "open_discrepancy_ticket": OpenDiscrepancyTicketIn,
    "find_discrepancy_tickets": FindDiscrepancyTicketsIn,
}

WRITE_TOOLS = frozenset({
    "ensure_location", "ensure_group", "ensure_user", "ensure_supplier", "ensure_category",
    "apply_approved_change", "update_computer", "set_computer_state", "open_discrepancy_ticket",
})

_SHARED = ["session_info", "resolve_entity", "list_entities"]

# Decision: T1-4 — the design's access map (14 onboarding tools, 9 inventory tools).
ROLE_TOOLSETS: dict[str, frozenset[str]] = {
    "onboarding": frozenset(_SHARED + [
        "ensure_location", "find_locations", "ensure_group", "find_groups",
        "ensure_user", "find_users", "ensure_supplier", "find_suppliers",
        "ensure_category", "find_categories", "apply_approved_change",
    ]),
    "inventory": frozenset(_SHARED + [
        "find_users", "find_computers", "update_computer", "set_computer_state",
        "open_discrepancy_ticket", "find_discrepancy_tickets",
    ]),
}
