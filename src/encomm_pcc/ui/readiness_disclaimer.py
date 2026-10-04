"""The readiness disclaimer label (Session 020 §14).

Imported by the Proposal Mode panel; defined here so the exact contract
string is pinned in ONE module-level constant a test can assert against.
"""

from __future__ import annotations

from ..proposal.readiness import READINESS_DISCLAIMER

__all__ = ["READINESS_DISCLAIMER_LABEL"]

#: The label rendered directly under the readiness value — ALWAYS.
READINESS_DISCLAIMER_LABEL = READINESS_DISCLAIMER
