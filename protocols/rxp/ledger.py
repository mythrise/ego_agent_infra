"""Append-only matrix ledger and per-cell RXP state machine."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple, Type, TypeVar, Union, cast

from pydantic import ValidationError

from .canonical import (
    GENESIS_ROOT,
    canonical_bytes,
    digest_document,
    digest_ledger_entry,
    extend_ledger_root,
)
from .errors import RXPError
from .evidence import evidence_gate
from .grants import CLOCK_SKEW_SECONDS, GrantSigner, ReplayRegistry, parse_utc
from .models import (
    CellSnapshot,
    CellState,
    Decision,
    DeterminismLevel,
    Evidence,
    GateAssessment,
    Grant,
    Intent,
    LedgerEntry,
    LedgerEntryCore,
    LedgerEventType,
    MatrixCellDefinition,
    MatrixLedgerDocument,
    MatrixPlan,
    Receipt,
    StrictModel,
)

Document = Union[MatrixPlan, Intent, Grant, Receipt, Evidence, Decision]
ModelT = TypeVar("ModelT", bound=StrictModel)


@dataclass
class _Cell:
    intent: Intent
    intent_digest: str
    state: CellState = CellState.INTENT_RECORDED
    grant: Grant | None = None
    grant_digest: str | None = None
    receipt: Receipt | None = None
    receipt_digest: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    evidence_digests: list[str] = field(default_factory=list)
    decision: Decision | None = None
    decision_digest: str | None = None

    def snapshot(self) -> CellSnapshot:
        determinism = (
            self.receipt.determinism_level
            if self.receipt is not None
            else DeterminismLevel.D0_UNVERIFIED
        )
        return CellSnapshot(
            cell_id=self.intent.cell_id,
            state=self.state,
            intent_digest=self.intent_digest,
            grant_digest=self.grant_digest,
            receipt_digest=self.receipt_digest,
            evidence_digests=tuple(sorted(self.evidence_digests)),
            decision_digest=self.decision_digest,
            determinism_level=determinism,
        )


class MatrixLedger:
    """Thread-safe reference state machine for one experiment matrix."""

    def __init__(self, matrix_plan: MatrixPlan) -> None:
        self.matrix_id = matrix_plan.matrix_id
        self.matrix_plan = matrix_plan
        self.matrix_plan_digest = digest_document(matrix_plan)
        self._expected_cells: dict[str, MatrixCellDefinition] = {
            cell.cell_id: cell for cell in matrix_plan.cells
        }
        self._entries: list[LedgerEntry] = []
        self._cells: dict[str, _Cell] = {}
        self._root = GENESIS_ROOT
        self._grant_ids: set[str] = set()
        self._intent_ids: set[str] = set()
        self._lock = threading.RLock()
        self._append(
            "MATRIX_FROZEN",
            matrix_plan,
            causal_parents=(),
            recorded_at=matrix_plan.frozen_at,
        )

    @property
    def root(self) -> str:
        with self._lock:
            return self._root

    def record_intent(self, intent: Intent) -> str:
        with self._lock:
            self._require_matrix(intent.matrix_id)
            try:
                definition = self._expected_cells[intent.cell_id]
            except KeyError as exc:
                raise RXPError(
                    "cell_not_declared", "Intent cell is absent from the frozen matrix"
                ) from exc
            if intent.coordinates != definition.coordinates:
                raise RXPError(
                    "cell_coordinates_mismatch",
                    "Intent coordinates do not match the frozen matrix cell",
                )
            if intent.cell_id in self._cells:
                raise RXPError("cell_already_exists", "A matrix cell accepts exactly one Intent")
            if intent.intent_id in self._intent_ids:
                raise RXPError("intent_replayed", "intent_id already exists in this matrix")
            digest = digest_document(intent)
            self._append(
                "INTENT_RECORDED",
                intent,
                causal_parents=(),
                recorded_at=intent.created_at,
            )
            self._cells[intent.cell_id] = _Cell(intent=intent, intent_digest=digest)
            self._intent_ids.add(intent.intent_id)
            return digest

    def record_grant(
        self, grant: Grant, *, verifier: GrantSigner, accepted_at: str
    ) -> str:
        with self._lock:
            claims = grant.claims
            self._require_matrix(claims.matrix_id)
            cell = self._require_cell(claims.cell_id)
            self._require_state(cell, CellState.INTENT_RECORDED)
            if claims.grant_id in self._grant_ids:
                raise RXPError("grant_id_replayed", "grant_id already exists in this matrix")
            verifier.verify(grant, cell.intent, checked_at=accepted_at)
            digest = digest_document(grant)
            self._append(
                "GRANT_RECORDED",
                grant,
                causal_parents=(cell.intent_digest,),
                recorded_at=accepted_at,
            )
            cell.grant = grant
            cell.grant_digest = digest
            cell.state = CellState.GRANTED
            self._grant_ids.add(claims.grant_id)
            return digest

    def record_receipt(self, receipt: Receipt, *, replay_registry: ReplayRegistry) -> str:
        with self._lock:
            self._require_matrix(receipt.matrix_id)
            cell = self._require_cell(receipt.cell_id)
            self._require_state(cell, CellState.GRANTED)
            grant = cell.grant
            if grant is None or cell.grant_digest is None:
                raise RXPError("ledger_corrupt", "Granted cell is missing its Grant")
            mismatches: list[str] = []
            expected = {
                "intent_digest": cell.intent_digest,
                "grant_digest": cell.grant_digest,
                "grant_id": grant.claims.grant_id,
            }
            for name, value in expected.items():
                if getattr(receipt, name) != value:
                    mismatches.append(name)
            if mismatches:
                raise RXPError(
                    "receipt_causality_mismatch",
                    "Receipt does not descend from this exact Intent and Grant",
                    {"mismatched_fields": sorted(mismatches)},
                )
            self._validate_receipt(receipt, grant, cell.intent)
            self._assert_append_time(receipt.completed_at)
            # Perform fallible canonicalization before the irreversible replay write.
            canonical_bytes(receipt)
            if not replay_registry.consume("rxp-grant", grant.claims.grant_id):
                raise RXPError("grant_replayed", "Grant has already been consumed")
            digest = digest_document(receipt)
            self._append(
                "RECEIPT_RECORDED",
                receipt,
                causal_parents=(cell.intent_digest, cell.grant_digest),
                recorded_at=receipt.completed_at,
            )
            cell.receipt = receipt
            cell.receipt_digest = digest
            cell.state = CellState.RECEIPT_RECORDED
            return digest

    def record_evidence(self, evidence: Evidence) -> str:
        with self._lock:
            self._require_matrix(evidence.matrix_id)
            cell = self._require_cell(evidence.cell_id)
            if cell.state not in (CellState.RECEIPT_RECORDED, CellState.EVIDENCE_READY):
                raise RXPError(
                    "cell_state_invalid", "Evidence requires a recorded Receipt"
                )
            if cell.receipt_digest is None or evidence.receipt_digest != cell.receipt_digest:
                raise RXPError(
                    "evidence_causality_mismatch", "Evidence targets a different Receipt"
                )
            if evidence.evidence_id in {item.evidence_id for item in cell.evidence}:
                raise RXPError("evidence_replayed", "evidence_id already exists in this cell")
            digest = digest_document(evidence)
            if digest in cell.evidence_digests:
                raise RXPError("evidence_replayed", "Evidence document is already recorded")
            self._append(
                "EVIDENCE_RECORDED",
                evidence,
                causal_parents=(cell.receipt_digest,),
                recorded_at=evidence.observed_at,
            )
            cell.evidence.append(evidence)
            cell.evidence_digests.append(digest)
            if evidence_gate(cell.evidence).status == "PASS":
                cell.state = CellState.EVIDENCE_READY
            return digest

    def assess_evidence(self, cell_id: str) -> GateAssessment:
        """Return the deterministic gate assessment for a cell."""

        with self._lock:
            cell = self._require_cell(cell_id)
            return evidence_gate(tuple(cell.evidence))

    def record_decision(self, decision: Decision) -> str:
        with self._lock:
            self._require_matrix(decision.matrix_id)
            cell = self._require_cell(decision.cell_id)
            self._require_state(cell, CellState.EVIDENCE_READY)
            if cell.receipt is None or cell.receipt_digest is None:
                raise RXPError("ledger_corrupt", "Evidence-ready cell is missing a Receipt")
            if cell.receipt.outcome != "SUCCEEDED":
                raise RXPError("receipt_failed", "A failed execution cannot yield a Decision")
            gate = evidence_gate(cell.evidence)
            if decision.gate != gate:
                raise RXPError("decision_gate_mismatch", "Decision embeds a stale or altered gate")
            expected = {
                "intent_digest": cell.intent_digest,
                "receipt_digest": cell.receipt_digest,
            }
            mismatches = [
                name for name, value in expected.items() if getattr(decision, name) != value
            ]
            if tuple(sorted(cell.evidence_digests)) != decision.evidence_digests:
                mismatches.append("evidence_digests")
            if decision.determinism_level != cell.receipt.determinism_level:
                mismatches.append("determinism_level")
            if mismatches:
                raise RXPError(
                    "decision_causality_mismatch",
                    "Decision is not bound to the complete cell evidence chain",
                    {"mismatched_fields": sorted(mismatches)},
                )
            if (
                decision.determinism_level.rank
                < cell.intent.required_determinism.rank
            ):
                raise RXPError(
                    "decision_determinism_too_weak",
                    "Receipt determinism is below the frozen Intent requirement",
                )
            digest = digest_document(decision)
            parents = (
                cell.intent_digest,
                cell.receipt_digest,
                *tuple(sorted(cell.evidence_digests)),
            )
            self._append(
                "DECISION_RECORDED",
                decision,
                causal_parents=parents,
                recorded_at=decision.decided_at,
            )
            cell.decision = decision
            cell.decision_digest = digest
            cell.state = CellState.DECIDED
            return digest

    def snapshot(self) -> MatrixLedgerDocument:
        with self._lock:
            decided = {
                cell_id
                for cell_id, cell in self._cells.items()
                if cell.state == CellState.DECIDED
            }
            missing = tuple(sorted(set(self._expected_cells) - decided))
            return MatrixLedgerDocument(
                matrix_id=self.matrix_id,
                matrix_plan_digest=self.matrix_plan_digest,
                expected_cell_count=len(self._expected_cells),
                decided_cell_count=len(decided),
                missing_decisions=missing,
                completeness="COMPLETE" if not missing else "INCOMPLETE",
                entry_count=len(self._entries),
                root=self._root,
                entries=tuple(self._entries),
                cells=tuple(self._cells[key].snapshot() for key in sorted(self._cells)),
            )

    def _append(
        self,
        event_type: LedgerEventType,
        document: Document,
        *,
        causal_parents: Tuple[str, ...],
        recorded_at: str,
    ) -> None:
        self._assert_append_time(recorded_at)
        document_dict = json.loads(canonical_bytes(document))
        document_digest = digest_document(document)
        core = LedgerEntryCore(
            sequence=len(self._entries) + 1,
            event_type=event_type,
            matrix_id=self.matrix_id,
            cell_id=_document_cell_id(document),
            document_kind=document.kind,
            document_digest=document_digest,
            document=document_dict,
            causal_parents=causal_parents,
            previous_root=self._root,
            recorded_at=recorded_at,
        )
        entry_digest = digest_ledger_entry(core)
        root = extend_ledger_root(self._root, entry_digest)
        entry = LedgerEntry(
            **core.model_dump(), entry_digest=entry_digest, root=root
        )
        self._entries.append(entry)
        self._root = root

    def _assert_append_time(self, recorded_at: str) -> None:
        if self._entries and parse_utc(recorded_at) < parse_utc(self._entries[-1].recorded_at):
            raise RXPError("ledger_time_regression", "Ledger time cannot move backwards")

    @staticmethod
    def _validate_receipt(receipt: Receipt, grant: Grant, intent: Intent) -> None:
        started = parse_utc(receipt.started_at)
        completed = parse_utc(receipt.completed_at)
        issued = parse_utc(grant.claims.issued_at)
        expires = parse_utc(grant.claims.expires_at)
        if completed < started:
            raise RXPError("receipt_time_invalid", "Receipt completed before it started")
        if started.timestamp() + CLOCK_SKEW_SECONDS < issued.timestamp():
            raise RXPError("grant_not_yet_valid", "Execution started before Grant issuance")
        if started >= expires:
            raise RXPError("grant_expired", "Execution started after Grant expiry")
        usage = receipt.usage
        bounds = grant.claims.bounds
        exceeded = []
        if usage.gpu_count > bounds.max_gpu_count:
            exceeded.append("gpu_count")
        if usage.wall_time_seconds > bounds.max_wall_time_seconds:
            exceeded.append("wall_time_seconds")
        if usage.gpu_time_seconds > bounds.max_gpu_time_seconds:
            exceeded.append("gpu_time_seconds")
        if usage.artifact_bytes > bounds.max_artifact_bytes:
            exceeded.append("artifact_bytes")
        if receipt.output.bytes != usage.artifact_bytes:
            exceeded.append("output.bytes")
        if exceeded:
            raise RXPError(
                "grant_bounds_exceeded",
                "Receipt usage exceeds the signed Grant",
                {"fields": sorted(exceeded)},
            )
        if receipt.determinism_level.rank < grant.claims.minimum_determinism.rank:
            raise RXPError(
                "receipt_determinism_too_weak",
                "Receipt determinism is below the signed Grant minimum",
            )
        if receipt.determinism_level.rank < intent.required_determinism.rank:
            raise RXPError(
                "receipt_determinism_too_weak",
                "Receipt determinism is below the frozen Intent requirement",
            )

    def _require_matrix(self, matrix_id: str) -> None:
        if matrix_id != self.matrix_id:
            raise RXPError("matrix_mismatch", "Document belongs to a different matrix")

    def _require_cell(self, cell_id: str) -> _Cell:
        try:
            return self._cells[cell_id]
        except KeyError as exc:
            raise RXPError("cell_unknown", "Matrix cell has no recorded Intent") from exc

    @staticmethod
    def _require_state(cell: _Cell, expected: CellState) -> None:
        if cell.state != expected:
            raise RXPError(
                "cell_state_invalid",
                "Operation is not valid in the current cell state",
                {"expected": expected.value, "actual": cell.state.value},
            )


def _document_cell_id(document: Document) -> str:
    if isinstance(document, MatrixPlan):
        return "matrix"
    return document.claims.cell_id if isinstance(document, Grant) else document.cell_id


_KIND_MODEL: Dict[str, Type[StrictModel]] = {
    "MatrixPlan": MatrixPlan,
    "Intent": Intent,
    "Grant": Grant,
    "Receipt": Receipt,
    "Evidence": Evidence,
    "Decision": Decision,
}


def _parse_model(model: Type[ModelT], value: dict[str, Any]) -> ModelT:
    try:
        return model.model_validate_json(canonical_bytes(value))
    except ValidationError as exc:
        raise RXPError("document_invalid", f"Invalid {model.__name__} document") from exc


def verify_ledger_document(value: MatrixLedgerDocument | bytes | str | dict[str, Any]) -> None:
    """Verify hashes, causal links, transitions, evidence gate, and final snapshot.

    Signature trust is intentionally separate: call ``verify_grant_signatures`` with
    the deployment's key resolver after structural verification.
    """

    if isinstance(value, MatrixLedgerDocument):
        document = value
    else:
        try:
            if isinstance(value, bytes):
                document = MatrixLedgerDocument.model_validate_json(value)
            elif isinstance(value, str):
                document = MatrixLedgerDocument.model_validate_json(value)
            else:
                document = MatrixLedgerDocument.model_validate_json(canonical_bytes(value))
        except ValidationError as exc:
            raise RXPError("ledger_invalid", "MatrixLedger document is malformed") from exc

    root = GENESIS_ROOT
    cells: dict[str, _Cell] = {}
    matrix_plan: MatrixPlan | None = None
    seen_digests: set[str] = set()
    seen_intent_ids: set[str] = set()
    seen_grant_ids: set[str] = set()
    previous_timestamp: float | None = None
    for expected_sequence, entry in enumerate(document.entries, start=1):
        if entry.sequence != expected_sequence:
            raise RXPError("ledger_sequence_invalid", "Ledger sequence is not contiguous")
        if entry.matrix_id != document.matrix_id or entry.previous_root != root:
            raise RXPError("ledger_chain_invalid", "Ledger root predecessor does not match")
        model = _KIND_MODEL[entry.document_kind]
        parsed = cast(Document, _parse_model(model, entry.document))
        if _document_cell_id(parsed) != entry.cell_id:
            raise RXPError("document_cell_mismatch", "Entry cell differs from document cell")
        parsed_matrix_id = (
            parsed.claims.matrix_id if isinstance(parsed, Grant) else parsed.matrix_id
        )
        if parsed_matrix_id != entry.matrix_id:
            raise RXPError("document_matrix_mismatch", "Entry matrix differs from document matrix")
        current_timestamp = parse_utc(entry.recorded_at).timestamp()
        if previous_timestamp is not None and current_timestamp < previous_timestamp:
            raise RXPError("ledger_time_regression", "Ledger time moves backwards")
        previous_timestamp = current_timestamp
        if digest_document(parsed) != entry.document_digest:
            raise RXPError("document_digest_mismatch", "Embedded document was altered")
        if entry.document_digest in seen_digests:
            raise RXPError("document_replayed", "Document digest appears more than once")
        if isinstance(parsed, Intent):
            if parsed.intent_id in seen_intent_ids:
                raise RXPError("intent_replayed", "intent_id appears more than once")
            seen_intent_ids.add(parsed.intent_id)
        elif isinstance(parsed, Grant):
            if parsed.claims.grant_id in seen_grant_ids:
                raise RXPError("grant_id_replayed", "grant_id appears more than once")
            seen_grant_ids.add(parsed.claims.grant_id)
        core = LedgerEntryCore(**entry.model_dump(exclude={"entry_digest", "root"}))
        computed_entry = digest_ledger_entry(core)
        if computed_entry != entry.entry_digest:
            raise RXPError("entry_digest_mismatch", "Ledger entry was altered")
        root = extend_ledger_root(root, computed_entry)
        if root != entry.root:
            raise RXPError("ledger_root_mismatch", "Ledger root does not match entry chain")
        if any(parent not in seen_digests for parent in entry.causal_parents):
            raise RXPError("causal_parent_missing", "Ledger causal parent was not recorded earlier")
        if isinstance(parsed, MatrixPlan):
            if (
                expected_sequence != 1
                or matrix_plan is not None
                or entry.event_type != "MATRIX_FROZEN"
                or entry.cell_id != "matrix"
                or entry.causal_parents
                or parsed.matrix_id != document.matrix_id
            ):
                raise RXPError("transition_invalid", "Invalid frozen MatrixPlan transition")
            matrix_plan = parsed
        else:
            if matrix_plan is None:
                raise RXPError("transition_invalid", "MatrixPlan must be the first entry")
            _verify_transition(cells, entry, parsed, matrix_plan)
        _verify_recorded_at(entry, parsed)
        seen_digests.add(entry.document_digest)

    if matrix_plan is None:
        raise RXPError("matrix_plan_missing", "Ledger has no frozen MatrixPlan")
    expected_cells = tuple(cells[key].snapshot() for key in sorted(cells))
    decided = {
        cell_id for cell_id, cell in cells.items() if cell.state == CellState.DECIDED
    }
    plan_cells = {cell.cell_id for cell in matrix_plan.cells}
    missing = tuple(sorted(plan_cells - decided))
    if (
        root != document.root
        or expected_cells != document.cells
        or digest_document(matrix_plan) != document.matrix_plan_digest
        or document.expected_cell_count != len(plan_cells)
        or document.decided_cell_count != len(decided)
        or document.missing_decisions != missing
    ):
        raise RXPError("ledger_snapshot_mismatch", "Ledger summary does not match its entries")


def _verify_transition(
    cells: dict[str, _Cell],
    entry: LedgerEntry,
    document: StrictModel,
    matrix_plan: MatrixPlan,
) -> None:
    cell_id = entry.cell_id
    if isinstance(document, Intent):
        if entry.event_type != "INTENT_RECORDED" or entry.causal_parents or cell_id in cells:
            raise RXPError("transition_invalid", "Invalid Intent transition")
        definitions = {cell.cell_id: cell for cell in matrix_plan.cells}
        if cell_id not in definitions or document.coordinates != definitions[cell_id].coordinates:
            raise RXPError("cell_coordinates_mismatch", "Intent is outside the frozen matrix")
        cells[cell_id] = _Cell(intent=document, intent_digest=entry.document_digest)
        return
    if cell_id not in cells:
        raise RXPError("transition_invalid", "Cell document precedes its Intent")
    cell = cells[cell_id]
    if isinstance(document, Grant):
        if cell.state != CellState.INTENT_RECORDED or entry.event_type != "GRANT_RECORDED":
            raise RXPError("transition_invalid", "Invalid Grant transition")
        if entry.causal_parents != (cell.intent_digest,):
            raise RXPError("causal_parent_mismatch", "Grant parent is not the cell Intent")
        claims = document.claims
        if (
            claims.intent_digest != cell.intent_digest
            or claims.intent_id != cell.intent.intent_id
            or claims.matrix_id != cell.intent.matrix_id
            or claims.cell_id != cell.intent.cell_id
            or claims.action != cell.intent.action
            or claims.scope != cell.intent.scope
            or claims.action_payload_digest != cell.intent.action_payload_digest
            or not claims.bounds.contains(cell.intent.requested_resources)
        ):
            raise RXPError("grant_scope_mismatch", "Grant does not match the cell Intent")
        cell.grant = document
        cell.grant_digest = entry.document_digest
        cell.state = CellState.GRANTED
        return
    if isinstance(document, Receipt):
        if cell.state != CellState.GRANTED or entry.event_type != "RECEIPT_RECORDED":
            raise RXPError("transition_invalid", "Invalid Receipt transition")
        if cell.grant is None or cell.grant_digest is None:
            raise RXPError("ledger_corrupt", "Receipt cell has no Grant")
        if entry.causal_parents != (cell.intent_digest, cell.grant_digest):
            raise RXPError("causal_parent_mismatch", "Receipt parents do not match cell")
        if (
            document.intent_digest != cell.intent_digest
            or document.grant_digest != cell.grant_digest
            or document.grant_id != cell.grant.claims.grant_id
        ):
            raise RXPError("receipt_causality_mismatch", "Receipt does not match cell")
        MatrixLedger._validate_receipt(document, cell.grant, cell.intent)
        cell.receipt = document
        cell.receipt_digest = entry.document_digest
        cell.state = CellState.RECEIPT_RECORDED
        return
    if isinstance(document, Evidence):
        if cell.state not in (CellState.RECEIPT_RECORDED, CellState.EVIDENCE_READY):
            raise RXPError("transition_invalid", "Invalid Evidence transition")
        if (
            cell.receipt_digest is None
            or document.receipt_digest != cell.receipt_digest
            or entry.causal_parents != (cell.receipt_digest,)
        ):
            raise RXPError("evidence_causality_mismatch", "Evidence does not match Receipt")
        if document.evidence_id in {item.evidence_id for item in cell.evidence}:
            raise RXPError("evidence_replayed", "Duplicate evidence_id in ledger")
        cell.evidence.append(document)
        cell.evidence_digests.append(entry.document_digest)
        if evidence_gate(cell.evidence).status == "PASS":
            cell.state = CellState.EVIDENCE_READY
        return
    if isinstance(document, Decision):
        if cell.state != CellState.EVIDENCE_READY or entry.event_type != "DECISION_RECORDED":
            raise RXPError("transition_invalid", "Invalid Decision transition")
        if cell.receipt is None or cell.receipt_digest is None:
            raise RXPError("ledger_corrupt", "Decision cell has no Receipt")
        gate = evidence_gate(cell.evidence)
        parents = (
            cell.intent_digest,
            cell.receipt_digest,
            *tuple(sorted(cell.evidence_digests)),
        )
        if entry.causal_parents != parents or document.gate != gate:
            raise RXPError("decision_gate_mismatch", "Decision gate or parents do not match")
        if (
            document.intent_digest != cell.intent_digest
            or document.receipt_digest != cell.receipt_digest
            or document.evidence_digests != tuple(sorted(cell.evidence_digests))
            or document.determinism_level != cell.receipt.determinism_level
        ):
            raise RXPError("decision_causality_mismatch", "Decision does not match cell")
        if cell.receipt.outcome != "SUCCEEDED":
            raise RXPError("receipt_failed", "A failed execution cannot yield a Decision")
        cell.decision = document
        cell.decision_digest = entry.document_digest
        cell.state = CellState.DECIDED
        return
    raise RXPError("document_kind_unknown", "Unsupported document kind")


def _verify_recorded_at(entry: LedgerEntry, document: StrictModel) -> None:
    if isinstance(document, MatrixPlan):
        expected = document.frozen_at
    elif isinstance(document, Intent):
        expected = document.created_at
    elif isinstance(document, Receipt):
        expected = document.completed_at
    elif isinstance(document, Evidence):
        expected = document.observed_at
    elif isinstance(document, Decision):
        expected = document.decided_at
    elif isinstance(document, Grant):
        recorded = parse_utc(entry.recorded_at)
        issued = parse_utc(document.claims.issued_at)
        expires = parse_utc(document.claims.expires_at)
        if recorded.timestamp() + CLOCK_SKEW_SECONDS < issued.timestamp() or recorded >= expires:
            raise RXPError("grant_record_time_invalid", "Grant was recorded outside its window")
        return
    else:
        raise RXPError("document_kind_unknown", "Unsupported document kind")
    if entry.recorded_at != expected:
        raise RXPError("recorded_at_mismatch", "Entry time differs from document time")


def verify_grant_signatures(document: MatrixLedgerDocument, signer: GrantSigner) -> None:
    """Verify every Grant signature and exact Intent binding in a ledger."""

    intents: dict[str, Intent] = {}
    for entry in document.entries:
        if entry.document_kind == "Intent":
            intents[entry.cell_id] = _parse_model(Intent, entry.document)
        elif entry.document_kind == "Grant":
            grant = _parse_model(Grant, entry.document)
            try:
                intent = intents[entry.cell_id]
            except KeyError as exc:
                raise RXPError("causal_parent_missing", "Grant has no prior Intent") from exc
            signer.verify(grant, intent, checked_at=grant.claims.issued_at)
