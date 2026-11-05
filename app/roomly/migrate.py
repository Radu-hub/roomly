"""Apply db/NNN_*.sql in order with psql: python -m roomly.migrate

Runs as roomly_owner. Skips 000_bootstrap.sql, which needs the master user and runs once
per environment. Each migration inserts its own row into schema_migrations.
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from roomly.config import owner_conninfo

DB_DIR = Path(os.environ.get("DB_DIR", Path(__file__).resolve().parents[2] / "db"))

# How psql's environment variables map to connection parameters. Passing them in the
# environment keeps the password out of the process list.
PSQL_ENV = {
    "host": "PGHOST",
    "port": "PGPORT",
    "dbname": "PGDATABASE",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
}


def applied_versions(conninfo: str) -> set[str]:
    with psycopg.connect(conninfo) as conn:
        exists = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0]
        if exists is None:
            return set()
        return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def pending_migrations(applied: set[str]) -> list[Path]:
    files = sorted(DB_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    return [f for f in files if not f.name.startswith("000_") and f.stem not in applied]


def main() -> int:
    conninfo = owner_conninfo()
    params = conninfo_to_dict(conninfo)
    env = os.environ | {PSQL_ENV[k]: str(v) for k, v in params.items() if k in PSQL_ENV}

    pending = pending_migrations(applied_versions(conninfo))
    if not pending:
        print("No pending migrations.")
    for path in pending:
        print(f"Applying {path.name}")
        result = subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(path)], env=env)
        if result.returncode != 0:
            print(f"Migration {path.name} failed", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
