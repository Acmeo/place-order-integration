# Build/run targets for the place-order-integration master stack.
#
# Two execution modes:
#
# - Local development (default): the docker-compose.override.yml shadows
#   the GHCR image refs and builds satellites from `../<repo>`. Sales is
#   still consumed as local Pants tags (sales-*:latest), so `make images`
#   must run first.
#
# - GHCR bundle validation (`make test-bundle`): compose runs with the
#   base file only, pulling each service from ghcr.io/${OWNER}/... at the
#   exact version pinned in versions.yml. Used by release.yml in CI.

.PHONY: images sales-images compose-build up down test logs full \
        render bundle-up test-bundle

SALES_DIR ?= ../sales
OWNER ?= local

# Build the five Sales images via Pants in the Sales repo.
sales-images:
	cd $(SALES_DIR) && pants package src/sales/processes::

# Build the satellite images declared by the override's `build:` blocks.
compose-build:
	OWNER=$(OWNER) docker compose build

# `make images` is the prerequisite for local-mode `make up` / `make test`.
images: sales-images compose-build

# Render .env from versions.yml. Required before any `docker compose`
# invocation because the compose file references ${OWNER} and
# per-service ${<SERVICE>_VERSION} env vars.
render:
	OWNER=$(OWNER) uv run python scripts/render_env.py > .env

up: render
	docker compose up -d

down:
	docker compose down -v --remove-orphans

logs:
	docker compose logs --tail=200

test: render
	uv run pytest -x

# Convenience: build everything, run the tests, tear down on the way out
# whether or not the suite passed.
full:
	$(MAKE) images
	$(MAKE) render
	@trap '$(MAKE) down' EXIT; uv run pytest -x

# Run the suite against the actual GHCR bundle (base compose only,
# override skipped). OWNER must be set to the GHCR namespace; the
# versions.yml at HEAD decides which image tags to pull.
bundle-up: render
	docker compose -f docker-compose.yml up -d --pull always

test-bundle: render
	docker compose -f docker-compose.yml pull
	@trap 'docker compose -f docker-compose.yml down -v --remove-orphans' EXIT; \
	 docker compose -f docker-compose.yml up -d && \
	 uv run pytest -x
