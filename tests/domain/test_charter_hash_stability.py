"""Charter-hash stability snapshot.

Member mandates (and every signed mandate) bind to the charter hash, and the live deployment's circles are
already hashed. Adding the member-mandate feature must not change ``canonical_json`` or ``charter_sha256``:
these pins fail loudly if anything ever does. The mandate lives OUTSIDE the charter, so no charter field was added.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.charter import Charter, canonical_json, charter_sha256

FIXTURE = Path(__file__).resolve().parents[2] / "app" / "fixtures" / "demo_circle.json"
# The demo circle's hash as shipped and as seen in the recorded live run (docs/live-run/raw-2026-10-25).
DEMO_CHARTER_SHA256 = "b85cfed8ada903122d41249e0f8cfab62d1420a137638c921259511cda616f25"


def _demo_charter() -> Charter:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return Charter.model_validate(data["charter"])


def test_demo_fixture_charter_hash_is_pinned():
    assert charter_sha256(_demo_charter()) == DEMO_CHARTER_SHA256


def test_canonical_json_is_sorted_and_has_no_mandate_field():
    parsed = json.loads(canonical_json(_demo_charter()))
    assert list(parsed) == sorted(parsed)
    assert not any("mandate" in key for key in parsed)


def test_charter_has_no_member_mandate_field():
    assert not any("mandate" in name for name in Charter.model_fields)


def test_hash_survives_a_json_round_trip():
    c = _demo_charter()
    again = Charter.model_validate_json(c.model_dump_json())
    assert charter_sha256(again) == charter_sha256(c) == DEMO_CHARTER_SHA256


def test_hash_changes_when_the_charter_changes(make_charter):
    assert charter_sha256(make_charter()) != charter_sha256(make_charter(amount="210.00"))
