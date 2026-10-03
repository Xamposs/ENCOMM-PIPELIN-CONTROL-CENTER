"""Bounded, read-only proposal workspace snapshot — PURE proposal domain.

Reads the review-relevant workspace files into one deterministic
:class:`ReviewSourceSnapshot` for packet construction:

    00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md
    00_SOURCE_OF_TRUTH/PROJECT_FACTS.md
    00_SOURCE_OF_TRUTH/TEAM.md
    00_SOURCE_OF_TRUTH/ARCHITECTURE.md
    00_SOURCE_OF_TRUTH/TERMINOLOGY.md
    03_PROPOSAL/MASTER_PROPOSAL.md          (REQUIRED for a review)
    01_OFFICIAL/OFFICIAL_REQUIREMENTS.md    (optional; canonical Markdown)

Hard rules:

* the loader NEVER writes, touches or deletes anything — it is read-only by
  construction (only ``Path.read_bytes``/``read_text`` on contract paths);
* every read is BOUNDED by :data:`MAX_SNAPSHOT_FILE_CHARS` — an oversized
  file is recorded as unavailable-with-reason, never slurped unbounded;
* a missing OPTIONAL source is recorded as explicitly unavailable (the
  prompt renders it as unavailable so the reviewer cannot invent it);
* a missing MASTER_PROPOSAL is a HARD error — no review is possible;
* official requirements are accepted ONLY as plain text/Markdown
  (``.md``/``.txt``).  A ``.pdf``/``.rtf``/other binary official document is
  NOT ingested (no PDF/RTF parsing exists); it is recorded as unavailable
  with the exact reason so the reviewer knows compliance cannot be claimed.
* text is decoded UTF-8 with errors ``replace`` for snapshot purposes, but
  byte-exact hashing of the master proposal is done separately through
  :func:`encomm_pcc.proposal.fingerprint.proposal_fingerprint`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .workspace import MASTER_PROPOSAL_RELPATH

__all__ = [
    "MAX_SNAPSHOT_FILE_CHARS",
    "OFFICIAL_REQUIREMENTS_RELPATH",
    "SOURCE_SNAPSHOT_RELPATHS",
    "ReviewSourceSnapshot",
    "SourceSnapshotError",
    "load_review_snapshot",
]

#: Optional official-requirements input — canonical plain-text/Markdown only.
OFFICIAL_REQUIREMENTS_RELPATH = "01_OFFICIAL/OFFICIAL_REQUIREMENTS.md"

#: Session 019: the OFFICIAL application template (canonical normalized).
APPLICATION_TEMPLATE_RELPATH = "01_OFFICIAL/APPLICATION_TEMPLATE.md"

#: Session 019: directory of normalized official programme documents.
OFFICIAL_NORMALIZED_DIRNAME = "01_OFFICIAL/NORMALIZED"

#: Every file the snapshot reader may look at (canonical POSIX relpaths).
SOURCE_SNAPSHOT_RELPATHS: tuple[str, ...] = (
    "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md",
    "00_SOURCE_OF_TRUTH/PROJECT_FACTS.md",
    "00_SOURCE_OF_TRUTH/TEAM.md",
    "00_SOURCE_OF_TRUTH/ARCHITECTURE.md",
    "00_SOURCE_OF_TRUTH/TERMINOLOGY.md",
    MASTER_PROPOSAL_RELPATH,
    OFFICIAL_REQUIREMENTS_RELPATH,
)

#: Per-file read cap (characters, decoded text).  Keeps the whole snapshot
#: (7 files) under ~2.8M chars worst case; a real workspace is far below.
MAX_SNAPSHOT_FILE_CHARS = 400_000

#: Extensions accepted for the OFFICIAL requirements input (plain text only).
_OFFICIAL_ALLOWED_SUFFIXES = {".md", ".txt"}


class SourceSnapshotError(RuntimeError):
    """Raised when the snapshot cannot be built (e.g. no master proposal)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


@dataclass(slots=True)
class _SourceFile:
    text: str = ""
    available: bool = False
    unavailable_reason: str = ""


@dataclass(slots=True)
class ReviewSourceSnapshot:
    """Deterministic snapshot of the proposal workspace for ONE review.

    ``master_proposal_text`` is always present (the loader refuses to build
    a snapshot without it).  Optional sources carry ``available=False`` plus
    a stable ``unavailable_reason`` when absent — the packet renders them
    honestly instead of inventing content.

    Session 019: the OFFICIAL APPLICATION TEMPLATE and the normalized
    official programme documents are first-class packet inputs (brief §8).
    ``official_documents`` is a deterministic (name → text) tuple sorted by
    stable name; every packet labels each document with its name.
    """

    master_proposal_text: str
    master_blueprint_text: str = ""
    project_facts_text: str = ""
    team_text: str = ""
    architecture_text: str = ""
    terminology_text: str = ""
    official_requirements_text: str = ""
    official_requirements_available: bool = False
    official_requirements_unavailable_reason: str = ""
    #: Per-source availability evidence (relpath → reason when unavailable).
    unavailable_sources: dict[str, str] = field(default_factory=dict)
    #: Session 019: ``01_OFFICIAL/APPLICATION_TEMPLATE.md`` (canonical).
    application_template_text: str = ""
    #: Session 019: normalized official documents,
    #: ``((stable-name, text), ...)`` sorted by name — never a dict (the
    #: packet order must be deterministic across runs).
    official_documents: tuple[tuple[str, str], ...] = ()


def _read_bounded(path: Path, max_chars: int = MAX_SNAPSHOT_FILE_CHARS) -> _SourceFile:
    """Read one file bounded; absence/oversize is recorded, never raised."""
    if not path.exists():
        return _SourceFile(unavailable_reason="missing")
    if not path.is_file():
        return _SourceFile(unavailable_reason="not_a_regular_file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _SourceFile(unavailable_reason=f"unreadable: {exc}")
    if len(raw) > max_chars * 4:
        # Cheap byte-level pre-check: 4 bytes per decoded char worst case.
        return _SourceFile(
            unavailable_reason=(
                f"oversized: {len(raw)} bytes exceed the snapshot cap"
            )
        )
    text = raw.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        return _SourceFile(
            unavailable_reason=(
                f"oversized: {len(text)} characters exceed {max_chars}"
            )
        )
    return _SourceFile(text=text, available=True)


def load_review_snapshot(
    root: Path, *, blueprint_max_chars: int | None = None
) -> ReviewSourceSnapshot:
    """Read the review-relevant workspace files under ``root`` (read-only).

    Session 019: ``blueprint_max_chars`` (default: the historical 400,000
    per-file cap) raises the MASTER BLUEPRINT's read bound only — the
    canonical source-of-truth document reaches packets whole when the
    configured source budget allows it.  All other files keep the standard
    per-file cap.  An over-budget blueprint stays explicitly UNAVAILABLE
    (never truncated); the budget gate raises before any model call.

    Raises :class:`SourceSnapshotError` ONLY when
    ``03_PROPOSAL/MASTER_PROPOSAL.md`` is missing/unreadable — a review
    without the proposal is impossible and is never fabricated.  Every other
    missing source is recorded as explicitly unavailable.
    """
    root = Path(root)
    blueprint_cap = (
        MAX_SNAPSHOT_FILE_CHARS
        if blueprint_max_chars is None
        else max(1, int(blueprint_max_chars))
    )

    def _p(relpath: str) -> Path:
        return root.joinpath(*relpath.split("/"))

    master = _read_bounded(_p(MASTER_PROPOSAL_RELPATH))
    if not master.available:
        raise SourceSnapshotError(
            "master_proposal_unavailable",
            f"{MASTER_PROPOSAL_RELPATH} is {master.unavailable_reason or 'unavailable'}; "
            "a review without the master proposal is impossible.",
        )

    blueprint = _read_bounded(
        _p("00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md"), max_chars=blueprint_cap
    )
    facts = _read_bounded(_p("00_SOURCE_OF_TRUTH/PROJECT_FACTS.md"))
    team = _read_bounded(_p("00_SOURCE_OF_TRUTH/TEAM.md"))
    architecture = _read_bounded(_p("00_SOURCE_OF_TRUTH/ARCHITECTURE.md"))
    terminology = _read_bounded(_p("00_SOURCE_OF_TRUTH/TERMINOLOGY.md"))

    unavailable: dict[str, str] = {}
    for rel, src in (
        ("00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md", blueprint),
        ("00_SOURCE_OF_TRUTH/PROJECT_FACTS.md", facts),
        ("00_SOURCE_OF_TRUTH/TEAM.md", team),
        ("00_SOURCE_OF_TRUTH/ARCHITECTURE.md", architecture),
        ("00_SOURCE_OF_TRUTH/TERMINOLOGY.md", terminology),
    ):
        if not src.available:
            unavailable[rel] = src.unavailable_reason

    # Official requirements: canonical plain-text/Markdown input ONLY.
    official_path = _p(OFFICIAL_REQUIREMENTS_RELPATH)
    if official_path.exists() and official_path.suffix.lower() not in _OFFICIAL_ALLOWED_SUFFIXES:
        official = _SourceFile(
            unavailable_reason=(
                f"unsupported format '{official_path.suffix or '(none)'}': only "
                "plain-text/Markdown official requirements are accepted "
                "(OFFICIAL_REQUIREMENTS.md); PDF/RTF ingestion does not exist."
            )
        )
    else:
        official = _read_bounded(official_path)
    if not official.available:
        unavailable[OFFICIAL_REQUIREMENTS_RELPATH] = official.unavailable_reason

    # Session 019: OFFICIAL TEMPLATE (canonical, bounded like the blueprint
    # when raised) and the normalized official documents (deterministic
    # name order).  Both are optional — absence is recorded, never invented.
    template = _read_bounded(
        _p(APPLICATION_TEMPLATE_RELPATH), max_chars=blueprint_cap
    )
    if not template.available:
        unavailable[APPLICATION_TEMPLATE_RELPATH] = template.unavailable_reason
    official_documents: list[tuple[str, str]] = []
    normalized_dir = _p(OFFICIAL_NORMALIZED_DIRNAME)
    if normalized_dir.is_dir():
        for entry in sorted(normalized_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() != ".md":
                continue
            doc = _read_bounded(entry)
            if doc.available:
                official_documents.append((entry.stem, doc.text))
            else:
                unavailable[
                    f"{OFFICIAL_NORMALIZED_DIRNAME}/{entry.name}"
                ] = doc.unavailable_reason

    return ReviewSourceSnapshot(
        master_proposal_text=master.text,
        master_blueprint_text=blueprint.text if blueprint.available else "",
        project_facts_text=facts.text if facts.available else "",
        team_text=team.text if team.available else "",
        architecture_text=architecture.text if architecture.available else "",
        terminology_text=terminology.text if terminology.available else "",
        official_requirements_text=official.text if official.available else "",
        official_requirements_available=official.available,
        official_requirements_unavailable_reason=official.unavailable_reason,
        unavailable_sources=unavailable,
        application_template_text=(
            template.text if template.available else ""
        ),
        official_documents=tuple(official_documents),
    )
