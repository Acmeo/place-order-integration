# place-order-integration

Master end-to-end of the `place_order` reference and, simultaneously,
the **compatibility bundle** for the whole system. It does two jobs
that turn out to be the same job:

- **It is the integration test.** Brings up Postgres + Redis + Sales'
  four processes + its migration job + the real `catalog` and
  `identity` totalities + the `fake-payment-gateway` and
  `fake-shipping-carrier` fakes + the `notifications` and `analytics`
  consumers, and asserts the three asymmetries of `place_order`
  against the live stack.

- **It is the versioned bundle.** `versions.yml` pins each of the 11
  service images to a specific tag in GHCR. Every combination that
  lands in `versions.yml` is one that has passed E2E together. Tagged
  releases of this repo are immutable snapshots of the manifest — a
  "compatibility certificate" for the pinned combination.

## About this reference

This repository is the integration point of a reference implementation
accompanying a six-article series on distributed systems architecture by
Alberto Casado Martin. The series argues that most distributed systems are
*attributive totalities* — their parts only acquire meaning in relation to
the whole — and that the canonical microservices reparto routinely confuses
them with *distributive totalities* (sets of independent peers). The full
list of articles is at the bottom of this README; the fifth and sixth
articles describe what this repository wires together and why.

### What this repo is, in that frame

`place-order-integration` is the **materialisation of the e-commerce in
executable form** — the place where the formal parts of the system actually
run together against real infrastructure, exercising the `place_order` flow
end-to-end. It is **not the complete e-commerce**: the reference implements
only the subset of formal parts needed to make `place_order` demonstrable
(Sales, catalog, identity, notifications, analytics). A real e-commerce
would have Reviews, Search, Recommendations, and other formal parts that are
deliberately out of scope here.

The composition this repo orchestrates is shaped by the formal / material
distinction the fourth article makes explicit:

- **Formal parts implemented for real:** Sales (as five process images out
  of one repository — `sales-api`, `payment-handler`, `outbox-relay`,
  `shipping-dispatcher`, `sales-migrate`), `catalog`, `identity`,
  `notifications`, `analytics`. Each has its own repo, image, and CI.
- **Material parts of the e-commerce, simulated** because they are third
  parties from our cut and there is nothing of ours to implement there:
  `fake-payment-gateway`, `fake-shipping-carrier`. They model their
  contracts faithfully and nothing more.
- **Material parts of the e-commerce, real:** Postgres and Redis, run as
  real containers. They are determined by Sales' schemas, queries and
  outbox conventions (per article four, *involution* — a determinable
  totality made into a material part by the formal nexus that consumes it).

### What this repo verifies

The end-to-end suite exercises the three situations that the bad cut would
need an entire substrate of saga / outbox-everywhere / compensation /
reconciliation to survive, and that the right cut simply absorbs:

1. **Happy path** (`pm-integration`): an order closes synchronously
   (`201 CONFIRMED`), the outbox-relay publishes `OrderConfirmed`,
   `shipping-dispatcher` arranges shipping, `notifications` and `analytics`
   project both events. The full chain in finite time.

2. **Payment declined** (`pm-decline`): the external gateway responds 402.
   The order is cancelled cleanly. The only thing unwound is the charge
   attempted at the external boundary — because there is no internal saga
   to compensate, there is nothing internal to roll back. `notifications`
   projects `ORDER_CANCELLED` with the decline reason; `analytics` counts
   the cancellation; revenue does not move.

3. **Gateway 503 → recovery** (`pm-503-once`): the synchronous attempt
   sees a 503 and the order remains `PENDING`. The Sales `payment-handler`
   process retries on its own poll cadence, reuses the same idempotency
   key, the fake gateway now succeeds, and the order reaches `CONFIRMED`.
   The series points to this as the canonical demonstration that the
   distributed-systems machinery (idempotency keys) lives at the real
   boundary — and that, placed there, it is what makes recovery from
   transient failures possible without a coordinated rollback across
   internal services.

### Reading the topology

Per the sixth article: *"the map of where idempotency appears in the code
turns out to be the map of the totality's real boundaries."* In this bundle:

- Idempotency keys flow from Sales to the two fakes (real-third-party
  boundaries).
- `event_id` dedup lives inside `notifications` and `analytics` (the
  broker boundary between Sales and downstream totalities).
- Inside Sales — between its four processes — there is *none* of this:
  they share one database and coordinate by local transactions.

If you are an agent modifying this reference, that map is the diagnostic
you should preserve. If a change requires sprouting saga / dedup / outbox
*inside* Sales, you are about to misplace a cut. If a change requires
removing idempotency at a boundary that already has it, you are about to
make a real boundary unsafe to retry.

### Not a template

The four-totality shape implemented here is not a reparto to copy by
reflex — that reflex is the disease the whole series has described. What
this repository offers is narrower: a single honest cut, carried all the
way to something that runs, so the central claim of the series stops being
something you take on faith.

## Self-contained

This repo references **nothing outside itself**: no `../sales`, no
`../catalog`, nothing on disk. The default execution path is

```bash
make test     # render .env, pull missing images from GHCR, run E2E
```

which exercises whatever combination `versions.yml` pins. Cloning
just this repo is enough to run integration tests against any
published bundle.

For local iteration on a specific service, see *Iterating locally*
below.

## Manifest: `versions.yml`

```yaml
owner: your-github-user   # GHCR namespace; CI overrides via OWNER env

services:
  sales-api: "0.1.0"
  sales-payment-handler: "0.1.0"
  sales-outbox-relay: "0.1.0"
  sales-shipping-dispatcher: "0.1.0"
  sales-migrate: "0.1.0"
  catalog: "0.1.0"
  identity: "0.1.0"
  fake-payment-gateway: "0.1.0"
  fake-shipping-carrier: "0.1.0"
  notifications: "0.1.0"
  analytics: "0.1.0"
```

The GHCR namespace (`OWNER` in `ghcr.io/<owner>/<service>:<version>`)
is supplied at render time via the `OWNER` env var, never pinned here.
That makes the manifest portable across forks.

## How a version lands in the manifest

When any of the seven source repos (5 Sales images + 6 satellites)
publishes a new release, its workflow fires a `repository_dispatch` at
this repo:

```
event-type: service-released
payload:    { "service": "catalog", "version": "0.2.0" }
```

`.github/workflows/e2e.yml` reacts:

1. Renders an ad-hoc `.env` with the manifest's pinned versions for
   every service *except* the one in the payload, which is bumped.
2. Brings the stack up from GHCR (`docker compose -f docker-compose.yml`
   without the override).
3. Runs the E2E suite.
4. If green, **opens an auto-PR** bumping the matching entry in
   `versions.yml`. A human reviews and merges.
5. If red, the workflow fails. No PR is opened. See the failure
   policy below.

The same `e2e.yml` also runs on every PR that touches `versions.yml`
(so manual bumps are validated identically) and on `workflow_dispatch`
with per-service inputs for ad-hoc testing of arbitrary combinations.

## Compatibility policy: expand/contract

Every release of every component must be **backwards-compatible** with
the previous bundle. New fields/endpoints/event-types may be added;
old ones may not be removed in the same release that adds their
replacement. Removal happens in a later release, once consumers have
caught up.

This is the discipline that makes the auto-PR flow work: a satellite
release should never red the bundle, because the contract is the same
shape it was before. Red E2E in the auto-PR is a real regression, not
an expected coordination failure.

### Escape hatch: coordinated RC promotion

For genuinely breaking changes (rare), the two-step ratchet is
insufficient. The escape hatch is:

1. Both repos cut **pre-release** tags (`v0.2.0-rc1`).
2. A maintainer opens a PR here that bumps both entries in
   `versions.yml` to the RC versions.
3. E2E runs against the RC combination. Iterate until green.
4. Both repos then cut the stable tags (`v0.2.0`). A second PR bumps
   the manifest to the stable versions. E2E runs as a final gate.
5. Merge → release.

The bundle never ships with a single side of a breaking change.

### Schema-diff guardrail (future work)

Today the policy is enforced by review and by the E2E catching
regressions in tested paths. A future iteration will store each
satellite's contract (OpenAPI / JSON-schema) in its own repo and run
a diff against the previously-released version on every PR. Removals
without an expand step will fail the satellite's own CI before they
can ever fire a `service-released` event here.

## Releases

A bundle release is a git tag on this repo following CalVer:
`v2026.06.0`, `v2026.06.1`, etc. (this is a snapshot of a tested
combination, not an API version).

`.github/workflows/release.yml` runs on the tag push:

1. Renders `.env` from the `versions.yml` at the tagged commit.
2. Pulls the bundle from GHCR.
3. Runs E2E one more time as the final gate.
4. Creates a GitHub Release with two assets attached:
   - `versions.yml` — the manifest.
   - `docker-compose.yml` — a self-contained compose file (the
     overlay is omitted) that anyone with the release tag can
     `wget && docker compose up` to reproduce the exact bundle.
5. Release notes auto-generated from the version table.

## Topology

- **One Postgres** with five databases (`sales`, `catalog`, `identity`,
  `notifications`, `analytics`). Each service points at its own DB.
- **One Redis** shared by Sales (outbox relay XADDs) and the consumers
  (XREADGROUP).
- **Sales**: five distinct images (`sales-api`, `sales-payment-handler`,
  `sales-outbox-relay`, `sales-shipping-dispatcher`, `sales-migrate`)
  published by Sales' `release.yml` to GHCR. Built locally by Pants
  for development; pulled from GHCR by the release flow.
- **Satellites**: `catalog`, `identity`, `fake-payment-gateway`,
  `fake-shipping-carrier`, `notifications`, `analytics`, each from
  its own `uv`-based repo, each pushing to GHCR on release.

### Host port map

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

## Running

### Default: pull-from-GHCR

```bash
make test
```

`make test` renders `.env` from `versions.yml` and runs the E2E suite.
The test harness brings the compose stack up; `docker compose` pulls
every image not present locally from `ghcr.io/<owner>/<service>:<version>`
using the manifest's pinned versions.

`OWNER` is read from the `owner:` field in `versions.yml`. If you need
a different namespace for one run (e.g. testing against a fork), set
`OWNER=<other>` on the command line — env wins over the manifest.

### Iterating locally on one service

You don't need an override to test a local satellite build: if you run

```bash
# in ../catalog, with OWNER and VERSION matching versions.yml
make image
```

the resulting image is tagged `ghcr.io/<owner>/catalog:<version>`,
which is exactly what the compose ref resolves to. Compose uses the
local copy because it's already present; the other 10 services are
still pulled from GHCR. No override needed.

You only need an override when the local image *cannot* match the
expected tag. The main case is **iterating on Sales**: its Pants build
produces `sales-*:latest` (no namespace), so the GHCR ref won't
match. Copy the template and uncomment the Sales block:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
$EDITOR docker-compose.override.yml      # uncomment the Sales block
make test
```

The override file is gitignored — personal local edits never get
committed to the bundle.

### Bundle validation (override skipped)

```bash
make test-bundle
```

Invokes compose with `-f docker-compose.yml` only, so any local
override is skipped and you exercise the published bundle verbatim.
This is what `release.yml` runs as the final gate before publishing a
GitHub Release.

### Manual ad-hoc combination

`e2e.yml` exposes a `workflow_dispatch` with per-service inputs
(defaulting to the manifest values). Use the Actions tab to test an
arbitrary combination without touching `versions.yml`.

## Layout

```
.github/workflows/
  e2e.yml         dispatch / PR / push / manual; opens auto-PR on green
  release.yml     on tag push v*; creates GitHub Release with assets
docker/
  postgres-init.sql  creates the five per-service databases
docker-compose.yml          bundle compose, references GHCR by version
docker-compose.override.yml  local-dev overlay (build from siblings)
scripts/
  render_env.py   versions.yml -> .env
tests/
  conftest.py     testcontainers + render
  test_happy_path.py
  test_payment_declined.py
  test_gateway_recovery.py
  test_exactly_once.py
versions.yml      the manifest (source of truth for the bundle)
```

## Article series

1. [The Illusion of Microservices Independence](https://www.linkedin.com/pulse/illusion-microservices-independence-alberto-casado-martin-9kwue/)
2. [Is Going Back to Monoliths Really the Solution?](https://www.linkedin.com/pulse/going-back-monoliths-really-solution-alberto-casado-martin-qlqxe/)
3. [The Forgotten Transition: From Analysis to Design, in a Field That Stopped Asking](https://www.linkedin.com/pulse/forgotten-transition-from-analysis-design-field-alberto-casado-martin-fiwle/)
4. [The Illusion of Method: How Domain-Driven Design Hides the Question It Claims to Answer](https://www.linkedin.com/pulse/illusion-method-how-domain-driven-design-hides-claims-casado-martin-sk5ee/)
5. [Place Order: Anatomy of a Bad Cut](https://www.linkedin.com/pulse/place-order-anatomy-bad-cut-alberto-casado-martin-9wz8e/)
6. [Place Order: A Cut That Holds](https://www.linkedin.com/pulse/place-order-cut-holds-alberto-casado-martin-escve/)
