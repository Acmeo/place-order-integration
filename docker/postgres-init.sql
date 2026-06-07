-- Init script for the master integration Postgres instance.
--
-- The default POSTGRES_USER/POSTGRES_DB pair (postgres) is created by
-- the entrypoint. This script creates the five per-totality databases
-- the stack expects. Each service points at its own database so the
-- catalog/identity/notifications/analytics services do not share a
-- schema with Sales -- they are independent totalities.

CREATE DATABASE sales;
CREATE DATABASE catalog;
CREATE DATABASE identity;
CREATE DATABASE notifications;
CREATE DATABASE analytics;
