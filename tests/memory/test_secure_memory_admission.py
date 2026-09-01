from __future__ import annotations

import base64

import pytest

from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import (
    CandidateProposal,
    ChannelEnvelopeCore,
    EvaluatorDecision,
    FactScope,
    SourceRef,
    TrustedFactCore,
)
from benchmarks.secure_memory.substrate.admission import (
    AdmissionRejected,
    AdmissionStatus,
    apply_admission,
    build_admission_request,
    scan_guest_candidate,
)
from benchmarks.secure_memory.substrate.evaluator_channel import (
    EvaluatorChannelError,
    EvaluatorChannelVerifier,
    EvaluatorSourceReceipt,
    build_evaluator_source_receipt,
)
from benchmarks.secure_memory.substrate.scanner import (
    CandidateBundleScanner,
    CandidateBundleScannerConfig,
    CandidateScanError,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _secret_canaries() -> tuple[str, str, str]:
    """Build scanner canaries without committing key-shaped source literals."""

    provider_key = "api_key=" + "".join(
        ("sk", "-", "abcdefghijklmnopqrstuvwxyz0123456789")
    )
    private_key = "".join(
        (
            "-----BEGIN ",
            "PRIVATE KEY-----\n",
            "not-a-real-key\n",
            "-----END ",
            "PRIVATE KEY-----",
        )
    )
    cloud_key = "AWS_ACCESS_KEY_ID=" + "".join(("AK", "IA", "ABCDEFGHIJKLMNOP"))
    return provider_key, private_key, cloud_key


def _proposal(*, proposal_id: str = "proposal-1", statement: str = "stable fact") -> CandidateProposal:
    statement_b64 = base64.b64encode(statement.encode("utf-8")).decode("ascii")
    return CandidateProposal(
        schema_version="secure-memory-candidate/v1",
        proposal_id=proposal_id,
        task_id="task-1",
        generation=1,
        claimed_fact_id=None,
        statement_utf8_base64=statement_b64,
        memory_type="semantic",
        component="optimizer",
        outcome_claim="KEEP",
        applicability_scope=FactScope(
            tenant_id="tenant-a",
            project_id="project-a",
            component="optimizer",
            version="v1",
            problem_id="problem-1",
        ),
        source_refs=(SourceRef(kind="artifact", identifier="artifact-1"),),
        support_digest_claims=(SHA_A,),
    )


def _envelope(*, output_path: str = "workspace/output/candidate.json") -> ChannelEnvelopeCore:
    return ChannelEnvelopeCore(
        schema_version="secure-memory-channel-envelope/v2",
        envelope_id="envelope-1",
        direction="candidate_to_evaluator",
        channel_epoch=1,
        sequence=1,
        sender_role="candidate_runner",
        recipient_role="control",
        task_id="task-1",
        generation=1,
        campaign_id="campaign-1",
        configuration_id="A",
        execution_phase_owner="A",
        problem_id="problem-1",
        turn=1,
        requirement_ledger_sha256=SHA_A,
        workspace_checkpoint_sha256=SHA_B,
        memory_watermark=7,
        payload_kind="candidate_proposal",
        payload_sha256=SHA_C,
        payload_path=output_path,
        previous_envelope_sha256="0" * 64,
        nonce="nonce-1",
    )


def _decision(
    *,
    proposal: CandidateProposal,
    status: str = "KEEP",
    verified_facts: tuple[TrustedFactCore, ...] = (),
) -> EvaluatorDecision:
    proposal_sha256 = canonical_sha256("candidate-proposal", proposal)
    core = {
        "schema_version": "secure-memory-evaluator-decision/v1",
        "task_id": proposal.task_id,
        "generation": proposal.generation,
        "evaluator_id": "evaluator-1",
        "proposal_sha256": proposal_sha256,
        "status": status,
        "reason_codes": ("SUPPORTED",),
        "verified_facts": verified_facts,
        "verified_relations": (),
        "source_refs": proposal.source_refs,
        "support_digests": proposal.support_digest_claims,
    }
    return EvaluatorDecision(
        **core,
        decision_sha256=canonical_sha256("evaluator-decision", core),
    )


def _source_receipt(
    *,
    admission,
    source_verified: bool = True,
    signature_verified: bool = True,
    key_id: str = "key-1",
    issuer_id: str = "control",
    envelope_sha256: str = SHA_C,
) -> EvaluatorSourceReceipt:
    return build_evaluator_source_receipt(
        admission=admission,
        source_verified=source_verified,
        signature_verified=signature_verified,
        key_id=key_id,
        issuer_id=issuer_id,
        envelope_sha256=envelope_sha256,
    )


def _scan(
    *,
    proposal: CandidateProposal,
    output_bytes: bytes,
    output_path: str = "workspace/output/candidate.json",
    exit_code: int = 0,
    stderr: str = "",
):
    return scan_guest_candidate(
        proposal=proposal,
        output_path=output_path,
        output_bytes=output_bytes,
        exit_code=exit_code,
        stderr=stderr,
    )


def _request(*, proposal: CandidateProposal, scan):
    return build_admission_request(
        campaign_id="campaign-1",
        configuration_id="A",
        execution_phase_owner="A",
        problem_id="problem-1",
        turn=1,
        requirement_ledger_sha256=SHA_A,
        workspace_checkpoint_sha256=SHA_B,
        memory_watermark=7,
        proposal=proposal,
        scan=scan,
        channel_envelope=_envelope(output_path=scan.output_path),
    )


def test_scanner_accepts_exact_canonical_candidate_bundle() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)

    scan = _scan(proposal=proposal, output_bytes=output_bytes)

    assert scan.accepted is True
    assert scan.rejection_codes == ()
    assert scan.observed_size == len(output_bytes)
    assert scan.output_sha256 == canonical_sha256("candidate-output", output_bytes)
    assert scan.candidate_sha256 == canonical_sha256("candidate-proposal", proposal)


def test_scanner_rejects_noncanonical_statement_bytes() -> None:
    proposal = _proposal(statement="stable fact")

    scan = _scan(proposal=proposal, output_bytes=b"stable fact\n")

    assert scan.accepted is False
    assert scan.rejection_codes == ("OUTPUT_STATEMENT_MISMATCH",)


def test_scanner_rejects_path_size_secret_and_process_failures() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)

    bad_path = _scan(
        proposal=proposal,
        output_bytes=output_bytes,
        output_path="workspace/output/other.json",
    )
    assert "OUTPUT_PATH_MISMATCH" in bad_path.rejection_codes

    too_large = _scan(
        proposal=proposal,
        output_bytes=output_bytes,
    ).model_copy(
        update={
            "accepted": False,
            "rejection_codes": ("OUTPUT_TOO_LARGE",),
        }
    )
    assert too_large.accepted is False

    secret_scan = _scan(
        proposal=_proposal(statement="api_key=must-not-leak"),
        output_bytes=b"api_key=must-not-leak",
    )
    assert secret_scan.accepted is False
    assert "SECRET_PATTERN" in secret_scan.rejection_codes

    process_scan = _scan(
        proposal=proposal,
        output_bytes=output_bytes,
        exit_code=1,
        stderr="runner failed",
    )
    assert process_scan.accepted is False
    assert "PROCESS_EXIT_NONZERO" in process_scan.rejection_codes
    assert "PROCESS_STDERR_PRESENT" in process_scan.rejection_codes


@pytest.mark.parametrize("statement", _secret_canaries())
def test_scanner_rejects_common_embedded_secret_values(statement: str) -> None:
    proposal = _proposal(statement=statement)
    output_bytes = statement.encode("utf-8")

    scan = _scan(proposal=proposal, output_bytes=output_bytes)

    assert scan.accepted is False
    assert "SECRET_PATTERN" in scan.rejection_codes


def test_scanner_rejects_nul_control_bytes_even_when_candidate_claims_them() -> None:
    proposal = _proposal(statement="stable\x00fact")
    output_bytes = b"stable\x00fact"

    scan = _scan(proposal=proposal, output_bytes=output_bytes)

    assert scan.accepted is False
    assert "FORBIDDEN_CONTROL_BYTE" in scan.rejection_codes


def test_scanner_fails_closed_when_policy_has_no_limits_or_patterns() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)
    with pytest.raises(CandidateScanError, match="scanner policy"):
        CandidateBundleScanner(
            CandidateBundleScannerConfig(
                allowed_output_path="workspace/output/candidate.json",
                max_output_bytes=0,
                forbidden_patterns=(),
            )
        )
    with pytest.raises(CandidateScanError, match="scanner policy"):
        CandidateBundleScanner(
            CandidateBundleScannerConfig(
                allowed_output_path="workspace/output/candidate.json",
                max_output_bytes=128,
                forbidden_patterns=(),
            )
        )
    with pytest.raises(CandidateScanError, match="scanner policy"):
        CandidateBundleScanner(
            CandidateBundleScannerConfig(
                allowed_output_path="workspace/output/candidate.json",
                max_output_bytes=128,
                forbidden_patterns=(r"(?!)",),
            )
        )

    scan = _scan(proposal=proposal, output_bytes=output_bytes)
    assert scan.accepted is True


def test_admission_rejects_scan_or_envelope_mismatch() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)
    scan = _scan(proposal=proposal, output_bytes=output_bytes)
    request = _request(proposal=proposal, scan=scan)

    receipt = apply_admission(request)
    assert receipt.status is AdmissionStatus.ADMITTED
    assert receipt.candidate_sha256 == canonical_sha256("candidate-proposal", proposal)

    tampered = request.model_copy(
        update={"proposal": _proposal(proposal_id="proposal-2")}
    )
    with pytest.raises(AdmissionRejected, match="candidate digest"):
        apply_admission(tampered)

    bad_scan = scan.model_copy(
        update={
            "accepted": False,
            "rejection_codes": ("SECRET_PATTERN",),
        }
    )
    with pytest.raises(AdmissionRejected, match="scan rejected"):
        apply_admission(
            request.model_copy(
                update={
                    "scan": bad_scan,
                    "scan_sha256": canonical_sha256("candidate-scan", bad_scan),
                }
            )
        )


def test_evaluator_source_verifier_rejects_unverified_replayed_or_wrong_scope_source() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)
    admission = apply_admission(_request(proposal=proposal, scan=_scan(proposal=proposal, output_bytes=output_bytes)))
    decision = _decision(proposal=proposal)
    verifier = EvaluatorChannelVerifier(
        expected_task_id=proposal.task_id,
        expected_generation=proposal.generation,
        expected_key_id="key-1",
        expected_issuer_id="control",
    )
    source = _source_receipt(
        admission=admission,
        envelope_sha256=decision.decision_sha256,
    )

    verifier.verify(decision=decision, source_receipt=source)
    with pytest.raises(EvaluatorChannelError, match="replayed"):
        verifier.verify(decision=decision, source_receipt=source)

    unverified = _source_receipt(
        admission=admission,
        source_verified=False,
        envelope_sha256=decision.decision_sha256,
    )
    with pytest.raises(EvaluatorChannelError, match="not verified"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(decision=decision, source_receipt=unverified)

    wrong_scope = build_evaluator_source_receipt(
        admission=admission.model_copy(update={"task_id": "task-other"}),
        source_verified=True,
        signature_verified=True,
        key_id="key-1",
        issuer_id="control",
        envelope_sha256=decision.decision_sha256,
    )
    with pytest.raises(EvaluatorChannelError, match="scope"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(decision=decision, source_receipt=wrong_scope)


def test_evaluator_source_verifier_rejects_unsigned_or_wrong_identity_source() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)
    admission = apply_admission(
        _request(proposal=proposal, scan=_scan(proposal=proposal, output_bytes=output_bytes))
    )
    decision = _decision(proposal=proposal)

    unsigned = _source_receipt(
        admission=admission,
        signature_verified=False,
        envelope_sha256=decision.decision_sha256,
    )
    with pytest.raises(EvaluatorChannelError, match="signature"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(decision=decision, source_receipt=unsigned)

    wrong_key = _source_receipt(
        admission=admission,
        key_id="key-other",
        envelope_sha256=decision.decision_sha256,
    )
    with pytest.raises(EvaluatorChannelError, match="key"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(decision=decision, source_receipt=wrong_key)

    wrong_issuer = _source_receipt(
        admission=admission,
        issuer_id="untrusted",
        envelope_sha256=decision.decision_sha256,
    )
    with pytest.raises(EvaluatorChannelError, match="issuer"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(decision=decision, source_receipt=wrong_issuer)


def test_evaluator_source_verifier_rejects_payload_not_bound_to_verified_envelope() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)
    admission = apply_admission(
        _request(proposal=proposal, scan=_scan(proposal=proposal, output_bytes=output_bytes))
    )
    decision = _decision(proposal=proposal)
    source = _source_receipt(
        admission=admission,
        envelope_sha256=SHA_A,
    )

    with pytest.raises(EvaluatorChannelError, match="envelope"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(decision=decision, source_receipt=source)


def test_keep_decision_requires_verified_facts_and_support_subset() -> None:
    proposal = _proposal()
    output_bytes = base64.b64decode(proposal.statement_utf8_base64)
    admission = apply_admission(_request(proposal=proposal, scan=_scan(proposal=proposal, output_bytes=output_bytes)))
    source = _source_receipt(
        admission=admission,
        envelope_sha256=canonical_sha256("evaluator-decision", {}),
    )

    with pytest.raises(EvaluatorChannelError, match="verified facts"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(
            decision=_decision(proposal=proposal, status="KEEP", verified_facts=()),
            source_receipt=source,
        )

    fact = TrustedFactCore(
        schema_version="secure-memory-trusted-fact/v1",
        fact_id="fact-1",
        fact_kind="semantic",
        statement_utf8_base64=proposal.statement_utf8_base64,
        outcome="KEEP",
        applicability_scope=proposal.applicability_scope,
        source_refs=proposal.source_refs,
        support_digests=(SHA_B,),
    )
    decision = _decision(proposal=proposal, status="KEEP", verified_facts=(fact,))
    source = _source_receipt(
        admission=admission,
        envelope_sha256=decision.decision_sha256,
    )
    with pytest.raises(EvaluatorChannelError, match="support"):
        EvaluatorChannelVerifier(
            expected_task_id=proposal.task_id,
            expected_generation=proposal.generation,
            expected_key_id="key-1",
            expected_issuer_id="control",
        ).verify(decision=decision, source_receipt=source)
