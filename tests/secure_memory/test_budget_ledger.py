"""Fail-closed budget reservations are entirely host-side state."""

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
    RawUsage,
    ReservationState,
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
    return IssuedBudgetTicket(**values)


def _ledger(template=None, ticket=None):
    template = template or _template()
    ticket = ticket or _ticket(template.template_id)
    return BudgetLedger(templates=(template,), tickets=(ticket,), manifest_sha256=SHA), _lease(ticket.ticket_id)


def test_frozen_caps_and_four_request_margin_are_exact():
    assert CAMPAIGN_RESERVATION.requests == 356
    assert CAMPAIGN_RESERVATION.input == 3_306_000
    assert CAMPAIGN_RESERVATION.output == 485_500
    assert CAMPAIGN_ABSOLUTE.requests == 360
    assert CAMPAIGN_ABSOLUTE.requests - CAMPAIGN_RESERVATION.requests == 4
    assert BudgetLedger.absolute_allows(CAMPAIGN_ABSOLUTE)
    assert not BudgetLedger.absolute_allows(CAMPAIGN_ABSOLUTE.add(requests=1))


def test_reserves_conservative_input_and_entire_output_ceiling():
    ledger, lease = _ledger()
    reservation = ledger.reserve("ticket-1", lease, requester_role="Worker", tokenizer_estimate=100,
                                 calibrated_positive_error=20, serialized_model_visible_bytes=b"x" * 700)
    assert reservation.reserved_input == 1724
    assert reservation.reserved_output == 1500
    assert reservation.state is ReservationState.RESERVED
    assert ledger.totals.requests == 1


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
