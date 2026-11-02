"""What the rest of the code needs from the brain (docs/design/onboarding-brain-spec.md §1).

The brain area owns `brain/` (YAML) and `src/glpi_agents/brain/` (the loader). The loader
returns an object satisfying `Brain`. Everything else reads GLPI facts and rules through
it, never by re-stating them (CLAUDE.md R6).
"""
from __future__ import annotations

from typing import Literal, Optional, Protocol

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """A GLPI fact or a rule, by its stable id (e.g. "A4", "F4", "P-PARENT")."""

    id: str
    claim: str
    source: str                                  # "src/Glpi/Api/API.php::changeActiveEntities" or a spec section
    verified_live: bool = False                  # set once a live probe has confirmed it


class RecordTypeSpec(BaseModel):
    """How one GLPI record type behaves and is searched (brain spec §3)."""

    name: str                                    # "location", "group", "user", ...
    itemtype: str                                # "Location"
    right: str                                   # GLPI right name, "location"
    shared_flag: bool                            # has is_recursive
    tree_parent_field: Optional[str] = None      # "locations_id" for trees, else None
    has_trash: bool                              # is_deleted exists (delete trashes vs purges)
    db_unique: list[str] = Field(default_factory=list)  # DB unique key columns, [] when none (G1, S1)
    search_options: dict[str, int]               # role -> GLPI search-option id: "id", "name",
                                                 # "completename", "entity", "is_recursive", ...


class RoleSpec(BaseModel):
    """A role's grant and allow-list (T1-4)."""

    name: str                                    # "onboarding" / "inventory"
    glpi_user: str                               # "onboarding.bot"
    token_env: str                               # .env key holding its user token
    grant_entity: str                            # full GLPI path of its profile assignment
    grant_recursive: bool
    rights: dict[str, int]                       # expected live rights by right name
    grantable_profiles: list[str] = Field(default_factory=list)
    tools: list[str]                             # the allow-list, exact


class ToolSpec(BaseModel):
    name: str
    kind: Literal["read", "write"]
    required_rights: dict[str, int]              # right name -> bitmask the role must hold


class AgentManifest(BaseModel):
    """An agent's layer on top of the core (brain spec §1): it may only narrow."""

    name: str                                    # "onboarding-agent"
    role: str
    brain: str                                   # version range, e.g. ">=0.1,<1"
    rules: list[str]                             # rule ids it applies, e.g. ["U1", "U3", "P-PARENT"]


class Brain(Protocol):
    version: str                                 # brain version, e.g. "0.1.0"
    glpi_version: str                            # "11.0.12"

    def codes(self) -> dict[str, str]:
        """Reason code -> lane, as listed in the brain (must equal contracts.envelope.CODE_LANES)."""

    def fact(self, fact_id: str) -> Fact: ...
    def has_id(self, fact_id: str) -> bool: ...
    def record_type(self, name: str) -> RecordTypeSpec: ...
    def role(self, name: str) -> RoleSpec: ...
    def tool(self, name: str) -> ToolSpec: ...
    def agent(self, name: str) -> AgentManifest: ...
