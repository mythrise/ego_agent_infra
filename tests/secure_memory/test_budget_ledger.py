"""Fail-closed budget reservations are entirely host-side state."""

from dataclasses import replace
import threading

import pytest

from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import (
    ExecutionPhaseOwner,
    IssuedBudgetTicket,
    RequestClass,
    SignedTaskLease,
    SignedTaskLeaseCore,
    TicketTemplate,
)
from benchmarks.secure_memory.substrate.budget import (
    CAMPAIGN_ABSOLUTE,
    CAMPAIGN_RESERVATION,
    BudgetDenied,
    BudgetLedger,
    BudgetTrustContext,
    RawUsage,
    ReservationState,
    SettledUsage,
)


SHA = "a" * 64


def _template(template_id="template-1", request_class=RequestClass.MAIN, **overrides):
    values = dict(schema_version="secure-memory-ticket-template/v1", template_id=template_id,
                  purpose="work", execution_phase_owner=ExecutionPhaseOwner.A, configuration_id="A",
                  problem_id="p1", turn=1, allowed_role="Worker", request_class=request_class,
                  usage_phase="evaluation", slot_id="slot-1", attempt_group="initial",
                  retry_owner=None, max_input_tokens=10000, max_output_tokens=1500)
    values.update(overrides)
    return TicketTemplate(**values)


def _lease(ticket_id="ticket-1", **overrides):
    values = dict(schema_version="secure-memory-task-lease/v1", campaign_id="campaign", configuration_id="A",
                  execution_phase_owner="A", problem_id="p1", turn=1, generation=1,
                  manifest_sha256=SHA, post_selection_extension_sha256=None, policy_sha256=SHA,
                  requirement_ledger_sha256=SHA, workspace_checkpoint_sha256=SHA, memory_watermark=0,
                  project_id="project", task_id="task", worker="worker", matrix_user_id="@worker:test",
                  role="Worker", stage="run", allowed_skills=(), allowed_tools=(), request_class="main",
                  issued_ticket_ids=(ticket_id,), expires_at_sequence=10, issuer_id="control", key_id="k", issue_sequence=1)
    values.update(overrides)
    core = SignedTaskLeaseCore(**values)
    return SignedTaskLease(core=core, core_sha256=canonical_sha256("task-lease-core", core), signature_base64="c2ln")


def _ticket(template_id="template-1", ticket_id="ticket-1", **overrides):
    values = dict(schema_version="secure-memory-issued-budget-ticket/v1", ticket_id=ticket_id,
                  template_id=template_id, campaign_id="campaign", manifest_sha256=SHA,
                  execution_phase_owner="A", configuration_id="A", project_id="project", task_id="task",
                  worker="worker", matrix_user_id="@worker:test", allowed_role="Worker",
                  effective_request_class="main", usage_phase="evaluation", max_input_tokens=10000,
                  max_output_tokens=1500, expires_at_sequence=10, issuer_id="control", key_id="k",
                  issue_sequence=1, ticket_sha256=SHA, signature_base64="c2ln")
    values.update(overrides)
    core = {key: value for key, value in values.items() if key not in {"ticket_sha256", "signature_base64"}}
    values["ticket_sha256"] = canonical_sha256("issued-budget-ticket", core)
    return IssuedBudgetTicket(**values)


def _ledger(template=None, ticket=None, *, calibrated_positive_error=0):
    template = template or _template()
    ticket = ticket or _ticket(template.template_id)
    context = BudgetTrustContext(issuer_id="control", key_id="k", current_sequence=lambda: 1,
                                 signature_verifier=lambda _value: True,
                                 calibrated_positive_error=calibrated_positive_error)
    return BudgetLedger(templates=(template,), tickets=(ticket,), manifest_sha256=SHA, trust_context=context), _lease(ticket.ticket_id)


def _trust(*, calibrated_positive_error=0):
    return BudgetTrustContext(issuer_id="control", key_id="k", current_sequence=lambda: 1,
                              signature_verifier=lambda _value: True,
                              calibrated_positive_error=calibrated_positive_error)


def _rehashed_events(events, mutate):
    """Rebuild a deliberately forged chain without using production hash helpers."""
    previous = ""
    changed_events = []
    for index, event in enumerate(events):
        changed = replace(
            mutate(index, event),
            previous_event_sha256=previous,
            event_sha256="",
        )
        core = {
            "sequence": changed.sequence,
            "ticket_id": changed.ticket_id,
            "template_id": changed.template_id,
            "previous_state": None
            if changed.previous_state is None
            else changed.previous_state.value,
            "new_state": changed.state.value,
            "reserved_input": changed.reserved_input,
            "reserved_output": changed.reserved_output,
            "tokenizer_estimate": changed.tokenizer_estimate,
            "calibrated_positive_error": changed.calibrated_positive_error,
            "model_visible_byte_length": changed.model_visible_byte_length,
            "settled_usage": changed.settled_usage,
            "retained_reason": changed.retained_reason,
            "previous_event_sha256": changed.previous_event_sha256,
        }
        changed = replace(
            changed,
            event_sha256=canonical_sha256("budget-ledger-event", core),
        )
        changed_events.append(changed)
        previous = changed.event_sha256
    return tuple(changed_events)


def _settled_ledger(
    *, calibrated_positive_error=0, tokenizer_estimate=1, visible=b"", raw_usage=None
):
    ledger, lease = _ledger(calibrated_positive_error=calibrated_positive_error)
    ledger.reserve(
        "ticket-1",
        lease,
        requester_role="Worker",
        tokenizer_estimate=tokenizer_estimate,
        calibrated_positive_error=calibrated_positive_error,
        serialized_model_visible_bytes=visible,
    )
    ledger.mark_dispatched("ticket-1")
    ledger.settle(
        "ticket-1",
        raw_usage or RawUsage(input_tokens=1, output_tokens=1),
    )
    return ledger


def test_trusted_context_rejects_stale_ticket_before_reservation():
    ticket = _ticket(expires_at_sequence=0)
    context = BudgetTrustContext(issuer_id="control", key_id="k", current_sequence=lambda: 1,
                                 signature_verifier=lambda _value: True,
                                 calibrated_positive_error=0)
    with pytest.raises(BudgetDenied, match="sequence"):
        BudgetLedger(templates=(_template(),), tickets=(ticket,), manifest_sha256=SHA, trust_context=context)


def test_template_problem_and_turn_bind_the_live_lease_row():
    ledger, _lease_one = _ledger()
    with pytest.raises(BudgetDenied, match="template_row"):
        ledger.reserve("ticket-1", _lease("ticket-1", problem_id="p2"), requester_role="Worker",
                       tokenizer_estimate=1, calibrated_positive_error=0, serialized_model_visible_bytes=b"")


def test_frozen_caps_and_four_request_margin_are_exact():
    assert CAMPAIGN_RESERVATION.requests == 356
    assert CAMPAIGN_RESERVATION.input == 3_306_000
    assert CAMPAIGN_RESERVATION.output == 485_500
    assert CAMPAIGN_ABSOLUTE.requests == 360
    assert CAMPAIGN_ABSOLUTE.requests - CAMPAIGN_RESERVATION.requests == 4
    assert BudgetLedger.absolute_allows(CAMPAIGN_ABSOLUTE)
    assert not BudgetLedger.absolute_allows(CAMPAIGN_ABSOLUTE.add(requests=1))


def test_reserves_conservative_input_and_entire_output_ceiling():
    ledger, lease = _ledger(calibrated_positive_error=20)
    reservation = ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=100,
                                 calibrated_positive_error=20, serialized_model_visible_bytes=b"x" * 700)
    assert reservation.reserved_input == 1724
    assert reservation.reserved_output == 1500
    assert reservation.state is ReservationState.RESERVED
    assert ledger.totals.requests == 1
    assert (
        reservation.tokenizer_estimate,
        reservation.calibrated_positive_error,
        reservation.model_visible_byte_length,
    ) == (100, 20, 700)
    assert not hasattr(reservation, "serialized_model_visible_bytes")


def test_reserve_requires_the_immutable_trusted_calibration_bound():
    ledger, lease = _ledger(calibrated_positive_error=20)
    with pytest.raises(BudgetDenied, match="calibration_binding"):
        ledger.reserve(
            "ticket-1",
            lease,
            requester_role="Worker",
            tokenizer_estimate=100,
            calibrated_positive_error=19,
            serialized_model_visible_bytes=b"",
        )
    assert ledger.events == ()


def test_reservation_rejects_class_overflow_before_consuming_ticket():
    ledger, lease = _ledger()
    with pytest.raises(BudgetDenied, match="input_ceiling"):
        ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=10_000,
                       calibrated_positive_error=0, serialized_model_visible_bytes=b"")
    assert ledger.totals.requests == 0
    assert ledger.reservation_for("ticket-1") is None


def test_ticket_is_one_shot_and_failed_dispatch_retains_budget():
    ledger, lease = _ledger()
    ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=1,
                   calibrated_positive_error=0, serialized_model_visible_bytes=b"")
    ledger.mark_dispatched("ticket-1")
    ledger.retain("ticket-1", "timeout")
    assert ledger.reservation_for("ticket-1").state is ReservationState.RETAINED
    with pytest.raises(BudgetDenied, match="ticket_consumed"):
        ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=1,
                       calibrated_positive_error=0, serialized_model_visible_bytes=b"")


def test_no_cross_role_configuration_or_untrusted_ticket_transfer():
    ledger, lease = _ledger()
    for changed in ({"role": "Other"}, {"configuration_id": "B"}, {"issued_ticket_ids": ()}):
        changed_lease = _lease(**changed)
        with pytest.raises(BudgetDenied):
            ledger.reserve("ticket-1", changed_lease, requester_role="Worker", tokenizer_estimate=1,
                           calibrated_positive_error=0, serialized_model_visible_bytes=b"")
    with pytest.raises(BudgetDenied, match="role"):
        ledger.reserve("ticket-1", lease, requester_role="Other", tokenizer_estimate=1,
                       calibrated_positive_error=0, serialized_model_visible_bytes=b"")


def test_settlement_is_append_only_and_subtotals_do_not_double_count():
    ledger, lease = _ledger()
    ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=1,
                   calibrated_positive_error=0, serialized_model_visible_bytes=b"")
    ledger.mark_dispatched("ticket-1")
    settled = ledger.settle("ticket-1", RawUsage(input_tokens=10, output_tokens=5,
                                                   cache_read_tokens=3, cache_write_tokens=2,
                                                   reasoning_tokens=4))
    assert (settled.budget_input, settled.budget_output) == (10, 5)
    with pytest.raises(BudgetDenied, match="already_terminal"):
        ledger.settle("ticket-1", RawUsage(input_tokens=10, output_tokens=5))
    with pytest.raises(ValueError, match="reasoning"):
        RawUsage(input_tokens=1, output_tokens=1, reasoning_tokens=2)
    with pytest.raises(ValueError, match="cache"):
        RawUsage(input_tokens=1, output_tokens=1, cache_read_tokens=2)


def test_atomic_concurrent_reservation_only_issues_one_slot():
    ledger, lease = _ledger()
    outcomes = []
    def reserve():
        try:
            ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=1,
                           calibrated_positive_error=0, serialized_model_visible_bytes=b"")
            outcomes.append("ok")
        except BudgetDenied:
            outcomes.append("denied")
    threads = [threading.Thread(target=reserve) for _ in range(12)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert outcomes.count("ok") == 1
    assert ledger.totals.requests == 1


def test_caps_are_frozen_and_duplicate_trusted_inputs_fail_closed():
    template = _template()
    ticket = _ticket()
    with pytest.raises(TypeError):
        BudgetLedger(templates=(template,), tickets=(ticket,), manifest_sha256=SHA,
                     reservation_cap=CAMPAIGN_ABSOLUTE)
    with pytest.raises(BudgetDenied, match="duplicate_template"):
        BudgetLedger(templates=(template, template), tickets=(ticket,), manifest_sha256=SHA, trust_context=_trust())
    with pytest.raises(BudgetDenied, match="template_ticket"):
        BudgetLedger(templates=(template,), tickets=(ticket, _ticket(ticket_id="ticket-2", issue_sequence=2)), manifest_sha256=SHA, trust_context=_trust())


def test_ticket_template_bindings_and_state_machine_are_exact():
    template = _template(max_input_tokens=1200, max_output_tokens=10)
    escalated = _ticket(max_input_tokens=10_000, max_output_tokens=1500)
    with pytest.raises(BudgetDenied, match="template_binding"):
        BudgetLedger(templates=(template,), tickets=(escalated,), manifest_sha256=SHA, trust_context=_trust())
    ledger, lease = _ledger()
    ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=1,
                   calibrated_positive_error=0, serialized_model_visible_bytes=b"")
    with pytest.raises(BudgetDenied, match="already_terminal"):
        ledger.settle("ticket-1", RawUsage(input_tokens=1, output_tokens=1))


def test_events_are_hash_chained_and_replayable():
    ledger, lease = _ledger()
    ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=1,
                   calibrated_positive_error=0, serialized_model_visible_bytes=b"")
    ledger.mark_dispatched("ticket-1")
    ledger.settle("ticket-1", RawUsage(input_tokens=1, output_tokens=1))
    assert len(ledger.events) == 3
    assert ledger.events[-1].event_sha256
    replayed = BudgetLedger.replay(templates=(_template(),), tickets=(_ticket(),), manifest_sha256=SHA,
                                   trust_context=_trust(), events=ledger.events,
                                   expected_state_digest=ledger.state_digest)
    assert replayed.totals == ledger.totals


def test_replay_rejects_rehashed_but_underreserved_event_chain():
    ledger = _settled_ledger()
    tampered = _rehashed_events(
        ledger.events,
        lambda _index, event: replace(event, reserved_input=1, reserved_output=1),
    )

    with pytest.raises(BudgetDenied, match="event_reservation"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_rejects_rehashed_calibration_and_reservation_tamper():
    ledger = _settled_ledger(calibrated_positive_error=20, tokenizer_estimate=600)
    tampered = _rehashed_events(
        ledger.events,
        lambda _index, event: replace(
            event,
            calibrated_positive_error=0,
            reserved_input=1_112,
        ),
    )
    with pytest.raises(BudgetDenied, match="event_reservation"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(calibrated_positive_error=20),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_rejects_rehashed_lower_output_reservation():
    ledger = _settled_ledger()
    tampered = _rehashed_events(
        ledger.events,
        lambda _index, event: replace(event, reserved_output=1_499),
    )
    with pytest.raises(BudgetDenied, match="event_reservation"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_rejects_rehashed_basis_drift_between_transitions():
    ledger = _settled_ledger()
    tampered = _rehashed_events(
        ledger.events,
        lambda index, event: replace(event, tokenizer_estimate=2)
        if index == 1
        else event,
    )
    with pytest.raises(BudgetDenied, match="event_transition"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_rejects_rehashed_negative_reservation_basis():
    ledger = _settled_ledger()
    tampered = _rehashed_events(
        ledger.events,
        lambda _index, event: replace(event, tokenizer_estimate=-1),
    )
    with pytest.raises(BudgetDenied, match="event_reservation"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_anchor_rejects_rehashed_lower_basis_and_matching_reservation():
    ledger = _settled_ledger(calibrated_positive_error=20, tokenizer_estimate=600)
    tampered = _rehashed_events(
        ledger.events,
        lambda _index, event: replace(
            event,
            tokenizer_estimate=0,
            reserved_input=1_024,
        ),
    )
    with pytest.raises(BudgetDenied, match="state_anchor"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(calibrated_positive_error=20),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_anchor_rejects_rehashed_lower_byte_basis_and_matching_reservation():
    ledger = _settled_ledger(visible=b"x" * 700)
    tampered = _rehashed_events(
        ledger.events,
        lambda _index, event: replace(
            event,
            model_visible_byte_length=0,
            reserved_input=1_024,
        ),
    )
    with pytest.raises(BudgetDenied, match="state_anchor"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_anchor_rejects_rehashed_lower_terminal_usage():
    ledger = _settled_ledger(raw_usage=RawUsage(input_tokens=100, output_tokens=100))
    forged_usage = SettledUsage(
        raw_usage=RawUsage(input_tokens=1, output_tokens=1),
        budget_input=1,
        budget_output=1,
        comparable_input=1,
        comparable_output=1,
    )
    tampered = _rehashed_events(
        ledger.events,
        lambda index, event: replace(event, settled_usage=forged_usage)
        if index == 2
        else event,
    )
    with pytest.raises(BudgetDenied, match="state_anchor"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(),
            events=tampered,
            expected_state_digest=ledger.state_digest,
        )


def test_replay_requires_well_formed_matching_external_state_anchor():
    ledger = _settled_ledger()
    for invalid in ("", "A" * 64, "0" * 63):
        with pytest.raises(BudgetDenied, match="state_anchor_invalid"):
            BudgetLedger.replay(
                templates=(_template(),),
                tickets=(_ticket(),),
                manifest_sha256=SHA,
                trust_context=_trust(),
                events=ledger.events,
                expected_state_digest=invalid,
            )
    with pytest.raises(BudgetDenied, match="state_anchor_mismatch"):
        BudgetLedger.replay(
            templates=(_template(),),
            tickets=(_ticket(),),
            manifest_sha256=SHA,
            trust_context=_trust(),
            events=ledger.events,
            expected_state_digest="0" * 64,
        )


def test_replay_rejects_event_hash_tamper():
    ledger, lease = _ledger()
    ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=1,
                   calibrated_positive_error=0, serialized_model_visible_bytes=b"")
    bad = list(ledger.events)
    bad[0] = replace(bad[0], event_sha256="0" * 64)
    with pytest.raises(BudgetDenied, match="event_hash"):
        BudgetLedger.replay(templates=(_template(),), tickets=(_ticket(),), manifest_sha256=SHA,
                            trust_context=_trust(), events=bad,
                            expected_state_digest=ledger.state_digest)


def test_replay_two_ticket_settled_and_retained_round_trip():
    first = _template()
    second = _template(template_id="template-2", slot_id="slot-2")
    one = _ticket()
    two = _ticket(template_id="template-2", ticket_id="ticket-2", issue_sequence=2)
    trust = BudgetTrustContext(issuer_id="control", key_id="k", current_sequence=lambda: 2,
                               signature_verifier=lambda _value: True,
                               calibrated_positive_error=0)
    ledger = BudgetLedger(templates=(first, second), tickets=(one, two), manifest_sha256=SHA, trust_context=trust)
    lease = _lease("ticket-1", issued_ticket_ids=("ticket-1", "ticket-2"))
    for ticket_id in ("ticket-1", "ticket-2"):
        ledger.reserve(ticket_id, lease, requester_role="Worker", tokenizer_estimate=1, calibrated_positive_error=0, serialized_model_visible_bytes=b"")
        ledger.mark_dispatched(ticket_id)
    ledger.settle("ticket-1", RawUsage(input_tokens=1, output_tokens=1))
    ledger.retain("ticket-2", "timeout")
    replay = BudgetLedger.replay(templates=(first, second), tickets=(one, two), manifest_sha256=SHA, trust_context=trust, events=ledger.events, expected_state_digest=ledger.state_digest)
    assert replay.events == ledger.events and replay.totals == ledger.totals and replay.state_digest == ledger.state_digest
    assert replay.reservation_for("ticket-1") == ledger.reservation_for("ticket-1")
    assert replay.reservation_for("ticket-2") == ledger.reservation_for("ticket-2")
