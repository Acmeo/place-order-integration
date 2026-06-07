"""Asymmetry 2: payment declined drives compensation.

POST /v1/orders with pm-decline -> 402 with the order id and
status:"cancelled" (literal, lowercase). GET /v1/orders/{id} reports
CANCELLED (enum, uppercase). The notifications consumer projects an
ORDER_CANCELLED notification carrying decline_reason. Analytics counts
the cancellation but does not add revenue and does not see a shipping
event.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx


def test_pm_decline_compensates_and_projects(
    post_order_fn: Callable[..., httpx.Response],
    http_client: httpx.Client,
    wait_for_notification_fn: Callable[..., dict[str, Any]],
    wait_for_analytics_count_fn: Callable[..., dict[str, Any]],
    analytics_client: httpx.Client,
) -> None:
    response = post_order_fn(payment_method_token="pm-decline")

    assert response.status_code == 402, response.text
    body = response.json()
    # Error-path status is the literal "cancelled" (lowercase). Sales
    # surfaces enum values in 201 bodies (CONFIRMED) and literal strings
    # in error bodies; this asymmetry is intentional.
    assert body["status"] == "cancelled"
    order_id = body["order_id"]
    assert "detail" in body

    # The DB-backed read returns the enum (uppercase).
    final = http_client.get(f"/v1/orders/{order_id}")
    assert final.status_code == 200
    assert final.json()["status"] == "CANCELLED"

    # Compensation event is projected by notifications with the
    # gateway's decline reason embedded.
    cancelled = wait_for_notification_fn(order_id=order_id, kind="ORDER_CANCELLED")
    assert "insufficient_funds" in cancelled["detail"]

    # Analytics counts the cancellation; revenue must NOT move for the
    # declined order, and no shipping is arranged.
    metrics = wait_for_analytics_count_fn(event_type="OrderCancelled", minimum=1)
    assert metrics["counts"].get("ShippingArranged", 0) == 0
    assert metrics["counts"].get("OrderConfirmed", 0) == 0
    assert Decimal(metrics["revenue_total"]) == Decimal("0.00")

    # Give the shipping-dispatcher and outbox-relay a couple of polls
    # to confirm they don't sneak a ShippingArranged in for a CANCELLED
    # order.
    time.sleep(2.0)
    final_metrics = analytics_client.get("/v1/metrics").json()
    assert final_metrics["counts"].get("ShippingArranged", 0) == 0
