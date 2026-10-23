"""Idempotency key formats, max lengths and run ids."""

from __future__ import annotations

import pytest

from app.paypal import keys

RUN, CIRCLE = "abcdefgh", "c1x2y3"


def test_formats_match_plan_section_4():
    assert keys.order_request_id(RUN, CIRCLE, 3, "ravi", 1) == "tk:abcdefgh:c1x2y3:3:ravi:a1"
    assert keys.order_request_id(RUN, CIRCLE, 3, "ravi", 2) == "tk:abcdefgh:c1x2y3:3:ravi:a2"
    assert keys.setup_request_id(RUN, CIRCLE, "meena") == "tk:abcdefgh:c1x2y3:setup:meena"
    assert keys.payout_batch_id(RUN, CIRCLE, 3, 1) == "tk:abcdefgh:c1x2y3:3:r1"
    assert keys.payout_batch_id(RUN, CIRCLE, 3, 2) == "tk:abcdefgh:c1x2y3:3:r2"
    assert keys.payout_item_id(RUN, CIRCLE, 3, "meena") == "abcdefghc1x2y33meena"
    assert keys.invoice_number(RUN, CIRCLE, 3, "ravi") == "TK-abcdefgh-c1x2y3-3-ravi"


def test_lengths_are_within_documented_maxima():
    longest_handle = "a" + "b" * 15  # ^[a-z][a-z0-9_]{1,15}$
    assert len(keys.order_request_id(RUN, CIRCLE, 999, longest_handle, 99)) <= keys.MAX_REQUEST_ID_LEN
    assert len(keys.setup_request_id(RUN, CIRCLE, longest_handle)) <= keys.MAX_REQUEST_ID_LEN
    assert len(keys.payout_batch_id(RUN, CIRCLE, 999, 99)) <= keys.MAX_SENDER_BATCH_ID_LEN
    assert len(keys.payout_item_id(RUN, CIRCLE, 999, longest_handle)) <= keys.MAX_SENDER_ITEM_ID_LEN
    assert (keys.MAX_REQUEST_ID_LEN, keys.MAX_SENDER_BATCH_ID_LEN, keys.MAX_SENDER_ITEM_ID_LEN) == (
        108,
        30,
        63,
    )


def test_too_long_keys_are_rejected():
    with pytest.raises(ValueError, match="sender_batch_id"):
        keys.payout_batch_id("r" * 20, CIRCLE, 3, 1)
    with pytest.raises(ValueError, match="sender_item_id"):
        keys.payout_item_id(RUN, CIRCLE, 3, "m" * 60)
    with pytest.raises(ValueError, match="PayPal-Request-Id"):
        keys.order_request_id(RUN, CIRCLE, 3, "m" * 100, 1)


@pytest.mark.parametrize(
    "call",
    [
        lambda: keys.order_request_id(RUN, CIRCLE, 0, "ravi", 1),
        lambda: keys.order_request_id(RUN, CIRCLE, 3, "ravi", 0),
        lambda: keys.order_request_id(RUN, CIRCLE, "3", "ravi", 1),
        lambda: keys.order_request_id(RUN, CIRCLE, 3, "ra:vi", 1),
        lambda: keys.order_request_id("", CIRCLE, 3, "ravi", 1),
        lambda: keys.payout_batch_id(RUN, CIRCLE, 3, 0),
        lambda: keys.setup_request_id(RUN, "c 1", "ravi"),
    ],
)
def test_invalid_segments_are_rejected(call):
    with pytest.raises(ValueError):
        call()


def test_new_run_id_is_8_lowercase_base32_and_unique():
    ids = {keys.new_run_id() for _ in range(200)}
    assert len(ids) == 200
    for run_id in ids:
        assert len(run_id) == 8
        assert set(run_id) <= set(keys.RUN_ID_ALPHABET)
        assert keys.is_run_id(run_id)
    assert not keys.is_run_id("ABCDEFGH")
    assert not keys.is_run_id("abc")
    assert not keys.is_run_id("abcdefg1")  # 0/1/8/9 are not base32
    assert len(keys.order_request_id(keys.new_run_id(), CIRCLE, 1, "asha", 1)) == 28
