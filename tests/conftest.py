"""Shared fixtures for the master end-to-end suite.

The session-scoped ``compose`` fixture brings the whole topology up
once per pytest run via testcontainers and gates on sales-api's
``/v1/ready``. Liveness via ``/v1/health`` is not enough -- that lights
up before the DB connection is wired.

The function-scoped ``clean_state`` fixture resets all five Postgres
databases plus Redis between tests, so tests do not see leftover state
from prior orders.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import psycopg
import pytest
import redis
from testcontainers.compose import DockerCompose

_COMPOSE_DIR = Path(__file__).resolve().parent.parent
_RENDER_SCRIPT = _COMPOSE_DIR / "scripts" / "render_env.py"
_ENV_FILE = _COMPOSE_DIR / ".env"

# Host port mappings (must match docker-compose.yml).
POSTGRES_PORT = 35432
REDIS_PORT = 36379
SALES_API_PORT = 38000
CATALOG_PORT = 38001
IDENTITY_PORT = 38002
PAYMENT_PORT = 38003
SHIPPING_PORT = 38004
NOTIFICATIONS_PORT = 38005
ANALYTICS_PORT = 38006

# Postgres credentials (must match docker-compose.yml).
PG_USER = "postgres"
PG_PASS = "postgres"
PG_DATABASES = ("sales", "catalog", "identity", "notifications", "analytics")

# Sales fixtures seeded by catalog/identity entrypoints.
VALID_TOKEN = "session-token-integration"
VALID_PRODUCT_ID = "prod-001"


def _render_env_file() -> None:
    """Render ``.env`` from versions.yml.

    The compose file expects per-service ${<SERVICE>_VERSION} env vars
    and a ${OWNER} for the GHCR namespace. Locally the override file
    builds each satellite from the sibling repo and the resulting image
    is tagged with whatever ${OWNER}/<service>:${version} evaluates to,
    so OWNER defaults to ``local`` (the tag name does not affect the
    build path). Tests that want to validate the actual GHCR bundle can
    set OWNER themselves before invoking pytest.
    """
    env = os.environ.copy()
    env.setdefault("OWNER", "local")
    result = subprocess.run(
        [sys.executable, str(_RENDER_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    _ENV_FILE.write_text(result.stdout)


@pytest.fixture(scope="session")
def compose() -> Iterator[DockerCompose]:
    """Bring the master stack up for the whole session.

    The compose references each service by ghcr.io/${OWNER}/...:${VERSION};
    docker-compose.override.yml shadows that with local builds for the
    six satellites and points the five Sales images at the local Pants
    tags (sales-*:latest). Both files are passed to DockerCompose
    explicitly because passing ``-f`` suppresses compose's automatic
    override discovery.
    """
    _render_env_file()
    with DockerCompose(
        str(_COMPOSE_DIR),
        compose_file_name=["docker-compose.yml", "docker-compose.override.yml"],
        pull=False,
        build=False,
    ) as c:
        # sales-api readiness depends transitively on the migration job
        # and on every external it talks to being healthy, so once /ready
        # responds 200 the whole stack is wired and the four loops are
        # spinning.
        _wait_for_http(
            f"http://localhost:{SALES_API_PORT}/v1/ready", timeout=240.0
        )
        yield c


def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: str | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
            last = f"status={response.status_code}"
        except httpx.RequestError as exc:
            last = str(exc)
        time.sleep(1.0)
    raise TimeoutError(f"{url} did not become ready within {timeout}s ({last})")


@pytest.fixture
def http_client(compose: DockerCompose) -> Iterator[httpx.Client]:
    """HTTP client targeting sales-api at the host-mapped port."""
    with httpx.Client(base_url=f"http://localhost:{SALES_API_PORT}", timeout=10.0) as c:
        yield c


@pytest.fixture
def notifications_client(compose: DockerCompose) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=f"http://localhost:{NOTIFICATIONS_PORT}", timeout=10.0
    ) as c:
        yield c


@pytest.fixture
def analytics_client(compose: DockerCompose) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=f"http://localhost:{ANALYTICS_PORT}", timeout=10.0
    ) as c:
        yield c


@pytest.fixture
def redis_client(compose: DockerCompose) -> Iterator[redis.Redis]:
    client = redis.Redis(host="localhost", port=REDIS_PORT, db=0)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(autouse=True)
def clean_state(compose: DockerCompose) -> Iterator[None]:
    """Reset mutable state between tests.

    - Sales DB: truncate the four operational tables (cascade for FKs).
    - notifications/analytics DBs: truncate projections AND the dedup
      table, so a new test's events look fresh to the consumers.
    - Redis: FLUSHALL clears the stream and every consumer group.
    - catalog/identity DBs are NOT touched -- their seeds are immutable
      fixtures for the integration suite.

    The consumers now self-heal on NOGROUP (the consumer module
    recreates the group reactively the next time XREADGROUP raises),
    so we no longer recreate the groups from the harness after FLUSHALL.
    One poll of latency is absorbed by the per-test timeouts.
    """
    _truncate_sales()
    _truncate_notifications()
    _truncate_analytics()

    r = redis.Redis(host="localhost", port=REDIS_PORT, db=0)
    try:
        r.flushall()
    finally:
        r.close()

    yield


def _truncate_sales() -> None:
    conn = psycopg.connect(
        f"postgresql://{PG_USER}:{PG_PASS}@localhost:{POSTGRES_PORT}/sales"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE TABLE
                    outbox_events,
                    payment_records,
                    inventory_reservations,
                    order_items,
                    orders
                RESTART IDENTITY CASCADE
                """
            )
        conn.commit()
    finally:
        conn.close()


def _truncate_notifications() -> None:
    conn = psycopg.connect(
        f"postgresql://{PG_USER}:{PG_PASS}@localhost:{POSTGRES_PORT}/notifications"
    )
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE notifications, processed_events RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()


def _truncate_analytics() -> None:
    conn = psycopg.connect(
        f"postgresql://{PG_USER}:{PG_PASS}@localhost:{POSTGRES_PORT}/analytics"
    )
    try:
        with conn.cursor() as cur:
            # Reset counters and revenue too; the revenue table is a
            # singleton, so deleting the row is enough (next read will
            # get_or_create it back at 0.00).
            cur.execute(
                "TRUNCATE TABLE event_counters, revenue_total, processed_events "
                "RESTART IDENTITY CASCADE"
            )
        conn.commit()
    finally:
        conn.close()


# --- Polling helpers ---


def post_order(
    http_client: httpx.Client,
    *,
    payment_method_token: str,
    quantity: int = 2,
    token: str = VALID_TOKEN,
) -> httpx.Response:
    """POST /v1/orders with the integration fixtures.

    Sales-api may transiently 503 while an external is still warming up
    even after readiness reports green (the gateway/carrier do not yet
    have their consumer groups, the consumer reaches /health before the
    Redis client has connected, etc.). Retry a small number of times to
    distinguish warmup from a real decline.
    """
    last: httpx.Response | None = None
    for _ in range(5):
        response = http_client.post(
            "/v1/orders",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "items": [{"product_id": VALID_PRODUCT_ID, "quantity": quantity}],
                "payment_method_token": payment_method_token,
            },
        )
        if response.status_code != 503:
            return response
        last = response
        time.sleep(0.5)
    assert last is not None
    return last


def wait_for_order_status(
    http_client: httpx.Client,
    *,
    order_id: str,
    expected: str,
    timeout: float = 30.0,
) -> str:
    """Poll GET /v1/orders/{id} until status == expected."""
    deadline = time.time() + timeout
    last: str | None = None
    while time.time() < deadline:
        response = http_client.get(f"/v1/orders/{order_id}")
        if response.status_code == 200:
            last = str(response.json().get("status"))
            if last == expected:
                return last
        time.sleep(0.5)
    raise TimeoutError(
        f"order {order_id} did not reach status {expected} within {timeout}s; "
        f"last seen: {last or 'absent'}"
    )


def wait_for_notification(
    notifications_client: httpx.Client,
    *,
    order_id: str,
    kind: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Poll notifications until a row of the given kind appears for the order."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = notifications_client.get(
            "/v1/notifications", params={"order_id": order_id}
        )
        if response.status_code == 200:
            for n in response.json().get("notifications", []):
                if n.get("kind") == kind:
                    return cast(dict[str, Any], n)
        time.sleep(0.5)
    raise TimeoutError(
        f"notification kind={kind} for order={order_id} not seen within {timeout}s"
    )


def wait_for_analytics_count(
    analytics_client: httpx.Client,
    *,
    event_type: str,
    minimum: int,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Poll /v1/metrics until counts[event_type] >= minimum."""
    deadline = time.time() + timeout
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        response = analytics_client.get("/v1/metrics")
        if response.status_code == 200:
            last = cast(dict[str, Any], response.json())
            if last["counts"].get(event_type, 0) >= minimum:
                return last
        time.sleep(0.5)
    raise TimeoutError(
        f"analytics counts.{event_type} did not reach >={minimum} within {timeout}s; "
        f"last seen: {last}"
    )


@pytest.fixture
def post_order_fn(http_client: httpx.Client) -> Callable[..., httpx.Response]:
    def _call(**kwargs: Any) -> httpx.Response:
        return post_order(http_client, **kwargs)

    return _call


@pytest.fixture
def wait_for_status(http_client: httpx.Client) -> Callable[..., str]:
    def _call(**kwargs: Any) -> str:
        return wait_for_order_status(http_client, **kwargs)

    return _call


@pytest.fixture
def wait_for_notification_fn(
    notifications_client: httpx.Client,
) -> Callable[..., dict[str, Any]]:
    def _call(**kwargs: Any) -> dict[str, Any]:
        return wait_for_notification(notifications_client, **kwargs)

    return _call


@pytest.fixture
def wait_for_analytics_count_fn(
    analytics_client: httpx.Client,
) -> Callable[..., dict[str, Any]]:
    def _call(**kwargs: Any) -> dict[str, Any]:
        return wait_for_analytics_count(analytics_client, **kwargs)

    return _call
