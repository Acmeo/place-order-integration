# place-order-integration

Master end-to-end of the `place_order` reference. Brings up the whole
topology -- Sales (four processes plus its migration), the real
`catalog` and `identity` totalities, the `fake-payment-gateway` and
`fake-shipping-carrier` third-party fakes, and the `notifications` and
`analytics` outbox consumers -- on real infra (Postgres + Redis), and
asserts the three headline asymmetries against the live stack.

There is no WireMock here. Every boundary Sales crosses is a real
service in the compose network.

## Topology

- **One Postgres** with five databases: `sales`, `catalog`, `identity`,
  `notifications`, `analytics`. Each service points at its own database.
- **One Redis** shared by Sales (outbox relay XADDs) and the consumers
  (XREADGROUP).
- **Sales** is **five distinct images** (`sales-api:latest`,
  `sales-payment-handler:latest`, `sales-outbox-relay:latest`,
  `sales-shipping-dispatcher:latest`, `sales-migrate:latest`) built by
  Pants in the Sales repo and referenced here by tag. This compose
  does **not** rebuild Sales.
- **catalog**, **identity**, the two **fakes**, and the two **consumers**
  are uv-based services built by `docker compose build` from their
  sibling repos.

## Host port map (kept high to avoid local collisions)

| Service              | Host port |
|----------------------|-----------|
| postgres             | 35432     |
| redis                | 36379     |
| sales-api            | 38000     |
| catalog              | 38001     |
| identity             | 38002     |
| fake-payment-gateway | 38003     |
| fake-shipping-carrier| 38004     |
| notifications        | 38005     |
| analytics            | 38006     |

## Build order (this is the bit that bites)

Sales' images must exist in the local Docker daemon **before**
`docker compose up`, because the compose references them by tag. The
Makefile encodes this:

```bash
make images   # 1) pants package src/sales/processes::  (in ../sales)
              # 2) docker compose build                  (the satellites)
make test     # runs pytest, which uses testcontainers to bring the
              # stack up and gates on sales-api /v1/ready
make down     # tear down
```

Or `make full` to do build + test + teardown in one shot.

If `make images` fails on the Pants step, ensure `pants` is on `$PATH`
and the `../sales` repo is at the same commit that produced the slim
runtime Dockerfiles in `src/sales/processes/`.

## What gets asserted

`tests/test_happy_path.py` -- happy path:
- `POST /v1/orders` with `pm-integration` returns **201 CONFIRMED**.
- The outbox-relay publishes `OrderConfirmed`; both consumers project it.
- The shipping-dispatcher arranges shipping; consumers project
  `ShippingArranged`.

`tests/test_payment_declined.py` -- compensation on decline:
- `POST` with `pm-decline` returns **402** with `{order_id, status:"cancelled"}`.
- `GET /v1/orders/{id}` reports `CANCELLED`.
- `notifications` records `ORDER_CANCELLED` with `decline_reason=insufficient_funds`.
- `analytics.counts.OrderCancelled` increments; revenue does not.

`tests/test_gateway_recovery.py` -- transient 503 + recovery:
- `POST` with `pm-503-once` returns **503** with `status:"pending"`.
- The payment-handler picks the PENDING order back up on its next poll
  and drives it to **CONFIRMED**.
- This is where the Idempotency-Key contract is exercised end-to-end:
  the handler's retry must reuse the same key as the synchronous
  attempt for the fake to recognise it on the second call. If
  recovery never happens the test surfaces it explicitly rather than
  silently relaxing -- see the assertion in
  `test_gateway_503_recovers_to_confirmed`.

`tests/test_exactly_once.py` -- optional: redelivery does not double
the analytics counters.
