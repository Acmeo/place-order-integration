# Build/run targets for the place-order-integration master stack.
#
# Order matters: Pants must produce the five sales-*:latest images
# before docker compose tries to start anything, because the compose
# references them by tag and does NOT rebuild Sales here.

.PHONY: images sales-images compose-build up down test logs

SALES_DIR ?= ../sales

# Build the five Sales images via Pants in the Sales repo. Independent
# of the satellites (which are built by `compose-build`).
sales-images:
	cd $(SALES_DIR) && pants package src/sales/processes::

# Build the satellite images declared by `build:` blocks in the compose.
compose-build:
	docker compose build

# `make images` is the prerequisite for `make up`/`make test`.
images: sales-images compose-build

up:
	docker compose up -d

down:
	docker compose down -v --remove-orphans

logs:
	docker compose logs --tail=200

test:
	uv run pytest -x

# Convenience: build everything, run the tests, tear down on the way out
# whether or not the suite passed. `set -e` exits early on the first
# error from `images`; the trap handles teardown.
full:
	$(MAKE) images
	@trap '$(MAKE) down' EXIT; uv run pytest -x
