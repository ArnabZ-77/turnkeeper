from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.charter import (
    ALL_REMEDIES,
    Charter,
    canonical_dumps,
    canonical_json,
    charter_sha256,
    collateral_violation,
    due_date,
    quorum_tuple,
    recipient_for_round,
    rounds_total,
)


class TestValidation:
    def test_demo_charter_validates(self, charter):
        assert charter.amount == Decimal("200.00")
        assert rounds_total(charter) == 6
        assert quorum_tuple(charter) == (4, 6)
        assert charter.remedy_menu == list(ALL_REMEDIES)
        assert charter.buffer.seed == Decimal("250.00")
        assert charter.fee_policy == "net_pot"

    @pytest.mark.parametrize("raw", ["200", "200.0", 200, Decimal("2E+2")])
    def test_amount_is_normalized_to_two_places(self, make_charter, raw):
        c = make_charter(amount=raw)
        assert c.amount == Decimal("200.00")
        assert str(c.amount) == "200.00"

    @pytest.mark.parametrize("raw", ["200.005", "0.00", "-5.00", "abc", "NaN"])
    def test_bad_amounts_rejected(self, make_charter, raw):
        with pytest.raises(ValidationError):
            make_charter(amount=raw)

    def test_buffer_seed_rejects_negative(self, make_charter):
        with pytest.raises(ValidationError):
            make_charter(buffer={"seed": "-1.00"})

    @pytest.mark.parametrize("handle", ["Ravi", "1ravi", "r", "a" * 17, "ravi-k", "ravi k", ""])
    def test_bad_handles_rejected(self, make_charter, handle):
        members = [{"handle": handle, "name": "X"}, {"handle": "dev", "name": "Dev"}]
        with pytest.raises(ValidationError):
            make_charter(members=members, rotation=[handle, "dev"], quorum="1/2")

    @pytest.mark.parametrize("handle", ["ab", "ravi_2", "a123456789bcdefg"])
    def test_good_handles(self, make_charter, handle):
        members = [{"handle": handle, "name": "X"}, {"handle": "dev", "name": "Dev"}]
        c = make_charter(members=members, rotation=[handle, "dev"], quorum="1/2")
        assert c.members[0].handle == handle

    def test_duplicate_handles_rejected(self, make_charter):
        members = [{"handle": "dev", "name": "A"}, {"handle": "dev", "name": "B"}]
        with pytest.raises(ValidationError, match="duplicate"):
            make_charter(members=members, rotation=["dev", "dev"], quorum="1/2")

    @pytest.mark.parametrize(
        "rotation",
        [
            ["meena", "asha", "dev", "lin", "tomas"],  # ravi missing
            ["meena", "asha", "dev", "lin", "tomas", "ravi", "ravi"],  # duplicate
            ["meena", "asha", "dev", "lin", "tomas", "zed"],  # unknown handle
        ],
    )
    def test_rotation_must_be_a_permutation(self, make_charter, rotation):
        with pytest.raises(ValidationError, match="permutation"):
            make_charter(rotation=rotation)

    def test_collateral_rule_rejects_untracked_before_tracked(self, make_charter):
        with pytest.raises(ValidationError, match="collateral rule: ravi") as ei:
            make_charter(rotation=["meena", "ravi", "asha", "dev", "lin", "tomas"])
        assert "asha" in str(ei.value)

    def test_collateral_rule_allows_tracked_members_in_any_order(self, make_charter):
        c = make_charter(rotation=["asha", "meena", "ravi", "dev", "lin", "tomas"])
        assert c.rotation[:2] == ["asha", "meena"]

    def test_collateral_rule_is_trivial_when_nobody_has_a_track_record(self, make_charter, charter_dict):
        members = [dict(m, track_record_rounds=0) for m in charter_dict()["members"]]
        c = make_charter(members=members, rotation=["ravi", "meena", "asha", "dev", "lin", "tomas"])
        assert collateral_violation(c.rotation, c.members) is None

    def test_collateral_violation_helper_names_the_pair(self, charter):
        assert collateral_violation(["ravi", "dev", "meena"], charter.members) == ("ravi", "meena")
        assert collateral_violation(["meena", "asha", "ravi"], charter.members) is None

    @pytest.mark.parametrize("quorum", ["4/5", "0/6", "7/6", "abc", "4", "4/6/1"])
    def test_bad_quorum_rejected(self, make_charter, quorum):
        with pytest.raises(ValidationError):
            make_charter(quorum=quorum)

    @pytest.mark.parametrize("quorum", ["1/6", "6/6", "4/6"])
    def test_good_quorum(self, make_charter, quorum):
        k, n = (int(x) for x in quorum.split("/"))
        assert quorum_tuple(make_charter(quorum=quorum)) == (k, n)

    def test_charter_is_frozen(self, charter):
        with pytest.raises(ValidationError):
            charter.amount = Decimal("1.00")


class TestCanonicalJson:
    def test_canonical_json_is_sorted_compact_and_typed(self, charter):
        s = canonical_json(charter)
        assert s.startswith(
            '{"amount":"200.00","buffer":{"retention_pct":0,"seed":"250.00","source":"organizer"},"cadence":"monthly"'
        )
        assert '"start_date":"2026-11-01"' in s
        assert ": " not in s and ", " not in s and "\n" not in s
        parsed = json.loads(s)
        assert list(parsed) == sorted(parsed)
        assert parsed["amount"] == "200.00"  # a string, never a number

    def test_hash_is_sha256_of_canonical_utf8(self, charter):
        h = charter_sha256(charter)
        assert len(h) == 64
        int(h, 16)  # hex
        assert h == hashlib.sha256(canonical_json(charter).encode("utf-8")).hexdigest()

    def test_hash_stable_under_key_reordering(self, charter_dict):
        a = Charter.model_validate(charter_dict())
        reordered = dict(reversed(list(charter_dict().items())))
        reordered["members"] = [dict(reversed(list(m.items()))) for m in reordered["members"]]
        reordered["buffer"] = dict(reversed(list(reordered["buffer"].items())))
        b = Charter.model_validate(reordered)
        assert canonical_json(a) == canonical_json(b)
        assert charter_sha256(a) == charter_sha256(b)

    def test_hash_stable_under_whitespace_and_json_roundtrip(self, charter):
        pretty = json.dumps(json.loads(charter.model_dump_json()), indent=4)
        assert "\n" in pretty and "    " in pretty
        again = Charter.model_validate_json(pretty)
        assert canonical_json(again) == canonical_json(charter)
        assert charter_sha256(again) == charter_sha256(charter)

    def test_hash_stable_under_amount_spelling(self, make_charter):
        hashes = {
            charter_sha256(make_charter(amount=raw)) for raw in ("200", "200.0", "200.00", Decimal("2E+2"))
        }
        assert len(hashes) == 1

    def test_hash_stable_with_explicit_defaults(self, make_charter):
        implicit = make_charter()
        explicit = make_charter(
            fee_policy="net_pot",
            grace_days=3,
            languages=["en"],
            remedy_menu=list(ALL_REMEDIES),
            currency="USD",
        )
        assert charter_sha256(implicit) == charter_sha256(explicit)

    def test_hash_changes_when_a_field_changes(self, make_charter):
        base = charter_sha256(make_charter())
        assert charter_sha256(make_charter(amount="250.00")) != base
        assert charter_sha256(make_charter(rotation=["asha", "meena", "dev", "lin", "tomas", "ravi"])) != base
        assert charter_sha256(make_charter(grace_days=4)) != base

    def test_canonical_dumps_handles_types_and_rejects_float(self):
        s = canonical_dumps({"b": Decimal("1"), "a": date(2026, 1, 2), "s": {"y", "x"}, "n": None, "t": True})
        assert s == '{"a":"2026-01-02","b":"1.00","n":null,"s":["x","y"],"t":true}'
        with pytest.raises(TypeError, match="float"):
            canonical_dumps({"x": 1.5})

    def test_non_ascii_names_hash_deterministically(self, make_charter, charter_dict):
        members = charter_dict()["members"]
        members[1]["name"] = "रवि कुमार"
        c1 = make_charter(members=members)
        c2 = make_charter(members=deepcopy(members))
        assert charter_sha256(c1) == charter_sha256(c2)
        assert "रवि" in canonical_json(c1)


class TestDerivedFacts:
    def test_recipient_for_round(self, charter):
        assert [recipient_for_round(charter, n) for n in range(1, 7)] == [
            "meena",
            "asha",
            "dev",
            "lin",
            "tomas",
            "ravi",
        ]
        for bad in (0, 7):
            with pytest.raises(ValueError, match="out of range"):
                recipient_for_round(charter, bad)

    def test_due_date_monthly(self, charter):
        assert due_date(charter, 1) == date(2026, 11, 1)
        assert due_date(charter, 3) == date(2027, 1, 1)
        assert due_date(charter, 6) == date(2027, 4, 1)

    def test_due_date_monthly_clamps_to_month_end(self, make_charter):
        c = make_charter(start_date="2027-01-31")
        assert due_date(c, 2) == date(2027, 2, 28)
        assert due_date(c, 3) == date(2027, 3, 31)
        assert due_date(c, 4) == date(2027, 4, 30)

    def test_due_date_weekly(self, make_charter):
        c = make_charter(cadence="weekly")
        assert due_date(c, 1) == date(2026, 11, 1)
        assert due_date(c, 4) == date(2026, 11, 22)
        with pytest.raises(ValueError, match="out of range"):
            due_date(c, 7)
