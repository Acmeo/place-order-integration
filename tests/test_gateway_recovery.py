"""Asymmetry 3: transient 503 + payment-handler recovery.

POST /v1/orders with pm-503-once -> 503 with status:"pending" (the order
is PENDING in the DB but not yet authorized). The payment-handler loop
picks the PENDING order back up; on its retry the fake gateway responds
200 authorized for that Idempotency-Key (which it remembered as
"already 503'd once, recover on next attempt"), and the order reaches
CONFIRMED.

⚠️ This test is also the end-to-end check on Sales' Idempotency-Key
contract: recovery only works if the handler reuses the same key as the
synchronous attempt. If recovery never happens, that is a finding -- do
not relax the assertion to make it pass; report the discrepancy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


def test_pm_503_once_recovers_to_confirmed(
    post_order_fn: Callable[..., httpx.Response],
    http_client: httpx.Client,
    wait_for_status: Callable[..., str],
    wait_for_notification_fn: Callable[..., dict[str, Any]],
) -> None:
    response = post_order_fn(payment_method_token="pm-503-once")

    # The synchronous attempt sees the fake's first 503 for this key.
    # sales-api persists the order as PENDING and surfaces the error
    # body with the order id so the caller can reference it.
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "pending"
    order_id = body["order_id"]

    # The PENDING state must be visible from the DB-backed read before
    # the handler retries.
    initial = http_client.get(f"/v1/orders/{order_id}")
    assert initial.status_code == 200
    assert initial.json()["status"] in {"PENDING", "CONFIRMED"}

    # Now the payment-handler loop should retry the gateway. Because
    # pm-503-once succeeds on the second attempt per Idempotency-Key,
    # Sales must reuse the same key for the recovery to take effect.
    # A long timeout (60s) gives the handler several polls.
    #
    # If the order never reaches CONFIRMED, that is the headline finding
    # of this test (likely a bug in Sales' idempotency-key reuse on
    # PENDING recovery, or pm-503-once on the fake not matching how
    # Sales' handler retries). Do NOT slacken this assertion.
    final = wait_for_status(order_id=order_id, expected="CONFIRMED", timeout=60.0)
    assert final == "CONFIRMED"

    # Once confirmed, the rest of the happy path flows: outbox-relay
    # publishes OrderConfirmed, the consumers project it, the
    # shipping-dispatcher arranges shipping.
    wait_for_notification_fn(order_id=order_id, kind="ORDER_CONFIRMED", timeout=30.0)
    wait_for_notification_fn(order_id=order_id, kind="SHIPPING_ARRANGED", timeout=30.0)
