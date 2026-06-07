"""Exactly-once effect on at-least-once delivery (stretch).

We do not synthetically inject a redelivery here -- that requires
restarting `sales-outbox-relay` mid-test, which is brittle in
testcontainers. Instead we observe the same invariant indirectly:
after a happy-path order, the analytics counters reflect the *set* of
event_ids the consumer has seen, not the redelivery count. Because the
relay republishes any pending outbox row on restart and the consumer
deduplicates on event_id, multiple test runs against the same stream
do not double-count this run's contribution (they would if dedup were
absent).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx


def test_consumers_do_not_double_count_on_redelivery_chain(
    post_order_fn: Callable[..., httpx.Response],
    wait_for_notification_fn: Callable[..., dict[str, Any]],
    wait_for_analytics_count_fn: Callable[..., dict[str, Any]],
    analytics_client: httpx.Client,
) -> None:
    response = post_order_fn(payment_method_token="pm-integration")
    assert response.status_code == 201
    order_id = response.json()["order_id"]

    wait_for_notification_fn(order_id=order_id, kind="ORDER_CONFIRMED")
    wait_for_notification_fn(order_id=order_id, kind="SHIPPING_ARRANGED")
    metrics = wait_for_analytics_count_fn(event_type="ShippingArranged", minimum=1)

    # One order, one of each event_type relevant to it.
    assert metrics["counts"]["OrderConfirmed"] == 1
    assert metrics["counts"]["ShippingArranged"] == 1
    assert metrics["counts"].get("OrderCancelled", 0) == 0
    # Exactly the one order's revenue, not double.
    assert Decimal(metrics["revenue_total"]) == Decimal("121.00")

    # A few seconds of additional polling should not change the counters
    # if dedup is working. (The relay re-publishes any row whose
    # mark_published commit failed; the consumers see those as
    # duplicates on the dedup table.)
    import time

    time.sleep(3.0)
    after = analytics_client.get("/v1/metrics").json()
    assert after["counts"]["OrderConfirmed"] == 1
    assert after["counts"]["ShippingArranged"] == 1
    assert Decimal(after["revenue_total"]) == Decimal("121.00")
