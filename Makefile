# place-order-integration -- master stack task runner.
#
# Two execution modes:
#
# - Local development (default): the docker-compose.override.yml replaces
#   the GHCR refs for Sales (sales-*:latest) and inherits the GHCR refs
#   for satellites (ghcr.io/${OWNER}/<svc>:${VERSION}). Every image MUST
#   exist locally before `up` -- nothing is built inline by compose.
#   `make images` produces the lot.
#
# - GHCR bundle validation (`make test-bundle`): compose runs with the
#   base file only, pulling each service from ghcr.io/${OWNER}/... at the
#   exact version pinned in versions.yml. Used by release.yml in CI.

.PHONY: help images sales-images compose-build up down test logs full \
        render bundle-up test-bundle clean-images prune-images

SALES_DIR ?= ../sales
OWNER ?= local

SATELLITES := catalog identity fake-payment-gateway fake-shipping-carrier \
              notifications analytics
SALES_IMAGES := sales-migrate sales-api sales-payment-handler \
                sales-outbox-relay sales-shipping-dispatcher

# Read versions.yml. Used to know which tag the satellite image must
# carry so the override's inherited ghcr.io ref finds it.
define _SAT_VERSION
$(shell uv run python -c "import yaml; print(yaml.safe_load(open('versions.yml'))['services']['$(1)'])")
endef

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z][a-zA-Z0-9_-]*:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Build the five Sales images via Pants.
sales-images: ## Build the five Sales images via Pants (sales-*:latest)
	$(MAKE) -C $(SALES_DIR) images

# Build the six satellite images by delegating to each repo's own Makefile,
# passing OWNER + VERSION so the resulting tag is exactly what the base
# compose expects (ghcr.io/${OWNER}/<svc>:${<SVC>_VERSION}).
compose-build: ## Build the six satellite images (delegates to each repo's `make image`)
	@for sat in $(SATELLITES); do \
	  ver=$$(uv run python -c "import yaml; print(yaml.safe_load(open('versions.yml'))['services']['$$sat'])"); \
	  echo ">>> building $$sat:$$ver"; \
	  $(MAKE) -C ../$$sat image OWNER=$(OWNER) VERSION=$$ver; \
	done

images: sales-images compose-build ## Build the full set of 11 images required by `up`

# Render .env from versions.yml. Required before any `docker compose`
# invocation because the compose file references ${OWNER} and per-service
# ${<SERVICE>_VERSION} env vars.
render: ## Render .env from versions.yml
	OWNER=$(OWNER) uv run python scripts/render_env.py > .env

up: render ## Bring the local stack up (-d). Requires images to be built first.
	docker compose up -d

down: ## Tear the local stack down (removes volumes)
	docker compose down -v --remove-orphans

logs: ## Tail compose logs (last 200 lines, follow)
	docker compose logs --tail=200 -f

test: render ## Run the E2E suite against the local stack
	uv run pytest -x

# Convenience: build everything, run the tests, tear down on the way out
# whether or not the suite passed.
full: ## images + render + pytest + down (idempotent end-to-end run)
	$(MAKE) images
	$(MAKE) render
	@trap '$(MAKE) down' EXIT; uv run pytest -x

# Run the suite against the actual GHCR bundle (base compose only,
# override skipped). OWNER must point at a real GHCR namespace where
# the versions pinned in versions.yml have been published.
bundle-up: render ## Pull the GHCR bundle and bring it up (override skipped)
	docker compose -f docker-compose.yml up -d --pull always

test-bundle: render ## Pull the GHCR bundle, run E2E, tear down (used by release.yml)
	docker compose -f docker-compose.yml pull
	@trap 'docker compose -f docker-compose.yml down -v --remove-orphans' EXIT; \
	 docker compose -f docker-compose.yml up -d && \
	 uv run pytest -x

# --- Cleanup ----------------------------------------------------------------

clean-images: ## Remove all 11 bundle images from the local Docker daemon
	@for s in $(SALES_IMAGES); do \
	  docker rmi $$s:latest 2>/dev/null && echo "removed $$s:latest" || true; \
	done
	@for sat in $(SATELLITES); do \
	  ver=$$(uv run python -c "import yaml; print(yaml.safe_load(open('versions.yml'))['services']['$$sat'])"); \
	  tag="ghcr.io/$(OWNER)/$$sat:$$ver"; \
	  docker rmi "$$tag" 2>/dev/null && echo "removed $$tag" || true; \
	done

prune-images: ## Remove any dangling images orphaned by rebuilds
	@docker image prune -f >/dev/null 2>&1 || true
	@echo "dangling images pruned"
