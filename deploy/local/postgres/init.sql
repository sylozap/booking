-- Five databases and five roles in one PostgreSQL instance (D42).
--
-- Isolation is enforced by the database, not by convention: CONNECT is revoked
-- from PUBLIC on every database, so a service role can reach its own database
-- and nothing else. scripts/check_db_isolation.sh asserts this.
--
-- Passwords are local-only placeholders. The cluster gets its credentials from
-- SOPS-encrypted secrets (D52); this file is never applied outside compose.
--
-- Run once, by the entrypoint, as the postgres superuser.

-- identity ------------------------------------------------------------------

CREATE ROLE identity LOGIN PASSWORD 'identity_local_dev';
CREATE DATABASE identity OWNER identity;
REVOKE ALL ON DATABASE identity FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE identity TO identity;

\connect identity

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO identity;
-- Extensions are NOT created here. Migration 0001 installs the ones identity
-- needs, as the identity role, exactly as it does in the cluster. Creating one
-- here as the superuser makes the local database subtly different: the service
-- role does not own the extension and cannot drop it, so `alembic downgrade`
-- fails on a developer machine and nowhere else.

-- catalog -------------------------------------------------------------------

\connect postgres

CREATE ROLE catalog LOGIN PASSWORD 'catalog_local_dev';
CREATE DATABASE catalog OWNER catalog;
REVOKE ALL ON DATABASE catalog FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE catalog TO catalog;

\connect catalog

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO catalog;

-- booking -------------------------------------------------------------------

\connect postgres

CREATE ROLE booking LOGIN PASSWORD 'booking_local_dev';
CREATE DATABASE booking OWNER booking;
REVOKE ALL ON DATABASE booking FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE booking TO booking;

\connect booking

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO booking;
-- btree_gist, which the no-double-booking EXCLUDE constraint needs (D12), is
-- installed by that service's own first migration for the same reason.

-- payment -------------------------------------------------------------------

\connect postgres

CREATE ROLE payment LOGIN PASSWORD 'payment_local_dev';
CREATE DATABASE payment OWNER payment;
REVOKE ALL ON DATABASE payment FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE payment TO payment;

\connect payment

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO payment;

-- notification --------------------------------------------------------------

\connect postgres

CREATE ROLE notification LOGIN PASSWORD 'notification_local_dev';
CREATE DATABASE notification OWNER notification;
REVOKE ALL ON DATABASE notification FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE notification TO notification;

\connect notification

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO notification;
