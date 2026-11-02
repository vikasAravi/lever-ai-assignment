"""The answer envelope enforces its own shape (T1-3)."""
import pytest
from pydantic import ValidationError

from glpi_agents.contracts.envelope import (
    CODE_LANES, Envelope, Evidence, Lane, Outcome, Record, Status, done, found, refused, unknown,
)

REC = Record(type="Location", id=13, entity="Root entity > Acme > India > Engineering", path="Floor 5 Lab", shared=False)
EV = Evidence(readback="search/Location in Engineering: 1 row, id 13", glpi_calls=4)


def test_done_write_needs_readback_evidence():
    with pytest.raises(ValidationError, match="readback evidence"):
        Envelope(status=Status.done, log_id="d-1", outcome=Outcome.created, record=REC)
    env = done("d-1", Outcome.created, REC, EV)
    assert env.is_error is False


def test_done_read_carries_records():
    env = found("d-2", [REC])
    assert env.records == [REC] and env.is_error is False


def test_done_carries_no_code():
    with pytest.raises(ValidationError, match="no code"):
        Envelope(status=Status.done, log_id="d-1", outcome=Outcome.created, record=REC, evidence=EV, code="conflict")


def test_refused_lane_is_derived_from_code():
    env = refused("d-3", "conflict", "jonas.weber exists with another profile.", "Approve or reject the diff.")
    assert env.lane is Lane.decision and env.is_error is True


def test_refused_wrong_lane_is_rejected():
    with pytest.raises(ValidationError, match="belongs to lane"):
        refused("d-3", "conflict", "m", "r", lane=Lane.blocked)


def test_codes_are_a_closed_list():
    with pytest.raises(ValidationError, match="closed list"):
        refused("d-4", "made_up_code", "m", "r")


def test_refused_needs_message_and_resolution():
    with pytest.raises(ValidationError, match="message and resolution"):
        Envelope(status=Status.refused, log_id="d-5", code="out_of_scope", message="outside")


def test_unknown_codes_cannot_be_refusals_and_vice_versa():
    with pytest.raises(ValidationError, match="unknown code, not a refusal"):
        refused("d-6", "write_unknown", "m", "r")
    with pytest.raises(ValidationError, match="refusal code, not an unknown"):
        unknown("d-7", "conflict", "known", "next")


def test_unknown_needs_what_is_known_and_next_step():
    env = unknown("d-8", "write_unknown", "POST answered 504", "Next run searches first.")
    assert env.lane is Lane.unknown and env.is_error is True


def test_every_lane_has_codes_and_the_list_matches_the_spec():
    # Decision: T1-3 / tier1 spec §3.1 — 7 bad input, 5 decision, 5 blocked, 7 unknown.
    counts = {lane: sum(1 for l in CODE_LANES.values() if l is lane) for lane in Lane}
    assert counts == {Lane.bad_input: 7, Lane.decision: 5, Lane.blocked: 5, Lane.unknown: 7}
