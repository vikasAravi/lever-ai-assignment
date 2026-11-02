"""The answer envelope every tool returns (docs/design/tier1-mcp-server-spec.md §3, T1-3).

Three statuses map one-to-one onto the report's outcomes:
  done     (+ outcome created / matched / updated, proven by a readback) -> created / matched existing
  refused  (+ code, lane, message, resolution)                           -> skipped with reason
  unknown  (+ code, what_is_known, next_step)                            -> unknown

Codes are a closed list. The brain carries the same list; a test keeps them equal,
and a new code is a brain version bump.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class Status(str, Enum):
    done = "done"
    refused = "refused"
    unknown = "unknown"


class Outcome(str, Enum):
    created = "created"
    matched = "matched"
    updated = "updated"


class Lane(str, Enum):
    """Who has to act on a refusal or an unknown (onboarding-brain-spec §2.3)."""

    bad_input = "bad_input"   # fix the input (workbook, CSV, tool arguments)
    decision = "decision"     # a human approves or rejects (approvals file)
    blocked = "blocked"       # an admin changes GLPI or the bot's rights
    unknown = "unknown"       # nobody can tell yet; the next run re-checks first


# Decision: T1-3 — the closed list of reason codes and the lane each belongs to.
CODE_LANES: dict[str, Lane] = {
    # bad input
    "out_of_scope": Lane.bad_input,
    "entity_not_found": Lane.bad_input,
    "missing_required": Lane.bad_input,
    "invalid_value": Lane.bad_input,
    "parent_not_found": Lane.bad_input,
    "parent_cycle": Lane.bad_input,
    "duplicate_row": Lane.bad_input,
    # decision
    "conflict": Lane.decision,
    "ambiguous_parent": Lane.decision,
    "duplicate_in_glpi": Lane.decision,
    "trashed_match": Lane.decision,
    "precondition_failed": Lane.decision,
    # blocked
    "entity_not_creatable": Lane.blocked,
    "profile_not_grantable": Lane.blocked,
    "right_missing": Lane.blocked,
    "parent_not_created": Lane.blocked,
    "internal_error": Lane.blocked,
    # unknown
    "write_unknown": Lane.unknown,
    "parent_unknown": Lane.unknown,
    "read_unconfirmed": Lane.unknown,
    "exists_unexpectedly": Lane.unknown,
    "readback_mismatch": Lane.unknown,
    "conformance": Lane.unknown,
    "budget_exhausted": Lane.unknown,
}

UNKNOWN_CODES = frozenset(c for c, lane in CODE_LANES.items() if lane is Lane.unknown)
REFUSAL_CODES = frozenset(c for c, lane in CODE_LANES.items() if lane is not Lane.unknown)


class Record(BaseModel):
    """What a tool acted on or found, by its full identity (P-IDENTITY)."""

    type: str                                   # GLPI itemtype, e.g. "Location"
    id: Optional[int] = None
    entity: Optional[str] = None                # full GLPI path, "Root entity > Acme > ..."
    path: Optional[str] = None                  # tree path or name ("Floor 5 Lab > Server Room")
    login: Optional[str] = None                 # users
    shared: Optional[bool] = None               # is_recursive, where the type has it
    fields: dict[str, Any] = Field(default_factory=dict)  # other fields worth showing


class Evidence(BaseModel):
    """Proof behind a `done`: the scoped readback (D-API-2) and what the call cost."""

    readback: str                               # e.g. "search/Location in Engineering: 1 row, id 13"
    glpi_calls: int = 0
    faults_seen: list[str] = Field(default_factory=list)  # e.g. ["injected_502", "session_expired"]


class Envelope(BaseModel):
    status: Status
    log_id: str                                 # pointer into the decision log

    # done
    outcome: Optional[Outcome] = None
    record: Optional[Record] = None
    records: Optional[list[Record]] = None      # read tools return lists
    evidence: Optional[Evidence] = None

    # refused / unknown
    code: Optional[str] = None
    lane: Optional[Lane] = None
    message: Optional[str] = None
    resolution: Optional[str] = None            # refused: what would resolve it
    what_is_known: Optional[str] = None         # unknown
    next_step: Optional[str] = None             # unknown

    # decisions (conflicts, ambiguous parents)
    diff: Optional[dict[str, Any]] = None
    diff_id: Optional[str] = None
    candidates: Optional[list[Record]] = None

    # extra lines for the human; never change the outcome (P-CONTEXT)
    context: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> "Envelope":
        if self.status is Status.done:
            if self.code is not None:
                raise ValueError("a done envelope carries no code")
            if self.outcome is None and self.records is None:
                raise ValueError("done needs an outcome (writes) or records (reads)")
            if self.outcome is not None and self.evidence is None:
                raise ValueError("a done write needs readback evidence (D-API-2)")
        else:
            if self.code not in CODE_LANES:
                raise ValueError(f"unknown code {self.code!r}; codes are a closed list (T1-3)")
            expected = CODE_LANES[self.code]
            if self.lane is None:
                self.lane = expected
            elif self.lane is not expected:
                raise ValueError(f"code {self.code!r} belongs to lane {expected.value}, not {self.lane.value}")
            if self.outcome is not None:
                raise ValueError("only done envelopes carry an outcome")
            if self.status is Status.refused:
                if self.code in UNKNOWN_CODES:
                    raise ValueError(f"{self.code!r} is an unknown code, not a refusal")
                if not (self.message and self.resolution):
                    raise ValueError("refused needs message and resolution")
            if self.status is Status.unknown:
                if self.code not in UNKNOWN_CODES:
                    raise ValueError(f"{self.code!r} is a refusal code, not an unknown")
                if not (self.what_is_known and self.next_step):
                    raise ValueError("unknown needs what_is_known and next_step")
        return self

    @property
    def is_error(self) -> bool:
        """MCP isError: set whenever status is not done (T1-3 rule 4)."""
        return self.status is not Status.done


def done(log_id: str, outcome: Outcome, record: Record, evidence: Evidence, **extra: Any) -> Envelope:
    return Envelope(status=Status.done, log_id=log_id, outcome=outcome, record=record, evidence=evidence, **extra)


def found(log_id: str, records: list[Record], **extra: Any) -> Envelope:
    """A read tool's answer: the records the UI would list in the target entity."""
    return Envelope(status=Status.done, log_id=log_id, records=records, **extra)


def refused(log_id: str, code: str, message: str, resolution: str, **extra: Any) -> Envelope:
    return Envelope(status=Status.refused, log_id=log_id, code=code, message=message, resolution=resolution, **extra)


def unknown(log_id: str, code: str, what_is_known: str, next_step: str, **extra: Any) -> Envelope:
    return Envelope(status=Status.unknown, log_id=log_id, code=code, what_is_known=what_is_known,
                    next_step=next_step, **extra)
