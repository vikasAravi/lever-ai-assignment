"""Tool inputs, the design access map, and the plan hash (T1-2, T1-4, A2.4)."""
import pytest
from pydantic import ValidationError

from glpi_agents.contracts.plan import Plan, PlanStep
from glpi_agents.contracts.tools import ROLE_TOOLSETS, TOOL_INPUTS, WRITE_TOOLS, EnsureLocationIn


def test_access_map_sizes_match_the_design():
    assert len(ROLE_TOOLSETS["onboarding"]) == 14
    assert len(ROLE_TOOLSETS["inventory"]) == 9
    assert set().union(*ROLE_TOOLSETS.values()) == set(TOOL_INPUTS)  # 19 unique tools, none orphaned
    assert len(TOOL_INPUTS) == 19


def test_every_write_tool_has_a_read_twin_in_the_same_role():
    twins = {
        "ensure_location": "find_locations", "ensure_group": "find_groups", "ensure_user": "find_users",
        "ensure_supplier": "find_suppliers", "ensure_category": "find_categories",
        "update_computer": "find_computers", "set_computer_state": "find_computers",
        "open_discrepancy_ticket": "find_discrepancy_tickets",
    }
    assert set(twins) | {"apply_approved_change"} == WRITE_TOOLS
    for role, tools in ROLE_TOOLSETS.items():
        for write, read in twins.items():
            if write in tools:
                assert read in tools, f"{role}: {write} without its read twin {read}"


def test_no_role_gets_a_delete_or_generic_tool():
    for tools in ROLE_TOOLSETS.values():
        assert not any(t.startswith(("delete", "purge", "call", "raw")) for t in tools)


def test_unknown_tool_arguments_are_rejected():
    with pytest.raises(ValidationError):
        EnsureLocationIn(entity="Root entity > Acme", path="Floor 5 Lab", shared=False, session_root="Root entity")


def _plan(**over):
    step = PlanStep(step_id="Locations!5", sheet="Locations", row=5, tool="ensure_location",
                    args={"entity": "Root entity > Acme > India > Engineering", "path": "Floor 5 Lab", "shared": False})
    base = dict(workbook_sha256="ab" * 32, session_root="Root entity > Acme", steps=[step])
    base.update(over)
    return Plan(**base)


def test_plan_hash_is_stable_and_ignores_the_approvals_file():
    assert _plan().plan_hash() == _plan().plan_hash()
    assert _plan(approvals_sha256="cd" * 32).plan_hash() == _plan().plan_hash()


def test_plan_hash_changes_when_an_argument_changes():
    other = _plan()
    other.steps[0].args["shared"] = True
    assert other.plan_hash() != _plan().plan_hash()
