"""Asymmetry 1: happy path on real infra.

POST /v1/orders with pm-integration -> 201 CONFIRMED. The outbox-relay
publishes OrderConfirmed; the shipping-dispatcher arranges shipping and
publishes ShippingArranged. Both consumers project both events.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx


def test_happy_path_201_confirmed_with_consumer_projections(
    post_order_fn: Callable[..., httpx.Response],
    http_client: httpx.Client,
    wait_for_notification_fn: Callable[..., dict[str, Any]],
    wait_for_analytics_count_fn: Callable[..., dict[str, Any]],
) -> None:
    response = post_order_fn(payment_method_token="pm-integration")

    # 201 path uses the enum value (uppercase).
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "CONFIRMED"
    order_id = body["order_id"]
    assert body["currency"] == "EUR"

    # Pricing invariants. Catalog seed has prod-001 at 50.00 EUR; the cart
    # is quantity=2 so subtotal must be 100.00. The total must equal
    # subtotal + tax regardless of the exact rate Sales applies.
    subtotal = Decimal(body["subtotal"])
    tax = Decimal(body["tax"])
    total = Decimal(body["total"])
    assert subtotal == Decimal("100.00")
    assert total == subtotal + tax

    # Sales' ES VAT is 21%, which is deterministic for the integration customer.
    assert tax == Decimal("21.00")
    assert total == Decimal("121.00")

    # The outbox relay + notifications consumer materialise the confirmation.
    confirmed = wait_for_notification_fn(order_id=order_id, kind="ORDER_CONFIRMED")
    assert "121.00" in confirmed["detail"]

    # The shipping-dispatcher loop arranges shipping; that event is
    # then projected by the consumers.
    wait_for_notification_fn(order_id=order_id, kind="SHIPPING_ARRANGED")
    metrics = wait_for_analytics_count_fn(event_type="ShippingArranged", minimum=1)
    assert metrics["counts"]["OrderConfirmed"] >= 1

    # Revenue accumulates the confirmed total (we truncated between
    # tests, so the only confirmed order in this run is this one).
    assert Decimal(metrics["revenue_total"]) >= Decimal("121.00")

    # GET /v1/orders/{id} eventually reports CONFIRMED with shipping.
    final = http_client.get(f"/v1/orders/{order_id}")
    assert final.status_code == 200
    final_body = final.json()
    assert final_body["status"] == "CONFIRMED"
