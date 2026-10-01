"""Proposal workspace contract — a safe, idempotent directory initializer.

The proposal workspace is a DOCUMENT workspace, separate from the Pipeline
Control Center source repository.  It is NOT required to be (and by default
is not treated as) a git repository; nothing here touches git.

Directory contract::

    00_SOURCE_OF_TRUTH/   immutable operator inputs (blueprint, facts, team…)
    01_OFFICIAL/          official programme documents (calls, templates)
    02_EVIDENCE/          source registry, claim ledger, AI usage log
    03_PROPOSAL/          MASTER_PROPOSAL.md — ORCHESTRATOR write authority ONLY
    04_REVIEWS/           reviewer outputs (structured review artifacts)
    05_CONTROL/           scorecard, hard gates, issues, contradictions…
    06_VERSIONS/          frozen proposal revisions per iteration
    07_FINAL/             final submitted artefacts

Safety rules (enforced by :class:`ProposalWorkspace`):

* Initialization is IDEMPOTENT: existing directories and files are never
  modified, never overwritten, never deleted.  Only missing directories are
  created and missing seed files are created only when absent.
* ``03_PROPOSAL/MASTER_PROPOSAL.md`` and the ``00_SOURCE_OF_TRUTH`` files are
  NEVER silently replaced.  Once present, the initializer leaves them exactly
  as they are.
* All paths are handled through :class:`pathlib.Path` and work identically on
  Windows and POSIX.

Authority invariant (Section 4 contract): the initializer may seed an absent
``MASTER_PROPOSAL.md`` (empty) but the ONLY future writer of that file is the
Proposal ORCHESTRATOR during the INTEGRATION phase.  Reviewers are read-only
with respect to it — this module gives them no write surface for it.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CONTROL_FILES",
    "EVIDENCE_FILES",
    "MASTER_PROPOSAL_RELPATH",
    "PROPOSAL_WORKSPACE_DIRS",
    "SOURCE_OF_TRUTH_FILES",
    "ProposalWorkspace",
    "workspace_paths",
]


def _p(relpath: str) -> str:
    """Normalise a relative contract path with POSIX separators."""
    return relpath.replace("\\", "/")


#: The eight canonical workspace directories, in contract order.
PROPOSAL_WORKSPACE_DIRS: tuple[str, ...] = (
    "00_SOURCE_OF_TRUTH",
    "01_OFFICIAL",
    "02_EVIDENCE",
    "03_PROPOSAL",
    "04_REVIEWS",
    "05_CONTROL",
    "06_VERSIONS",
    "07_FINAL",
)

#: Source-of-truth seed files (created ONLY when absent; never overwritten).
SOURCE_OF_TRUTH_FILES: tuple[str, ...] = (
    "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md",
    "00_SOURCE_OF_TRUTH/PROJECT_FACTS.md",
    "00_SOURCE_OF_TRUTH/TEAM.md",
    "00_SOURCE_OF_TRUTH/ARCHITECTURE.md",
    "00_SOURCE_OF_TRUTH/TERMINOLOGY.md",
)

#: Evidence seed files (created ONLY when absent; never overwritten).
EVIDENCE_FILES: tuple[str, ...] = (
    "02_EVIDENCE/SOURCE_REGISTRY.json",
    "02_EVIDENCE/CLAIM_LEDGER.json",
    "02_EVIDENCE/AI_USAGE_LOG.json",
)

#: The master proposal — the ORCHESTRATOR's exclusive future write target.
MASTER_PROPOSAL_RELPATH: str = "03_PROPOSAL/MASTER_PROPOSAL.md"

#: Control seed files (created ONLY when absent; never overwritten).
CONTROL_FILES: tuple[str, ...] = (
    "05_CONTROL/SCORECARD.json",
    "05_CONTROL/HARD_GATES.json",
    "05_CONTROL/ISSUES.json",
    "05_CONTROL/CONTRADICTIONS.json",
    "05_CONTROL/UNVERIFIED_CLAIMS.json",
    "05_CONTROL/PAGE_BUDGET.json",
)

#: Every canonical seed file, in deterministic order.
SEED_FILES: tuple[str, ...] = (
    SOURCE_OF_TRUTH_FILES
    + EVIDENCE_FILES
    + (MASTER_PROPOSAL_RELPATH,)
    + CONTROL_FILES
)


def workspace_paths(root: Path) -> dict[str, Path]:
    """Return the canonical relative-name → absolute-path mapping for ``root``.

    Pure path arithmetic (no I/O): safe to call on any platform; Windows
    drive-letter/separator handling is delegated to :class:`pathlib.Path`.
    Keys are the canonical directory names (e.g. ``"00_SOURCE_OF_TRUTH"``);
    seed files appear under their directory's key as absolute paths joined
    with their own name, e.g. ``result["02_EVIDENCE"] / "CLAIM_LEDGER.json"``.
    """
    root = Path(root)
    paths: dict[str, Path] = {}
    for name in PROPOSAL_WORKSPACE_DIRS:
        paths[name] = root / name
    return paths


class ProposalWorkspace:
    """Represents (and safely initialises) one proposal workspace.

    The class holds no mutable global state; each instance is bound to one
    root directory.  :meth:`initialize` is idempotent and side-effect-safe:

    * creates missing contract directories;
    * creates seed files ONLY when absent (empty files — the content is the
      operators'/agents' responsibility, never fabricated boilerplate);
    * NEVER overwrites, truncates or deletes any existing file;
    * NEVER modifies anything outside ``root``.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    # -- queries ---------------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, relpath: str) -> Path:
        """Absolute path for a canonical relative contract path.

        Accepts POSIX or Windows separators regardless of the host OS; the
        returned path is a platform-correct :class:`pathlib.Path`.
        """
        rel = _p(relpath)
        if rel not in SEED_FILES and rel not in PROPOSAL_WORKSPACE_DIRS:
            raise ValueError(f"Non-canonical workspace path: {relpath!r}")
        parts = rel.split("/")
        target = self._root.joinpath(*parts)
        # Defence in depth: the resolved path must stay inside root.
        resolved_root = self._root.resolve()
        if not target.resolve().is_relative_to(resolved_root):
            raise ValueError(f"Workspace path escapes the root: {relpath!r}")
        return target

    def master_proposal_path(self) -> Path:
        """Absolute path of ``03_PROPOSAL/MASTER_PROPOSAL.md``."""
        return self.path_for(MASTER_PROPOSAL_RELPATH)

    def missing_directories(self) -> list[Path]:
        """Contract directories that do not exist yet (read-only check)."""
        return [
            p
            for p in workspace_paths(self._root).values()
            if not p.is_dir()
        ]

    # -- mutations -------------------------------------------------------
    def initialize(self) -> list[Path]:
        """Idempotently create missing directories and missing seed files.

        Returns the list of paths THIS call created (empty on a fully
        initialised workspace — the idempotence signal).  Existing content is
        never touched: an existing ``MASTER_PROPOSAL.md`` or source-of-truth
        file is never replaced.
        """
        created: list[Path] = []
        for name in PROPOSAL_WORKSPACE_DIRS:
            directory = self._root / name
            if not directory.is_dir():
                # exist_ok: a concurrent/previous init may have made it.
                directory.mkdir(parents=True, exist_ok=True)
                created.append(directory)
        for rel in SEED_FILES:
            target = self._root.joinpath(*_p(rel).split("/"))
            if not target.exists():
                # Seed ONLY when absent; never overwrite existing content.
                # "x" mode fails loudly if the file appears concurrently —
                # that race is answered by leaving the existing file alone.
                try:
                    with open(target, "x", encoding="utf-8", newline="\n"):
                        pass
                except FileExistsError:  # pragma: no cover - concurrent init
                    pass
                else:
                    created.append(target)
        return created
