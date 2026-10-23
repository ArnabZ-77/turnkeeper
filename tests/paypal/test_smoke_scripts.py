"""The D1 smoke scripts (scripts/smoke) against the respx sandbox: JSON lines, PASS/FAIL, exit codes, no secrets."""

from __future__ import annotations

import json
import sys

import httpx
import pytest

from scripts.smoke import _common, b_mit_capture, c_mock_decline, e_balances

ORDER = {
    "id": "ORDER1",
    "status": "COMPLETED",
    "purchase_units": [
        {
            "payments": {
                "captures": [
                    {
                        "id": "CAP1",
                        "status": "COMPLETED",
                        "amount": {"currency_code": "USD", "value": "1.00"},
                        "seller_receivable_breakdown": {
                            "gross_amount": {"currency_code": "USD", "value": "1.00"},
                            "paypal_fee": {"currency_code": "USD", "value": "0.52"},
                            "net_amount": {"currency_code": "USD", "value": "0.48"},
                        },
                    }
                ]
            }
        }
    ],
}


def json_lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_redact_hides_credentials_but_keeps_ids():
    out = _common.redact(
        {
            "access_token": "A.B.C",
            "Authorization": "Bearer x",
            "client_secret": "s",
            "vault_id": "8kk",
            "setup_token_id": "5C9",
            "nested": [{"id_token": "t", "ok": True}],
        }
    )
    assert out["access_token"] == "<redacted>" and out["Authorization"] == "<redacted>"
    assert out["client_secret"] == "<redacted>" and out["nested"][0]["id_token"] == "<redacted>"
    assert out["vault_id"] == "8kk" and out["setup_token_id"] == "5C9" and out["nested"][0]["ok"] is True


async def test_b_mit_capture_pass(settings, paypal_mock, monkeypatch, capsys):
    post = paypal_mock.post("/v2/checkout/orders").mock(return_value=httpx.Response(201, json=ORDER))
    paypal_mock.get("/v2/checkout/orders/ORDER1").mock(return_value=httpx.Response(200, json=ORDER))
    monkeypatch.setattr(sys, "argv", ["b_mit_capture.py", "--vault-id", "VAULT1", "--amount", "1.00"])
    assert await b_mit_capture.main() is True
    sent = json.loads(post.calls[0].request.content)
    assert sent["payment_source"]["paypal"]["vault_id"] == "VAULT1"
    assert sent["purchase_units"][0]["amount"]["value"] == "1.00"
    assert post.calls[0].request.headers["PayPal-Request-Id"].startswith("tk:")
    lines = json_lines(capsys.readouterr().out)
    order = next(line for line in lines if line["step"] == "order")
    assert order["ok"] is True and order["paypal_fee"] == "0.52" and order["net"] == "0.48"
    calls = next(line for line in lines if line["step"] == "calls")
    assert calls["count"] == 2 and calls["token_fetches"] == 1
    assert "test-client-secret" not in json.dumps(lines) and "tok-1" not in json.dumps(lines)


async def test_c_mock_decline_pass_on_422_and_fail_on_success(settings, paypal_mock, monkeypatch, capsys):
    route = paypal_mock.post("/v2/checkout/orders").mock(
        return_value=httpx.Response(
            422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED"}]}
        )
    )
    monkeypatch.setattr(sys, "argv", ["c_mock_decline.py", "--vault-id", "VAULT1"])
    assert await c_mock_decline.main() is True
    header = route.calls[0].request.headers["PayPal-Mock-Response"]
    assert json.loads(header) == {"mock_application_codes": "INSTRUMENT_DECLINED"}
    decline = next(line for line in json_lines(capsys.readouterr().out) if line["step"] == "decline")
    assert decline["ok"] is True and decline["issues"] == ["INSTRUMENT_DECLINED"]

    route.mock(return_value=httpx.Response(201, json=ORDER))
    with pytest.raises(_common.SmokeFailure, match="NOT declined"):
        await c_mock_decline.main()


async def test_e_balances_pass_and_403_fail(settings, paypal_mock, monkeypatch, capsys):
    route = paypal_mock.get("/v1/reporting/balances").mock(
        return_value=httpx.Response(
            200,
            json={
                "balances": [
                    {"currency": "USD", "available_balance": {"currency_code": "USD", "value": "12.34"}}
                ],
                "account_id": "ACC",
                "last_refresh_time": "2026-10-23T00:00:00Z",
            },
        )
    )
    monkeypatch.setattr(sys, "argv", ["e_balances.py"])
    assert await e_balances.main() is True
    line = next(line for line in json_lines(capsys.readouterr().out) if line["step"] == "balances")
    assert line["available"] == "12.34" and line["ok"] is True
    route.mock(return_value=httpx.Response(403, json={"name": "NOT_AUTHORIZED"}))
    with pytest.raises(_common.SmokeFailure, match="403"):
        await e_balances.main()


def test_run_prints_pass_fail_and_exit_codes(capsys):
    async def ok() -> bool:
        return True

    async def failed() -> bool:
        raise _common.SmokeFailure("nope")

    with pytest.raises(SystemExit) as exc:
        _common.run(ok)
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "PASS"
    with pytest.raises(SystemExit) as exc:
        _common.run(failed)
    assert exc.value.code == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "FAIL" and json.loads(out[-2])["reason"] == "nope"
