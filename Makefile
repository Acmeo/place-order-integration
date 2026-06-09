# place-order-integration -- master stack task runner.
#
# This repo is self-contained: nothing here references sibling repos
# (`../catalog`, `../sales`, ...). The default execution pulls every
# image from ghcr.io/<owner>/... at the version pinned in versions.yml.
#
# If you are iterating on one of the services locally and want the
# integration tests to use your in-progress build, copy
# `docker-compose.override.yml.example` to `docker-compose.override.yml`
# (gitignored) and uncomment the services you want overridden. Compose
# will pick the override up automatically.

.PHONY: help render up down test logs pull clean-images prune-images \
        bundle-up test-bundle full

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z][a-zA-Z0-9_-]*:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Render .env from versions.yml. Required before any `docker compose`
# invocation because the compose file references ${OWNER} and per-service
# ${<SERVICE>_VERSION} env vars. OWNER comes from versions.yml's `owner:`
# field by default; an OWNER env var, if set, wins (CI uses
# github.repository_owner).
render: ## Render .env from versions.yml
	uv run python scripts/render_env.py > .env

pull: render ## Pull every bundle image from GHCR
	docker compose pull

up: render ## Bring the stack up (-d). Pulls any missing image from GHCR.
	docker compose up -d

down: ## Tear the stack down (removes volumes)
	docker compose down -v --remove-orphans

logs: ## Tail compose logs (last 200 lines, follow)
	docker compose logs --tail=200 -f

test: render ## Render env + run the E2E suite. Stack lifecycle handled by the test harness.
	uv run pytest -x

full: ## render + pytest + down (idempotent end-to-end run)
	$(MAKE) render
	@trap '$(MAKE) down' EXIT; uv run pytest -x

# Run the suite against the GHCR bundle WITHOUT the local override.
# Same as `make test` when no override file is present; if you have an
# override.yml, this skips it so you exercise the published bundle.
bundle-up: render ## Pull the GHCR bundle and bring it up (override skipped)
	docker compose -f docker-compose.yml up -d --pull always

test-bundle: render ## Pull the GHCR bundle, run E2E, tear down (used by release.yml)
	docker compose -f docker-compose.yml pull
	@trap 'docker compose -f docker-compose.yml down -v --remove-orphans' EXIT; \
	 docker compose -f docker-compose.yml up -d && \
	 uv run pytest -x

# --- Cleanup ----------------------------------------------------------------

# Compute the GHCR-namespaced tag for every service in versions.yml and
# remove any local copy. Useful to force a fresh pull on the next `up`.
clean-images: ## Remove every bundle image's local copy
	@uv run python scripts/list_image_tags.py | while read tag; do \
	    docker rmi "$$tag" 2>/dev/null && echo "removed $$tag" || true; \
	  done

prune-images: ## Remove any dangling images orphaned by rebuilds
	@docker image prune -f >/dev/null 2>&1 || true
	@echo "dangling images pruned"
