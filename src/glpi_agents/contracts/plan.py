"""The plan Agent A builds from the workbook and executes (agent-a-spec.md A1, A2.4).

Code builds the plan; the model executes it; the runtime rejects any tool call that
differs from its step. A step refused at intake carries its envelope and no tool call.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from pydantic import BaseModel, Field

from .envelope import Envelope


class PlanStep(BaseModel):
    step_id: str                                 # stable, e.g. "Users!4"
    sheet: str
    row: int
    tool: Optional[str] = None                   # None when refused at intake
    args: dict[str, Any] = Field(default_factory=dict)   # exact, normalised (A2)
    depends_on: list[str] = Field(default_factory=list)  # step ids (P-ORDER)
    intake: Optional[Envelope] = None            # set when refused at intake
    context: list[str] = Field(default_factory=list)     # e.g. "value trimmed", "duplicate_row of Users!2"


class Plan(BaseModel):
    workbook_sha256: str
    approvals_sha256: Optional[str] = None
    session_root: str                            # the session root entity the plan was built for
    steps: list[PlanStep]                        # execution order (dependencies first)

    def plan_hash(self) -> str:
        """Same workbook + same GLPI state -> same plan -> same hash (A2.4)."""
        payload = self.model_dump(mode="json", exclude={"approvals_sha256"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
