"""Safe source import — originals preserved, canonical normalized text derived.

Session 019 (Proposal Factory V2).  The operator imports real-world files
(``.md`` / ``.txt`` / ``.pdf`` / ``.docx`` / ``.rtf``) as proposal inputs.
The importer NEVER destructively converts the only copy:

* the ORIGINAL bytes are preserved verbatim under the role's ``IMPORTS/``
  directory (``00_SOURCE_OF_TRUTH/IMPORTS/`` or ``01_OFFICIAL/IMPORTS/``);
* a CANONICAL normalized Markdown copy is derived next to the originals the
  contract paths expect (``00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md``,
  ``01_OFFICIAL/APPLICATION_TEMPLATE.md``, ``03_PROPOSAL/MASTER_PROPOSAL.md``,
  ``01_OFFICIAL/NORMALIZED/<stable-name>.md``);
* every import appends ONE record to the durable manifest
  ``05_CONTROL/SOURCE_IMPORT_MANIFEST.json`` (atomic deterministic writes);
* replacing an existing canonical output requires the explicit ``replace``
  flag AND freezes the old exact bytes into a backup file first;
* extraction is REAL (no OCR): UTF-8 direct text for ``.md``/``.txt``,
  ``python-docx`` for ``.docx``, ``pypdf`` for ``.pdf``, ``striprtf`` for
  ``.rtf``.  Extraction that recovers no usable text FAILS VISIBLY — an
  empty canonical source is never fabricated.

This module is PURE proposal domain: it imports nothing from the coding
pipeline, ``proposal_runtime`` or the UI, and holds no engine knowledge.
The heavy parsers are imported lazily so the base application never needs
them (the packaged build ships them; see ``requirements.txt``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "MAX_IMPORT_SOURCE_BYTES",
    "MAX_NORMALIZED_CHARS",
    "SOURCE_IMPORT_MANIFEST_FILENAME",
    "SOURCE_IMPORT_MANIFEST_RELPATH",
    "SOURCE_IMPORT_MANIFEST_SCHEMA",
    "SUPPORTED_IMPORT_SUFFIXES",
    "SourceImportError",
    "SourceImportRecord",
    "blueprint_status",
    "import_proposal",
    "import_source",
    "load_source_import_manifest",
    "template_status",
]

#: Hard cap on the ORIGINAL file size accepted for import (bytes).  A real
#: 200-page blueprint is far below this; the cap exists so a stray huge file
#: is refused visibly instead of slurped unbounded.
MAX_IMPORT_SOURCE_BYTES = 64 * 1024 * 1024

#: Hard cap on the NORMALIZED canonical text produced from one import.
#: Extraction beyond this bound fails visibly (the source must be split or
#: trimmed by the operator) — never silently truncated.
MAX_NORMALIZED_CHARS = 2_500_000

#: User-facing suffixes the importer accepts.
SUPPORTED_IMPORT_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".txt", ".pdf", ".docx", ".rtf"}
)

#: Durable manifest (relative to the workspace root).
SOURCE_IMPORT_MANIFEST_FILENAME = "SOURCE_IMPORT_MANIFEST.json"
SOURCE_IMPORT_MANIFEST_RELPATH = f"05_CONTROL/{SOURCE_IMPORT_MANIFEST_FILENAME}"

#: Schema identifier persisted inside the manifest; a mismatching schema on
#: an existing manifest is a conflict, never silently upgraded.
SOURCE_IMPORT_MANIFEST_SCHEMA = "encomm-pcc.source-import-manifest/v1"

#: Canonical output paths per import role (contract paths of the workspace).
_BLUEPRINT_CANONICAL = "00_SOURCE_OF_TRUTH/MASTER_BLUEPRINT.md"
_TEMPLATE_CANONICAL = "01_OFFICIAL/APPLICATION_TEMPLATE.md"
_PROPOSAL_CANONICAL = "03_PROPOSAL/MASTER_PROPOSAL.md"

_IMPORTS_DIR_BY_ROLE = {
    "master_blueprint": "00_SOURCE_OF_TRUTH/IMPORTS",
    "application_template": "01_OFFICIAL/IMPORTS",
    "official_document": "01_OFFICIAL/IMPORTS",
    "existing_proposal": "00_SOURCE_OF_TRUTH/IMPORTS",
}

_CANONICAL_BY_ROLE = {
    "master_blueprint": _BLUEPRINT_CANONICAL,
    "application_template": _TEMPLATE_CANONICAL,
    "existing_proposal": _PROPOSAL_CANONICAL,
}


class SourceImportError(RuntimeError):
    """An import was refused or failed — always operator-actionable."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"[{reason}] {message}")


@dataclass(slots=True)
class SourceImportRecord:
    """One immutable import record persisted in the durable manifest."""

    original_filename: str
    import_role: str
    original_path: str          # workspace-relative, preserved bytes
    sha256_original: str        # SHA-256 of the ORIGINAL bytes
    size_bytes: int
    media_type: str             # normalized lowercase suffix, e.g. ".pdf"
    normalized_path: str        # workspace-relative canonical output
    extraction_status: str      # "ok" or a machine reason
    extraction_warning: str = ""  # non-fatal warning (e.g. empty pdf pages)
    replaced_previous: bool = False
    backup_path: str = ""       # workspace-relative frozen old bytes, if any

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_filename": self.original_filename,
            "import_role": self.import_role,
            "original_path": self.original_path,
            "sha256_original": self.sha256_original,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "normalized_path": self.normalized_path,
            "extraction_status": self.extraction_status,
            "extraction_warning": self.extraction_warning,
            "replaced_previous": self.replaced_previous,
            "backup_path": self.backup_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceImportRecord":
        return cls(
            original_filename=str(data.get("original_filename") or ""),
            import_role=str(data.get("import_role") or ""),
            original_path=str(data.get("original_path") or ""),
            sha256_original=str(data.get("sha256_original") or ""),
            size_bytes=int(data.get("size_bytes") or 0),
            media_type=str(data.get("media_type") or ""),
            normalized_path=str(data.get("normalized_path") or ""),
            extraction_status=str(data.get("extraction_status") or ""),
            extraction_warning=str(data.get("extraction_warning") or ""),
            replaced_previous=bool(data.get("replaced_previous") or False),
            backup_path=str(data.get("backup_path") or ""),
        )


# ---------------------------------------------------------------------------
# extraction — real parsers, lazily imported, never OCR
# ---------------------------------------------------------------------------
def _extract_text_md_txt(raw: bytes) -> str:
    return raw.decode("utf-8", errors="strict")


def _extract_text_docx(path: Path) -> tuple[str, str]:
    try:
        import docx  # python-docx (lazy: heavy, only needed on import)
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SourceImportError(
            "missing_dependency",
            "python-docx is not installed; DOCX import is unavailable.",
        ) from exc
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise SourceImportError(
            "docx_parse_failed", f"the DOCX file could not be parsed: {exc}"
        ) from exc
    parts: list[str] = []
    # Paragraphs and tables in document order would require walking the body
    # XML; deterministic approximation: all paragraphs, then all tables
    # rendered as pipe rows (tables after prose), each cell bounded.
    for paragraph in document.paragraphs:
        parts.append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            parts.append("| " + " | ".join(cells) + " |")
    return "\n".join(parts), ""


def _extract_text_pdf(path: Path) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SourceImportError(
            "missing_dependency",
            "pypdf is not installed; PDF import is unavailable.",
        ) from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise SourceImportError(
            "pdf_parse_failed", f"the PDF file could not be parsed: {exc}"
        ) from exc
    page_texts: list[str] = []
    empty_pages = 0
    for index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # a single broken page must not kill the import
            text = ""
        if not text.strip():
            empty_pages += 1
        page_texts.append(f"<!-- page {index + 1} -->\n{text.strip()}")
    warning = ""
    if empty_pages:
        warning = f"{empty_pages} of {len(reader.pages)} pages produced no text"
    return "\n\n".join(page_texts), warning


def _extract_text_rtf(raw: bytes) -> str:
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SourceImportError(
            "missing_dependency",
            "striprtf is not installed; RTF import is unavailable.",
        ) from exc
    try:
        text = rtf_to_text(raw.decode("latin-1", errors="replace"))
    except Exception as exc:
        raise SourceImportError(
            "rtf_parse_failed", f"the RTF file could not be parsed: {exc}"
        ) from exc
    return text or ""


def extract_text(source_path: Path, suffix: str) -> tuple[str, str]:
    """Extract text from ``source_path`` — returns ``(text, warning)``.

    Raises :class:`SourceImportError` when the format is unsupported, the
    parser is missing, the document is corrupt, or extraction recovers no
    usable text.  Empty canonical sources are NEVER produced silently.
    """
    suffix = suffix.lower()
    if suffix in {".md", ".txt"}:
        try:
            raw = source_path.read_bytes()
        except OSError as exc:
            raise SourceImportError(
                "source_unreadable", f"cannot read {source_path.name}: {exc}"
            ) from exc
        try:
            text = _extract_text_md_txt(raw)
        except UnicodeDecodeError as exc:
            raise SourceImportError(
                "not_utf8",
                f"{source_path.name} is not valid UTF-8 text: {exc}",
            ) from exc
        warning = ""
    elif suffix == ".docx":
        text, warning = _extract_text_docx(source_path)
    elif suffix == ".pdf":
        text, warning = _extract_text_pdf(source_path)
    elif suffix == ".rtf":
        text = _extract_text_rtf(source_path.read_bytes())
        warning = ""
    else:
        raise SourceImportError(
            "unsupported_format",
            f"'{suffix or '(no suffix)'}' is not an importable format "
            f"(supported: {', '.join(sorted(SUPPORTED_IMPORT_SUFFIXES))}).",
        )
    if not text.strip():
        raise SourceImportError(
            "extraction_empty",
            f"extraction recovered no text from {source_path.name}; "
            "refusing to create an empty canonical source (scanned/image PDFs "
            "need an external OCR pass — deliberately out of scope).",
        )
    if len(text) > MAX_NORMALIZED_CHARS:
        raise SourceImportError(
            "extraction_oversized",
            f"extracted text exceeds {MAX_NORMALIZED_CHARS} characters "
            f"({len(text)}); split or trim the source before importing — "
            "the importer never silently truncates.",
        )
    return text, warning


# ---------------------------------------------------------------------------
# deterministic helpers
# ---------------------------------------------------------------------------
def _stable_name(filename: str) -> str:
    """Deterministic filesystem-safe stable name for an imported file."""
    stem = Path(filename).stem.strip().lower()
    stem = re.sub(r"[^a-z0-9._-]+", "-", stem).strip("-.")
    if not stem:
        stem = "document"
    return stem[:80]


def _workspace_path(root: Path, relpath: str) -> Path:
    return root.joinpath(*relpath.split("/"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-import-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_text_lf(target: Path, text: str) -> None:
    """Normalized canonical output: UTF-8, LF newlines, trailing newline."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    _atomic_write_bytes(target, normalized.encode("utf-8"))


def _next_original_path(directory: Path, filename: str) -> Path:
    """First non-colliding ``<stem>.<ext>`` / ``<stem>-2.<ext>`` … path."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for counter in range(2, 10_000):
        candidate = directory / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
    raise SourceImportError(
        "import_directory_exhausted",
        f"could not find a free original filename under {directory}.",
    )


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def load_source_import_manifest(root: Path) -> dict[str, Any]:
    """Load the durable import manifest (empty structure when absent)."""
    path = _workspace_path(Path(root), SOURCE_IMPORT_MANIFEST_RELPATH)
    if not path.is_file():
        return {"schema": SOURCE_IMPORT_MANIFEST_SCHEMA, "imports": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceImportError(
            "manifest_unreadable", f"{SOURCE_IMPORT_MANIFEST_RELPATH}: {exc}"
        ) from exc
    if not isinstance(data, dict) or data.get("schema") not in (
        SOURCE_IMPORT_MANIFEST_SCHEMA,
    ):
        raise SourceImportError(
            "manifest_schema_conflict",
            f"{SOURCE_IMPORT_MANIFEST_RELPATH} carries schema "
            f"{data.get('schema')!r}, expected {SOURCE_IMPORT_MANIFEST_SCHEMA!r}.",
        )
    if not isinstance(data.get("imports"), list):
        raise SourceImportError(
            "manifest_invalid", "manifest 'imports' must be a list."
        )
    return data


def _append_manifest(root: Path, record: SourceImportRecord) -> None:
    data = load_source_import_manifest(root)
    data["imports"].append(record.to_dict())
    _atomic_write_bytes(
        _workspace_path(root, SOURCE_IMPORT_MANIFEST_RELPATH),
        (
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# the importer
# ---------------------------------------------------------------------------
def import_source(
    root: Path,
    source_path: Path,
    *,
    import_role: str,
    replace: bool = False,
) -> SourceImportRecord:
    """Import ONE source file — preserve original bytes, derive canonical text.

    ``import_role`` is one of ``master_blueprint``, ``application_template``,
    ``official_document``, ``existing_proposal``.  For the three roles with a
    canonical contract output, an existing non-empty canonical file is NEVER
    overwritten unless ``replace=True`` — and even then the old exact bytes
    are frozen into a backup file first and the replacement is recorded.
    """
    root = Path(root)
    source_path = Path(source_path)
    if import_role not in _IMPORTS_DIR_BY_ROLE:
        raise SourceImportError(
            "unknown_import_role",
            f"import_role must be one of {sorted(_IMPORTS_DIR_BY_ROLE)}; "
            f"got {import_role!r}.",
        )
    if not source_path.is_file():
        raise SourceImportError(
            "source_missing", f"{source_path} does not exist or is not a file."
        )
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_IMPORT_SUFFIXES:
        raise SourceImportError(
            "unsupported_format",
            f"'{suffix or '(no suffix)'}' is not importable (supported: "
            f"{', '.join(sorted(SUPPORTED_IMPORT_SUFFIXES))}).",
        )
    try:
        original_bytes = source_path.read_bytes()
    except OSError as exc:
        raise SourceImportError(
            "source_unreadable", f"cannot read {source_path.name}: {exc}"
        ) from exc
    if not original_bytes:
        raise SourceImportError("source_empty", f"{source_path.name} is empty.")
    if len(original_bytes) > MAX_IMPORT_SOURCE_BYTES:
        raise SourceImportError(
            "source_oversized",
            f"{source_path.name} exceeds {MAX_IMPORT_SOURCE_BYTES} bytes.",
        )

    text, warning = extract_text(source_path, suffix)

    # -- preserve the ORIGINAL bytes verbatim ------------------------------
    imports_dir = _workspace_path(root, _IMPORTS_DIR_BY_ROLE[import_role])
    imports_dir.mkdir(parents=True, exist_ok=True)
    original_target = _next_original_path(imports_dir, source_path.name)
    _atomic_write_bytes(original_target, original_bytes)
    original_rel = original_target.relative_to(root).as_posix()

    # -- canonical output (backup-before-replace) ---------------------------
    replaced_previous = False
    backup_rel = ""
    canonical_rel = _CANONICAL_BY_ROLE.get(import_role)
    if canonical_rel is None:
        # official documents: one normalized file per stable name
        canonical_rel = f"01_OFFICIAL/NORMALIZED/{_stable_name(source_path.name)}.md"
    canonical_target = _workspace_path(root, canonical_rel)
    if canonical_target.exists() and canonical_target.read_bytes().strip():
        if not replace:
            raise SourceImportError(
                "canonical_exists",
                f"{canonical_rel} already holds content; re-importing over it "
                "requires an explicit replace action (the existing proposal/"
                "blueprint is never silently overwritten).",
            )
        # Freeze the old exact bytes under the role's IMPORTS/backups first.
        backup_dir = _workspace_path(
            root, _IMPORTS_DIR_BY_ROLE[import_role] + "/backups"
        )
        backup_path = _next_original_path(
            backup_dir,
            f"{canonical_target.stem}-previous{canonical_target.suffix}",
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(backup_path, canonical_target.read_bytes())
        backup_rel = backup_path.relative_to(root).as_posix()
        replaced_previous = True

    _atomic_write_text_lf(canonical_target, text)
    canonical_rel = canonical_target.relative_to(root.resolve()).as_posix()

    record = SourceImportRecord(
        original_filename=source_path.name,
        import_role=import_role,
        original_path=original_rel,
        sha256_original=_sha256_bytes(original_bytes),
        size_bytes=len(original_bytes),
        media_type=suffix,
        normalized_path=canonical_rel,
        extraction_status="ok",
        extraction_warning=warning,
        replaced_previous=replaced_previous,
        backup_path=backup_rel,
    )
    _append_manifest(root, record)
    return record


def import_proposal(root: Path, source_path: Path) -> SourceImportRecord:
    """Import an EXISTING proposal draft into the (still empty) master.

    Refuses when ``03_PROPOSAL/MASTER_PROPOSAL.md`` already holds content —
    use :func:`import_source` with ``replace=True`` for an explicit,
    backed-up, recorded replacement.
    """
    master = _workspace_path(Path(root), _PROPOSAL_CANONICAL)
    if master.exists() and master.read_bytes().strip():
        raise SourceImportError(
            "master_not_empty",
            f"{_PROPOSAL_CANONICAL} is non-empty; importing an existing "
            "proposal over it requires an explicit replace action.",
        )
    return import_source(
        root, source_path, import_role="existing_proposal", replace=False
    )


# ---------------------------------------------------------------------------
# status helpers (UI-facing, read-only)
# ---------------------------------------------------------------------------
def blueprint_status(root: Path) -> str:
    """``READY`` / ``MISSING`` / ``EMPTY`` for the canonical blueprint."""
    return _canonical_status(_workspace_path(Path(root), _BLUEPRINT_CANONICAL))


def template_status(root: Path) -> str:
    """``READY`` / ``MISSING`` / ``EMPTY`` for the canonical template."""
    return _canonical_status(_workspace_path(Path(root), _TEMPLATE_CANONICAL))


def _canonical_status(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    try:
        if path.read_bytes().strip():
            return "READY"
    except OSError:
        return "MISSING"
    return "EMPTY"
