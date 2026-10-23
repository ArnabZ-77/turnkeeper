import pytest

from app.agent import handles


def test_handle_and_circle_id_validation():
    assert handles.is_handle("ravi") and handles.is_handle("a1_b")
    assert not handles.is_handle("Ravi") and not handles.is_handle("r") and not handles.is_handle("1abc")
    assert not handles.is_handle("a" * 17)
    assert (
        handles.is_circle_id("abc123")
        and not handles.is_circle_id("ABC123")
        and not handles.is_circle_id("abc12")
    )
    assert handles.is_round_n(1) and not handles.is_round_n(0) and not handles.is_round_n(True)
    assert handles.validate_inputs(circle_id="abc123", member="ravi", round_n=3) is None
    assert "circle_id" in handles.validate_inputs(circle_id="nope")
    assert "member" in handles.validate_inputs(member="Ravi")
    assert "round_n" in handles.validate_inputs(round_n=-1)


def test_refs_round_trip():
    assert handles.capture_ref("ravi", 3) == "cap:ravi:r3"
    assert handles.order_ref("asha", 12) == "ord:asha:r12"
    assert handles.payout_ref("dev", 4) == "pay:dev:r4"
    assert handles.parse_ref("cap:ravi:r3") == ("cap", "ravi", 3)
    with pytest.raises(ValueError):
        handles.parse_ref("CAP-123456")
    with pytest.raises(ValueError):
        handles.make_ref("cap", "Ravi", 3)


def test_fill_placeholders_uses_lookup_and_leaves_unknowns():
    ids = {("cap", "asha", 3): "8AB12345CD678901E", ("ord", "asha", 3): "5XY00000ZZ111111Q"}
    text = "Captured {capture_id:asha:r3} (order {order_id:asha:r3}); pending {capture_id:ravi:r3}."
    out = handles.fill_placeholders(text, lambda k, m, n: ids.get((k, m, n)))
    assert out == "Captured 8AB12345CD678901E (order 5XY00000ZZ111111Q); pending {capture_id:ravi:r3}."
    assert handles.fill_placeholders("", lambda *a: None) == ""


def test_first_name():
    assert handles.first_name("Asha Patel") == "Asha"
    assert handles.first_name("  Lin ") == "Lin"
    assert handles.first_name(None) == "" and handles.first_name("") == ""
