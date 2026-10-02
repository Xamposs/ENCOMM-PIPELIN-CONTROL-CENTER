"""Session 015 — ORCHESTRATOR integration packet/parser contract tests.

Covers the mandated PACKET/PARSER matrix sections (brief §18, items 1–12):

* deterministic integration packet; ORCHESTRATOR role required; the exact
  input hash embedded;
* strict envelope required — bare JSON rejected, duplicate envelope
  rejected, malformed JSON rejected, wrong role rejected, iteration
  mismatch rejected, input hash mismatch rejected, empty revised proposal
  rejected, oversized revised proposal rejected, malformed item arrays
  rejected.

All tests are OFFLINE and deterministic: no driver, no network, no model
call — pure package only.
"""

from __future__ import annotations

import json

import pytest

import encomm_pcc.proposal as pp
from encomm_pcc.proposal.integration_models import INTEGRATION_ITEM_ACTIONS
from encomm_pcc.proposal.integration_parser import (
    MAX_INTEGRATION_REVISED_CHARS,
    ProposalIntegrationParseError,
)

HASH1 = "a" * 64
REVISION = "rev-1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def integration_payload(
    *,
    iteration: int = 1,
    input_hash: str = HASH1,
    role: str = "ORCHESTRATOR",
    revised: str = "# REVISED\n\nnew content.\n",
    summary: str = "applied one finding",
    applied: list | None = None,
    rejected: list | None = None,
    unresolved: list | None = None,
) -> dict:
    return {
        "role": role,
        "iteration_number": iteration,
        "input_proposal_hash": input_hash,
        "summary": summary,
        "applied_items": applied if applied is not None else [],
        "rejected_items": rejected if rejected is not None else [],
        "unresolved_items": unresolved if unresolved is not None else [],
        "revised_proposal": revised,
    }


def envelope(payload: dict) -> str:
    return (
        f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n{json.dumps(payload)}\n"
        f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
    )


def parse_ok(text: str):
    return pp.parse_proposal_integration(
        text, expected_iteration=1, expected_input_hash=HASH1
    )


def make_inputs(**overrides):
    kwargs = dict(
        current_proposal_text="# CURRENT\n\nproposal body.\n",
        current_proposal_hash=HASH1,
        iteration_number=1,
        proposal_revision=REVISION,
        integration_brief_text='{"schema": "encomm-pcc.integration-brief/v1"}',
        source_snapshot_text="### facts\n",
    )
    kwargs.update(overrides)
    return pp.ProposalIntegrationInputs(**kwargs)


# ---------------------------------------------------------------------------
# PACKET (1–3)
# ---------------------------------------------------------------------------
class TestIntegrationPacket:
    def test_1_packet_is_deterministic(self) -> None:
        a = pp.build_integration_packet(make_inputs())
        b = pp.build_integration_packet(make_inputs())
        assert a.prompt_text == b.prompt_text
        assert a == b

    def test_1_changed_inputs_change_the_prompt(self) -> None:
        a = pp.build_integration_packet(make_inputs())
        b = pp.build_integration_packet(
            make_inputs(current_proposal_text="# CURRENT\n\nchanged body.\n")
        )
        assert a.prompt_text != b.prompt_text

    def test_2_orchestrator_role_is_the_addressee(self) -> None:
        packet = pp.build_integration_packet(make_inputs())
        assert packet.role is pp.ProposalRole.ORCHESTRATOR
        assert "ProposalRole.ORCHESTRATOR" in packet.prompt_text
        assert "ONLY integration authority" in packet.prompt_text

    def test_3_exact_input_hash_is_embedded(self) -> None:
        packet = pp.build_integration_packet(make_inputs())
        assert HASH1 in packet.prompt_text
        assert packet.input_proposal_hash == HASH1

    def test_reviewer_recommendations_not_commands_is_embedded(self) -> None:
        packet = pp.build_integration_packet(make_inputs())
        assert "RECOMMENDATIONS, not commands" in packet.prompt_text

    def test_one_complete_revised_proposal_rule_is_embedded(self) -> None:
        packet = pp.build_integration_packet(make_inputs())
        assert "ONE COMPLETE revised proposal" in packet.prompt_text
        assert pp.PROPOSAL_INTEGRATION_ENVELOPE_START in packet.prompt_text
        assert pp.PROPOSAL_INTEGRATION_ENVELOPE_END in packet.prompt_text

    def test_missing_proposal_text_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            pp.build_integration_packet(make_inputs(current_proposal_text="   "))

    def test_missing_hash_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            pp.build_integration_packet(make_inputs(current_proposal_hash=""))

    def test_iteration_number_must_be_positive_int(self) -> None:
        with pytest.raises(ValueError):
            pp.build_integration_packet(make_inputs(iteration_number=0))

    def test_official_requirements_flag_requires_text(self) -> None:
        with pytest.raises(ValueError):
            pp.build_integration_packet(
                make_inputs(official_requirements_available=True)
            )

    def test_oversized_section_is_refused(self) -> None:
        with pytest.raises(ValueError):
            pp.build_integration_packet(
                make_inputs(
                    integration_brief_text="x" * 500_000,
                )
            )


# ---------------------------------------------------------------------------
# PARSER — happy path (4)
# ---------------------------------------------------------------------------
class TestIntegrationParserHappyPath:
    def test_4_strict_envelope_required_and_parsed(self) -> None:
        result = parse_ok(envelope(integration_payload()))
        assert result.iteration_number == 1
        assert result.input_proposal_hash == HASH1
        assert result.revised_proposal.startswith("# REVISED")
        assert result.applied_items == []
        assert result.summary == "applied one finding"

    def test_prose_around_correct_envelope_is_accepted(self) -> None:
        text = f"Sure, here is the integration:\n{envelope(integration_payload())}\nDone."
        result = parse_ok(text)
        assert result.revised_proposal.startswith("# REVISED")

    def test_round_trip_through_models(self) -> None:
        payload = integration_payload(
            applied=[{"item_id": "finding:1", "action": "applied", "reason": "valid"}],
            rejected=[{"item_id": "patch:2", "action": "rejected", "reason": "conflicts"}],
            unresolved=[{"item_id": "finding:3", "action": "unresolved", "reason": "needs data"}],
        )
        result = parse_ok(envelope(payload))
        assert pp.ProposalIntegrationResult.from_dict(result.to_dict()) == result

    def test_result_json_friendly(self) -> None:
        result = parse_ok(envelope(integration_payload()))
        blob = json.dumps(result.to_dict())
        assert isinstance(json.loads(blob), dict)

    def test_hash_case_is_normalized_not_relaxed(self) -> None:
        payload = integration_payload(input_hash=HASH1.upper())
        result = parse_ok(envelope(payload))
        assert result.input_proposal_hash == HASH1.upper()


# ---------------------------------------------------------------------------
# PARSER — rejections (5–12)
# ---------------------------------------------------------------------------
class TestIntegrationParserRejections:
    def test_5_bare_json_rejected(self) -> None:
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(json.dumps(integration_payload()))
        assert exc.value.reason == "missing_envelope"

    def test_6_duplicate_envelope_rejected(self) -> None:
        text = envelope(integration_payload()) + "\n" + envelope(integration_payload())
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(text)
        assert exc.value.reason == "multiple_envelopes"

    def test_7_malformed_json_rejected(self) -> None:
        text = (
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n"
            "{\"role\": \"ORCHESTRATOR\", broken\n"
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(text)
        assert exc.value.reason == "malformed_json"

    def test_8_wrong_role_rejected(self) -> None:
        payload = integration_payload(role="SCIENTIFIC_REVIEWER")
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "wrong_role"

    def test_8_missing_role_rejected(self) -> None:
        payload = integration_payload()
        del payload["role"]
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "missing_role"

    def test_9_iteration_mismatch_rejected(self) -> None:
        payload = integration_payload(iteration=2)
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "iteration_mismatch"

    def test_9_iteration_as_string_rejected(self) -> None:
        payload = integration_payload(iteration="1")
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "unexpected_type"

    def test_10_input_hash_mismatch_rejected(self) -> None:
        payload = integration_payload(input_hash="b" * 64)
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "input_hash_mismatch"

    def test_10_missing_input_hash_rejected(self) -> None:
        payload = integration_payload()
        del payload["input_proposal_hash"]
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "missing_input_hash"

    def test_11_empty_revised_proposal_rejected(self) -> None:
        payload = integration_payload(revised="   ")
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "empty_field"

    def test_11_missing_revised_proposal_rejected(self) -> None:
        payload = integration_payload()
        del payload["revised_proposal"]
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "unexpected_type"

    def test_11_revised_proposal_as_object_rejected(self) -> None:
        payload = integration_payload(revised={"text": "nope"})
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "unexpected_type"

    def test_12_oversized_revised_proposal_rejected(self) -> None:
        payload = integration_payload(revised="x" * (MAX_INTEGRATION_REVISED_CHARS + 1))
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "oversized_field"

    def test_12_oversized_raw_payload_rejected(self) -> None:
        text = envelope(integration_payload()) + "x" * 2_100_000
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(text)
        assert exc.value.reason == "oversized_payload"

    def test_unterminated_envelope_rejected(self) -> None:
        text = f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n{json.dumps(integration_payload())}\n"
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(text)
        assert exc.value.reason == "unterminated_envelope"

    def test_empty_envelope_rejected(self) -> None:
        text = (
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n"
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(text)
        assert exc.value.reason == "no_json_object"

    def test_non_object_root_rejected(self) -> None:
        text = (
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_START}\n[1, 2, 3]\n"
            f"{pp.PROPOSAL_INTEGRATION_ENVELOPE_END}"
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(text)
        assert exc.value.reason == "unexpected_type"

    def test_empty_output_rejected(self) -> None:
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok("")
        assert exc.value.reason == "missing_envelope"


# ---------------------------------------------------------------------------
# PARSER — item array contract (12, malformed item arrays)
# ---------------------------------------------------------------------------
class TestIntegrationItemArrays:
    def test_valid_items_parse(self) -> None:
        payload = integration_payload(
            applied=[{"item_id": "finding:1", "action": "applied", "reason": "ok"}],
        )
        result = parse_ok(envelope(payload))
        assert len(result.applied_items) == 1
        assert result.applied_items[0].item_id == "finding:1"
        assert result.applied_items[0].action == "applied"

    def test_missing_reason_key_rejected(self) -> None:
        payload = integration_payload(
            applied=[{"item_id": "finding:1", "action": "applied"}],
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "malformed_item"

    def test_unknown_key_rejected(self) -> None:
        payload = integration_payload(
            applied=[
                {
                    "item_id": "f:1",
                    "action": "applied",
                    "reason": "ok",
                    "confidence": 0.9,
                }
            ],
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "malformed_item"

    def test_action_mismatch_within_array_rejected(self) -> None:
        payload = integration_payload(
            rejected=[{"item_id": "f:1", "action": "applied", "reason": "ok"}],
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "item_action_mismatch"

    def test_unknown_action_rejected(self) -> None:
        payload = integration_payload(
            applied=[{"item_id": "f:1", "action": "maybe", "reason": "ok"}],
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "invalid_item_action"

    def test_non_object_item_rejected(self) -> None:
        payload = integration_payload(applied=["finding:1"])
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "unexpected_type"

    def test_empty_item_id_rejected(self) -> None:
        payload = integration_payload(
            applied=[{"item_id": "", "action": "applied", "reason": "ok"}],
        )
        with pytest.raises(ProposalIntegrationParseError) as exc:
            parse_ok(envelope(payload))
        assert exc.value.reason == "empty_field"

    def test_items_default_to_empty_lists(self) -> None:
        payload = integration_payload()
        del payload["applied_items"]
        del payload["rejected_items"]
        del payload["unresolved_items"]
        result = parse_ok(envelope(payload))
        assert result.applied_items == []
        assert result.rejected_items == []
        assert result.unresolved_items == []


# ---------------------------------------------------------------------------
# models / constants
# ---------------------------------------------------------------------------
class TestIntegrationModels:
    def test_actions_whitelist(self) -> None:
        assert INTEGRATION_ITEM_ACTIONS == frozenset(
            {"applied", "rejected", "unresolved"}
        )

    def test_item_rejects_unknown_action(self) -> None:
        with pytest.raises(ValueError):
            pp.ProposalIntegrationItem(item_id="x", action="magic")

    def test_result_rejects_invalid_iteration(self) -> None:
        with pytest.raises(ValueError):
            pp.ProposalIntegrationResult(
                iteration_number=0, input_proposal_hash="h", revised_proposal="t"
            )

    def test_envelope_constants_are_distinct_from_review(self) -> None:
        assert (
            pp.PROPOSAL_INTEGRATION_ENVELOPE_START
            != pp.PROPOSAL_REVIEW_ENVELOPE_START
        )
        assert (
            pp.PROPOSAL_INTEGRATION_ENVELOPE_END != pp.PROPOSAL_REVIEW_ENVELOPE_END
        )
