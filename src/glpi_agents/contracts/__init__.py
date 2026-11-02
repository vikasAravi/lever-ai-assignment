"""Shared contracts: the interfaces every area codes against.

Owned by the lead (WORKLOG.md §1). An area that needs a change here asks the lead with a
handoff (`.claude/skills/handoff`); it never edits these files on its own branch.
"""
from .envelope import CODE_LANES, Envelope, Evidence, Lane, Outcome, Record, Status, done, found, refused, unknown
from .tools import ROLE_TOOLSETS, TOOL_INPUTS, WRITE_TOOLS

__all__ = [
    "CODE_LANES", "Envelope", "Evidence", "Lane", "Outcome", "Record", "Status",
    "done", "found", "refused", "unknown",
    "ROLE_TOOLSETS", "TOOL_INPUTS", "WRITE_TOOLS",
]
