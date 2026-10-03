"""READ-ONLY deterministic workspace status — safe recovery for the operator UI.

Session 017 (brief §13); Session 017A hardens recovery (brief §6/§7/§8/§12).
The Proposal Mode persists durable FILE artifacts (never SQLite); this module
reconstructs the smallest honest operator status from those artifacts.  It is
strictly READ-ONLY:

* it never writes, moves, deletes or creates anything;
* it never fabricates historical phase transitions — a phase is reported
  only when the durable artifacts prove it;
* ambiguity is surfaced as ``ambiguous=True`` (the UI then shows
  "Recovery requires operator confirmation" and never auto-runs AI).

Reporting precedence (most durable evidence wins):

1. ``04_REVIEWS/iteration_NNN/hard_gates.json``  → the gates RAN for the
   latest iteration; the artifact's ``overall_outcome`` is the honest
   outcome (COMPLETE ⇒ phase COMPLETE; INCOMPLETE stays at
   HARD_GATE_VALIDATION per S016; REVISION_REQUIRED / BLOCKED map
   accordingly).
2. ``04_REVIEWS/iteration_NNN/review_bundle.json`` → a full review cycle
   finished; the machine is at least at INTEGRATION.  When
   ``integration_result.json`` also exists the integration CONSUMED the
   brief — and Session 017A adds the ``05_CONTROL/NEXT_ITERATION.json``
   handoff check: when integration CHANGED the proposal (S015), a VALID
   handoff recovers ``REVISION_REQUIRED`` (with
   ``detail["next_iteration_number"]``), while a malformed/stale/conflicting
   handoff — or a CHANGED proposal whose required handoff is MISSING — is
   AMBIGUOUS, never a fake ``HARD_GATE_VALIDATION``.
3. Per-review artifacts only (three files, no bundle) → a cycle reached the
   last reviewer but never aggregated — ambiguous recovery.
4. A pre-review version freeze in ``06_VERSIONS`` WITHOUT any matching
   ``04_REVIEWS`` iteration directory (freeze-only interrupted iteration,
   Session 017A §12) → ambiguous recovery; the freeze floor participates in
   the latest-iteration determination so such an iteration is detected at
   all.
5. Anything less (an empty workspace) → phase IDLE.

The returned status is a plain JSON-friendly dataclass (same convention as
the proposal domain) so the UI never imports proposal_runtime logic into the
render path more than this one call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..proposal.enums import ProposalPhase
from ..proposal.fingerprint import proposal_fingerprint
from .revision_handoff import NEXT_ITERATION_FILENAME, REVISION_HANDOFF_SCHEMA

__all__ = [
    "PROPOSAL_STATUS_SCHEMA",
    "ProposalWorkspaceStatus",
    "latest_iteration_number",
    "load_workspace_status",
]

PROPOSAL_STATUS_SCHEMA = "encomm-pcc.proposal-workspace-status/v1"

_ITERATION_DIR = re.compile(r"^iteration_(\d{3,})$")
_VERSION_FREEZE_RE = re.compile(r"^iteration_(\d{3,})_pre_review\.md$")

#: Per-review artifact filenames (must mirror review_artifacts.py values;
#: duplicated as literals so this module stays a pure reader).
_REVIEW_FILES = ("scientific_review.json", "implementation_review.json", "red_team_review.json")
_BUNDLE_FILE = "review_bundle.json"
_BRIEF_FILE = "integration_brief.json"
_INTEGRATION_RESULT_FILE = "integration_result.json"
_HARD_GATES_FILE = "hard_gates.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    """Parse a JSON object file; ``None`` when absent/corrupt (never raises)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def latest_iteration_number(workspace: Path) -> int:
    """Highest ``iteration_NNN`` directory under ``04_REVIEWS``, else 0.

    Pure read-only directory scan.  A non-integer or non-directory entry is
    ignored, never treated as iteration 1.  (Freeze-only iterations are
    folded in by :func:`load_workspace_status` via the ``06_VERSIONS`` scan —
    this function keeps its documented reviews-dir contract.)
    """
    reviews_dir = Path(workspace) / "04_REVIEWS"
    if not reviews_dir.is_dir():
        return 0
    numbers = [
        int(match.group(1))
        for entry in reviews_dir.iterdir()
        if entry.is_dir() and (match := _ITERATION_DIR.match(entry.name))
    ]
    return max(numbers) if numbers else 0


def _latest_freeze_iteration(workspace: Path) -> int:
    """Highest ``iteration_NNN_pre_review.md`` under ``06_VERSIONS``, else 0.

    Session 017A §12: a pre-review freeze can exist WITHOUT a matching
    ``04_REVIEWS/iteration_NNN`` directory (the cycle died between the
    freeze and the first reviewer artifact).  A pure ``04_REVIEWS`` scan
    cannot discover it; this read-only scan can.
    """
    versions_dir = Path(workspace) / "06_VERSIONS"
    if not versions_dir.is_dir():
        return 0
    numbers = [
        int(match.group(1))
        for entry in versions_dir.iterdir()
        if entry.is_file() and (match := _VERSION_FREEZE_RE.match(entry.name))
    ]
    return max(numbers) if numbers else 0


def _evaluate_revision_handoff(
    *,
    workspace: Path,
    latest_iteration: int,
    current_proposal_hash: str,
    reviewed_hash: str,
) -> tuple[str, int, str]:
    """Classify ``05_CONTROL/NEXT_ITERATION.json`` for the LATEST iteration.

    Returns ``(verdict, next_iteration_number, reason)`` where verdict is:

    * ``"valid"``       — the handoff proves the S015 changed-proposal
      contract: schema matches, ``previous_iteration_number == N``,
      ``next_iteration_number == N + 1``,
      ``revised_proposal_hash`` equals the CURRENT master-proposal exact
      hash and ``previous_reviewed_hash`` equals the review bundle's
      ``proposal_hash``.  Recovery MUST be ``REVISION_REQUIRED``.
    * ``"absent"``      — no handoff file exists.  Legitimate
      ``HARD_GATE_VALIDATION`` only when the proposal is still
      review-current; a changed proposal without its required handoff is
      reported as ``"missing_changed"``.
    * ``"missing_changed"`` — the proposal hash DIFFERS from the reviewed
      hash but the required handoff does not exist: integration changed the
      proposal and the handoff is lost.  Ambiguous.
    * ``"consumed"``    — the handoff belongs to an EARLIER iteration
      (``previous_iteration_number < N``) and iteration ``P + 1`` carries a
      review bundle: the handoff was honoured by history and no longer
      describes the current position.
    * ``"stale_conflict"`` — the handoff exists but contradicts the durable
      evidence (wrong iteration linkage, unconsumed old handoff, forward
      pointer, hash mismatch).  Ambiguous.
    * ``"malformed"``   — the file exists but is unreadable / not a JSON
      object / carries the wrong schema / non-integer iteration numbers.
      Ambiguous — never guessed.

    Read-only: nothing here mutates durable artifacts.
    """
    handoff_path = Path(workspace) / "05_CONTROL" / NEXT_ITERATION_FILENAME
    if not handoff_path.is_file():
        if current_proposal_hash and reviewed_hash and (
            current_proposal_hash.lower() != reviewed_hash.lower()
        ):
            return ("missing_changed", 0, (
                "the proposal changed after the review but the required "
                f"{NEXT_ITERATION_FILENAME} handoff is missing."
            ))
        return ("absent", 0, "")
    data = _read_json(handoff_path)
    if data is None:
        return ("malformed", 0, (
            f"{NEXT_ITERATION_FILENAME} exists but is not a readable JSON object."
        ))
    if str(data.get("schema") or "") != REVISION_HANDOFF_SCHEMA:
        return ("malformed", 0, (
            f"{NEXT_ITERATION_FILENAME} carries schema "
            f"{data.get('schema')!r}, expected {REVISION_HANDOFF_SCHEMA!r}."
        ))
    try:
        previous_n = int(data.get("previous_iteration_number"))  # type: ignore[arg-type]
        next_n = int(data.get("next_iteration_number"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ("malformed", 0, (
            f"{NEXT_ITERATION_FILENAME} carries non-integer iteration numbers."
        ))
    if previous_n == latest_iteration and next_n == latest_iteration + 1:
        revised_hash = str(data.get("revised_proposal_hash") or "").strip().lower()
        prev_reviewed = str(data.get("previous_reviewed_hash") or "").strip().lower()
        if not revised_hash or not prev_reviewed:
            return ("stale_conflict", 0, (
                f"{NEXT_ITERATION_FILENAME} is missing a required hash field."
            ))
        if revised_hash != str(current_proposal_hash or "").strip().lower():
            return ("stale_conflict", 0, (
                f"{NEXT_ITERATION_FILENAME} revised_proposal_hash does not "
                "match the current MASTER_PROPOSAL hash (stale handoff)."
            ))
        if prev_reviewed != str(reviewed_hash or "").strip().lower():
            return ("stale_conflict", 0, (
                f"{NEXT_ITERATION_FILENAME} previous_reviewed_hash does not "
                "match the review bundle's proposal_hash."
            ))
        return ("valid", next_n, "")
    if previous_n < latest_iteration:
        consumed = _read_json(
            Path(workspace) / "04_REVIEWS"
            / f"iteration_{previous_n + 1:03d}" / _BUNDLE_FILE
        )
        if consumed is not None:
            return ("consumed", 0, "")
        return ("stale_conflict", 0, (
            f"{NEXT_ITERATION_FILENAME} targets iteration {previous_n + 1} "
            "whose review bundle does not exist (unconsumed stale handoff)."
        ))
    return ("stale_conflict", 0, (
        f"{NEXT_ITERATION_FILENAME} points at iteration {next_n} beyond the "
        f"latest review iteration {latest_iteration} (conflicting handoff)."
    ))


@dataclass(slots=True)
class ProposalWorkspaceStatus:
    """One honest snapshot of a proposal workspace's durable state."""

    phase: ProposalPhase = ProposalPhase.IDLE
    latest_iteration: int = 0
    current_proposal_hash: str = ""
    has_master_proposal: bool = False
    master_proposal_empty: bool = False
    has_review_bundle: bool = False
    has_integration_result: bool = False
    has_hard_gates_artifact: bool = False
    latest_overall_outcome: str = ""
    #: A completed position without its artifact, or an artifact at an
    #: unprovable position — the UI must ask the operator, never auto-run.
    ambiguous: bool = False
    ambiguity_reason: str = ""
    #: Operator-facing one-line summary of WHERE the work stands.
    summary: str = ""
    #: Structured detail the UI may render (review verdicts per role, gate
    #: counts, handoff presence, the recovered next iteration number).
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PROPOSAL_STATUS_SCHEMA,
            "phase": self.phase.value,
            "latest_iteration": self.latest_iteration,
            "current_proposal_hash": self.current_proposal_hash,
            "has_master_proposal": self.has_master_proposal,
            "master_proposal_empty": self.master_proposal_empty,
            "has_review_bundle": self.has_review_bundle,
            "has_integration_result": self.has_integration_result,
            "has_hard_gates_artifact": self.has_hard_gates_artifact,
            "latest_overall_outcome": self.latest_overall_outcome,
            "ambiguous": self.ambiguous,
            "ambiguity_reason": self.ambiguity_reason,
            "summary": self.summary,
            "detail": dict(self.detail),
        }


def load_workspace_status(workspace: Path) -> ProposalWorkspaceStatus:
    """Build the READ-ONLY status of one proposal workspace."""
    workspace = Path(workspace)
    status = ProposalWorkspaceStatus()

    master = workspace / "03_PROPOSAL" / "MASTER_PROPOSAL.md"
    status.has_master_proposal = master.is_file()
    if status.has_master_proposal:
        try:
            status.master_proposal_empty = not master.read_bytes().strip()
        except OSError:
            status.master_proposal_empty = True
        else:
            if not status.master_proposal_empty:
                try:
                    status.current_proposal_hash = proposal_fingerprint(master)
                except OSError:
                    status.current_proposal_hash = ""

    iteration = latest_iteration_number(workspace)
    # Session 017A §12: a freeze-only interrupted iteration (a pre-review
    # freeze with NO reviews directory yet) must still be DISCOVERABLE.
    freeze_iteration = _latest_freeze_iteration(workspace)
    if freeze_iteration > iteration:
        iteration = freeze_iteration
    status.latest_iteration = iteration
    if iteration < 1:
        status.summary = "No proposal iterations yet."
        return status

    it_dir = workspace / "04_REVIEWS" / f"iteration_{iteration:03d}"

    # -- 1. hard gates ran for the latest iteration -------------------------
    gates = _read_json(it_dir / _HARD_GATES_FILE)
    if gates is not None:
        status.has_hard_gates_artifact = True
        outcome = str(gates.get("overall_outcome") or "")
        status.latest_overall_outcome = outcome
        status.detail["gate_counts"] = dict(gates.get("counts") or {})
        # S016 dispositions → the phase the machine legitimately sat at.
        phase_by_outcome = {
            "COMPLETE": ProposalPhase.COMPLETE,
            "REVISION_REQUIRED": ProposalPhase.REVISION_REQUIRED,
            "BLOCKED": ProposalPhase.BLOCKED,
            # INCOMPLETE: the machine STAYS at HARD_GATE_VALIDATION (re-run).
            "INCOMPLETE": ProposalPhase.HARD_GATE_VALIDATION,
        }
        if outcome in phase_by_outcome:
            status.phase = phase_by_outcome[outcome]
            status.summary = (
                f"Hard gates ran for iteration {iteration}: {outcome}."
            )
        else:
            # An unrecognised record is shown, never interpreted.
            status.ambiguous = True
            status.ambiguity_reason = (
                f"hard-gates artifact carries unknown outcome {outcome!r}."
            )
            status.summary = (
                f"Hard-gates artifact for iteration {iteration} is not a "
                "recognised outcome; recovery requires operator confirmation."
            )
        return status

    # -- 2. a full review cycle finished -------------------------------------
    bundle = _read_json(it_dir / _BUNDLE_FILE)
    if bundle is not None:
        status.has_review_bundle = True
        status.detail["aggregate_verdict"] = str(bundle.get("aggregate_verdict") or "")
        reviewed_hash = str(bundle.get("proposal_hash") or "")
        status.detail["reviewed_hash"] = reviewed_hash
        integration = _read_json(it_dir / _INTEGRATION_RESULT_FILE)
        if integration is not None:
            status.has_integration_result = True
            # Session 017A §7: the integration may have CHANGED the proposal
            # (S015) — then the durable NEXT_ITERATION.json handoff decides
            # between REVISION_REQUIRED and an ambiguous recovery.  A plain
            # HARD_GATE_VALIDATION is honest ONLY when the proposal is still
            # review-current.
            verdict, next_n, reason = _evaluate_revision_handoff(
                workspace=workspace,
                latest_iteration=iteration,
                current_proposal_hash=status.current_proposal_hash,
                reviewed_hash=reviewed_hash,
            )
            if verdict == "valid":
                status.phase = ProposalPhase.REVISION_REQUIRED
                status.detail["next_iteration_number"] = next_n
                status.summary = (
                    f"Iteration {iteration} integration changed the proposal; "
                    f"the revision handoff recovers REVISION_REQUIRED at "
                    f"iteration {next_n}."
                )
            elif verdict == "consumed":
                # A historical handoff honoured by iteration P+1's review:
                # the hash check decides the CURRENT position honestly.
                if (
                    status.current_proposal_hash
                    and reviewed_hash.lower()
                    == status.current_proposal_hash.lower()
                ):
                    status.phase = ProposalPhase.HARD_GATE_VALIDATION
                    status.summary = (
                        f"Iteration {iteration} review + integration "
                        "finished; hard gates have NOT run yet."
                    )
                else:
                    status.ambiguous = True
                    status.ambiguity_reason = (
                        "the current proposal hash does not match the latest "
                        "reviewed hash and no valid handoff explains the "
                        "change."
                    )
                    status.summary = (
                        f"Iteration {iteration} recovery is ambiguous; "
                        "recovery requires operator confirmation."
                    )
            elif verdict in ("missing_changed", "malformed", "stale_conflict"):
                status.ambiguous = True
                status.ambiguity_reason = reason
                status.summary = (
                    f"Iteration {iteration} review + integration finished "
                    "but the recovery evidence conflicts; recovery requires "
                    "operator confirmation."
                )
            else:  # "absent" and the proposal is review-current
                status.phase = ProposalPhase.HARD_GATE_VALIDATION
                status.summary = (
                    f"Iteration {iteration} review + integration finished; "
                    "hard gates have NOT run yet."
                )
        else:
            # The review cycle reached INTEGRATION; the integration has NOT
            # consumed the brief (it is still there and unchanged).
            status.phase = ProposalPhase.INTEGRATION
            status.summary = (
                f"Iteration {iteration} review cycle finished; the "
                "integration has NOT run yet."
            )
        return status

    # -- 3. per-review artifacts without a bundle ----------------------------
    present = [name for name in _REVIEW_FILES if (it_dir / name).is_file()]
    if present:
        status.ambiguous = True
        status.ambiguity_reason = (
            f"{len(present)}/3 reviewer artifacts exist for iteration "
            f"{iteration} but no review bundle was aggregated."
        )
        status.phase = ProposalPhase.IDLE
        status.summary = (
            "A partial review cycle exists; recovery requires operator "
            "confirmation."
        )
        return status

    # -- 4. freeze without reviews (a cycle was started then died early) ----
    freeze = workspace / "06_VERSIONS" / f"iteration_{iteration:03d}_pre_review.md"
    if freeze.is_file():
        status.ambiguous = True
        status.ambiguity_reason = (
            f"a pre-review version freeze exists for iteration {iteration} "
            "but no reviewer artifacts."
        )
        status.summary = (
            "A review cycle was started for iteration "
            f"{iteration} but produced no durable reviewer artifacts; "
            "recovery requires operator confirmation."
        )
        return status

    status.summary = (
        f"Iteration {iteration} directory exists but carries no durable "
        "artifacts."
    )
    return status
