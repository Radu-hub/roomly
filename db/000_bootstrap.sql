-- =====================================================================
-- Roomly — 000_bootstrap.sql
-- Run ONCE per environment, as the RDS master user (or local superuser),
-- connected to the default "postgres" database:
--
--   psql "$MASTER_DATABASE_URL" \
--        -v owner_password="$OWNER_PW" -v app_password="$APP_PW" \
--        -f db/000_bootstrap.sql
--
-- Passwords are passed in as psql variables so they never live in git.
-- In AWS they come from SSM Parameter Store (SecureString).
-- =====================================================================

\set ON_ERROR_STOP on

-- Owner role: owns every table, runs migrations and seed. Never used by the
-- running web app.
CREATE ROLE roomly_owner LOGIN PASSWORD :'owner_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- App role: what the web app connects as. NOT the table owner, so
-- Row-Level Security policies always apply to it. NOBYPASSRLS is explicit.
CREATE ROLE roomly_app LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- On RDS (PG16+) the master user must be a member of a role to create a
-- database owned by it.
GRANT roomly_owner TO CURRENT_USER;

CREATE DATABASE roomly OWNER roomly_owner;

\connect roomly

-- btree_gist lets the exclusion constraint combine "=" on room_id with
-- "&&" (overlaps) on the time range. Creating extensions needs rds_superuser.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Lock down the default schema; all app objects live in schema "roomly".
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO roomly_owner, roomly_app;  -- for btree_gist

CREATE SCHEMA roomly AUTHORIZATION roomly_owner;
GRANT USAGE ON SCHEMA roomly TO roomly_app;

ALTER ROLE roomly_owner IN DATABASE roomly SET search_path = roomly, public;
ALTER ROLE roomly_app   IN DATABASE roomly SET search_path = roomly, public;

-- Fail fast on runaway queries / forgotten transactions from the app.
ALTER ROLE roomly_app IN DATABASE roomly SET statement_timeout = '5s';
ALTER ROLE roomly_app IN DATABASE roomly SET idle_in_transaction_session_timeout = '30s';
