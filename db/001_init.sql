-- =====================================================================
-- Roomly — 001_init.sql
-- Run as roomly_owner, connected to database "roomly":
--
--   psql "$MIGRATION_DATABASE_URL" -f db/001_init.sql
--
-- Design summary
--   * Multi-tenant: every tenant-owned row carries org_id.
--   * Tenant isolation is enforced by Postgres Row-Level Security (RLS),
--     keyed on a per-transaction setting:  app.org_id
--   * Double-booking is impossible: an EXCLUDE constraint rejects any two
--     active bookings for the same room whose time ranges overlap.
--   * The web app connects as roomly_app (not the owner), so RLS always
--     applies to it. Owner-only work goes through a few narrow
--     SECURITY DEFINER functions.
-- =====================================================================

\set ON_ERROR_STOP on
BEGIN;

SET LOCAL search_path = roomly, public;

-- ---------------------------------------------------------------------
-- Migration bookkeeping
-- ---------------------------------------------------------------------
CREATE TABLE schema_migrations (
  version    text        PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON schema_migrations TO roomly_app;   -- /health reports version

-- ---------------------------------------------------------------------
-- Tenant context
-- Returns NULL when app.org_id is unset, so every RLS policy matches no rows:
-- forgetting to set the tenant fails closed (empty results / rejected writes).
-- ---------------------------------------------------------------------
CREATE FUNCTION app_current_org() RETURNS bigint
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT nullif(current_setting('app.org_id', true), '')::bigint
$$;

-- ---------------------------------------------------------------------
-- organizations  (the "tenants")
-- ---------------------------------------------------------------------
CREATE TABLE organizations (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug          text        NOT NULL UNIQUE
                CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,39}$'),
  name          text        NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  -- IANA zone, e.g. 'America/Los_Angeles'. Invalid names raise an error.
  timezone      text        NOT NULL DEFAULT 'America/Los_Angeles'
                CHECK ((timestamptz '2000-01-01 00:00Z' AT TIME ZONE timezone) IS NOT NULL),
  workday_start time        NOT NULL DEFAULT '08:00',
  workday_end   time        NOT NULL DEFAULT '18:00',
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT workday_valid CHECK (workday_start < workday_end)
);

-- ---------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------
CREATE TABLE users (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id        bigint      NOT NULL DEFAULT app_current_org()
                REFERENCES organizations(id),
  email         text        NOT NULL CHECK (email = lower(email) AND email LIKE '%_@_%'),
  name          text        NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  role          text        NOT NULL DEFAULT 'employee'
                CHECK (role IN ('employee', 'admin')),
  password_hash text        NOT NULL,          -- argon2id, never plaintext
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, email),
  UNIQUE (org_id, id)                          -- target for composite FKs
);

-- ---------------------------------------------------------------------
-- rooms
-- ---------------------------------------------------------------------
CREATE TABLE rooms (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id     bigint      NOT NULL DEFAULT app_current_org()
             REFERENCES organizations(id),
  name       text        NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  capacity   integer     NOT NULL CHECK (capacity BETWEEN 1 AND 500),
  features   text[]      NOT NULL DEFAULT '{}',   -- e.g. {projector,whiteboard,video}
  is_active  boolean     NOT NULL DEFAULT true,   -- soft-delete; keeps history intact
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name),
  UNIQUE (org_id, id)
);

-- ---------------------------------------------------------------------
-- bookings
-- ---------------------------------------------------------------------
CREATE TABLE bookings (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id        bigint      NOT NULL DEFAULT app_current_org(),
  room_id       bigint      NOT NULL,
  user_id       bigint      NOT NULL,
  title         text        NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
  -- Half-open range [start, end): a 9:00-9:30 booking and a 9:30-10:00
  -- booking touch but do not overlap.
  during        tstzrange   NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  checked_in_at timestamptz,
  cancelled_at  timestamptz,
  cancel_reason text CHECK (cancel_reason IN ('user', 'admin', 'no_show')),

  -- Composite FKs: a booking can only reference a room / user from the SAME
  -- org. Cross-tenant references are impossible even if app code is wrong.
  FOREIGN KEY (org_id, room_id) REFERENCES rooms (org_id, id),
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id),

  CONSTRAINT range_well_formed CHECK (
    NOT isempty(during)
    AND lower(during) IS NOT NULL AND upper(during) IS NOT NULL
    AND lower_inc(during) AND NOT upper_inc(during)
  ),
  CONSTRAINT on_half_hour CHECK (
    extract(epoch FROM lower(during))::bigint % 1800 = 0
    AND extract(epoch FROM upper(during))::bigint % 1800 = 0
  ),
  CONSTRAINT max_four_hours CHECK (upper(during) - lower(during) <= interval '4 hours'),
  CONSTRAINT cancel_fields_paired CHECK ((cancelled_at IS NULL) = (cancel_reason IS NULL)),

  -- THE core guarantee: no two active bookings for one room may overlap.
  -- Violations raise SQLSTATE 23P01 (exclusion_violation) -> app returns 409.
  CONSTRAINT no_double_booking
    EXCLUDE USING gist (room_id WITH =, during WITH &&)
    WHERE (cancelled_at IS NULL)
);

CREATE INDEX bookings_user_start_idx ON bookings (org_id, user_id, lower(during));
CREATE INDEX bookings_org_during_idx ON bookings USING gist (org_id, during)
  WHERE cancelled_at IS NULL;                    -- day-grid query

-- ---------------------------------------------------------------------
-- Booking validation that needs other tables (org hours, room status).
-- Runs as the invoking role, so it sees only the current tenant's rows.
-- Raises SQLSTATE 23514 (check_violation) -> app returns 422.
-- ---------------------------------------------------------------------
CREATE FUNCTION validate_booking() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  org         organizations%ROWTYPE;
  room_active boolean;
  local_start timestamp;
  local_end   timestamp;
BEGIN
  -- Only re-validate when the slot/room changes (not on cancel / check-in).
  IF TG_OP = 'UPDATE'
     AND NEW.during = OLD.during AND NEW.room_id = OLD.room_id THEN
    RETURN NEW;
  END IF;

  SELECT * INTO org FROM organizations WHERE id = NEW.org_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Unknown organization' USING ERRCODE = 'check_violation';
  END IF;

  SELECT is_active INTO room_active FROM rooms
   WHERE id = NEW.room_id AND org_id = NEW.org_id;
  IF NOT coalesce(room_active, false) THEN
    RAISE EXCEPTION 'Room is not available for booking'
      USING ERRCODE = 'check_violation';
  END IF;

  local_start := lower(NEW.during) AT TIME ZONE org.timezone;
  local_end   := upper(NEW.during) AT TIME ZONE org.timezone;

  IF local_start::date <> local_end::date
     OR local_start::time < org.workday_start
     OR local_end::time   > org.workday_end THEN
    RAISE EXCEPTION 'Bookings must be within working hours (% - %)',
      to_char(org.workday_start, 'HH24:MI'), to_char(org.workday_end, 'HH24:MI')
      USING ERRCODE = 'check_violation';
  END IF;

  IF upper(NEW.during) <= now() THEN
    RAISE EXCEPTION 'Cannot book a time slot that has already ended'
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END
$$;

CREATE TRIGGER bookings_validate
  BEFORE INSERT OR UPDATE ON bookings
  FOR EACH ROW EXECUTE FUNCTION validate_booking();

-- ---------------------------------------------------------------------
-- Row-Level Security
-- ENABLE (not FORCE): policies apply to every non-owner role, i.e. the app.
-- The owner bypasses RLS, which is why the app must never connect as owner;
-- the app verifies this at startup (see SPEC.md, "RLS self-check").
-- ---------------------------------------------------------------------
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users         ENABLE ROW LEVEL SECURITY;
ALTER TABLE rooms         ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings      ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_self ON organizations
  FOR SELECT TO roomly_app
  USING (id = app_current_org());

CREATE POLICY tenant_isolation ON users TO roomly_app
  USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org());

CREATE POLICY tenant_isolation ON rooms TO roomly_app
  USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org());

CREATE POLICY tenant_isolation ON bookings TO roomly_app
  USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org());

-- ---------------------------------------------------------------------
-- Privileges for the app role (least privilege: no DELETE anywhere,
-- read-only organizations; bookings are cancelled, never deleted).
-- ---------------------------------------------------------------------
GRANT SELECT                 ON organizations         TO roomly_app;
GRANT SELECT, INSERT, UPDATE ON users, rooms, bookings TO roomly_app;

-- ---------------------------------------------------------------------
-- Narrow SECURITY DEFINER functions: the ONLY ways the app can look
-- across tenants. Each returns minimal data. search_path is pinned to
-- prevent search-path hijacking.
-- ---------------------------------------------------------------------

-- Resolve /o/{slug} to an org BEFORE a tenant context exists (login page).
CREATE FUNCTION org_by_slug(p_slug text)
RETURNS TABLE (id bigint, slug text, name text, timezone text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = roomly, pg_temp AS $$
  SELECT o.id, o.slug, o.name, o.timezone FROM organizations o WHERE o.slug = p_slug
$$;

-- Aggregate numbers for /health. No tenant data leaves this function.
CREATE FUNCTION health_stats()
RETURNS TABLE (organizations bigint, bookings_today bigint, active_bookings_now bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = roomly, pg_temp AS $$
  SELECT
    (SELECT count(*) FROM organizations),
    (SELECT count(*) FROM bookings
      WHERE cancelled_at IS NULL
        AND during && tstzrange(date_trunc('day', now()), date_trunc('day', now()) + interval '1 day')),
    (SELECT count(*) FROM bookings
      WHERE cancelled_at IS NULL AND during @> now())
$$;

-- Stretch goal: free rooms whose booker never checked in.
-- Called every 5 minutes by the background job; returns rows released.
CREATE FUNCTION release_no_shows(p_grace interval DEFAULT interval '10 minutes')
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = roomly, pg_temp AS $$
DECLARE n integer;
BEGIN
  UPDATE bookings
     SET cancelled_at = now(), cancel_reason = 'no_show'
   WHERE cancelled_at IS NULL
     AND checked_in_at IS NULL
     AND lower(during) + p_grace <= now()
     AND upper(during) > now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;

REVOKE ALL ON FUNCTION org_by_slug(text), health_stats(), release_no_shows(interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION org_by_slug(text), health_stats(), release_no_shows(interval)
  TO roomly_app;

-- ---------------------------------------------------------------------
-- Security self-check, called by the app at startup and by /health.
-- Returns true only if the CURRENT role is subject to RLS:
-- not superuser, no BYPASSRLS, and owns none of the tenant tables.
-- ---------------------------------------------------------------------
CREATE FUNCTION rls_enforced_for_current_user() RETURNS boolean
LANGUAGE sql STABLE SET search_path = roomly, pg_temp AS $$
  SELECT NOT r.rolsuper
     AND NOT r.rolbypassrls
     AND NOT EXISTS (
       SELECT 1 FROM pg_tables t
        WHERE t.schemaname = 'roomly'
          AND t.tablename IN ('organizations', 'users', 'rooms', 'bookings')
          AND t.tableowner = current_user)
  FROM pg_roles r WHERE r.rolname = current_user
$$;
GRANT EXECUTE ON FUNCTION rls_enforced_for_current_user() TO roomly_app;

INSERT INTO schema_migrations (version) VALUES ('001_init');

COMMIT;
