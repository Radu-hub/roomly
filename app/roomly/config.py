"""Settings from the environment, plus SSM Parameter Store when running in AWS."""

import os
from dataclasses import dataclass

from psycopg.conninfo import make_conninfo

RDS_CA_BUNDLE = "/app/certs/rds-global-bundle.pem"


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    session_secret: str
    demo_mode: bool
    no_show_release: bool
    app_version: str
    git_sha: str
    build_time: str

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


def _fetch_ssm_parameters(prefix: str, names: list[str]) -> dict[str, str]:
    """Read named parameters using the instance role, and return them keyed by name.

    Named, not by path: the web process has no business holding the owner or master
    passwords, even briefly.
    """
    import boto3  # imported here so local runs and tests never touch AWS

    # boto3 reads AWS_DEFAULT_REGION but not AWS_REGION, so the region is passed explicitly.
    client = boto3.client("ssm", region_name=os.environ["AWS_REGION"])
    response = client.get_parameters(
        Names=[f"{prefix}/{name}" for name in names], WithDecryption=True
    )
    if response["InvalidParameters"]:
        raise RuntimeError(f"Missing SSM parameters: {response['InvalidParameters']}")
    return {p["Name"].removeprefix(f"{prefix}/"): p["Value"] for p in response["Parameters"]}


def _rds_conninfo(host: str, user: str, password: str) -> str:
    # verify-full checks both the certificate chain and the hostname, so a machine
    # impersonating the database can't collect the password.
    return make_conninfo(
        host=host,
        port=5432,
        dbname="roomly",
        user=user,
        password=password,
        sslmode="verify-full",
        sslrootcert=RDS_CA_BUNDLE,
    )


def load_settings() -> Settings:
    prefix = os.environ.get("SSM_PREFIX")
    if prefix:
        params = _fetch_ssm_parameters(prefix, ["db/host", "db/app_password", "session_secret"])
        database_url = _rds_conninfo(params["db/host"], "roomly_app", params["db/app_password"])
        session_secret = params["session_secret"]
    else:
        database_url = os.environ["DATABASE_URL"]
        session_secret = os.environ["SESSION_SECRET"]

    return Settings(
        app_env=os.environ.get("APP_ENV", "local"),
        database_url=database_url,
        session_secret=session_secret,
        demo_mode=os.environ.get("DEMO_MODE", "false").lower() == "true",
        no_show_release=os.environ.get("NO_SHOW_RELEASE", "false").lower() == "true",
        app_version=os.environ.get("APP_VERSION", "dev"),
        git_sha=os.environ.get("GIT_SHA", "dev"),
        build_time=os.environ.get("BUILD_TIME", "dev"),
    )


def owner_conninfo() -> str:
    """Connection string for roomly_owner. Used by migrate, seed and the worker's reset only."""
    prefix = os.environ.get("SSM_PREFIX")
    if prefix:
        params = _fetch_ssm_parameters(prefix, ["db/host", "db/owner_password"])
        return _rds_conninfo(params["db/host"], "roomly_owner", params["db/owner_password"])
    return os.environ["MIGRATION_DATABASE_URL"]


def demo_password() -> str:
    prefix = os.environ.get("SSM_PREFIX")
    if prefix:
        return _fetch_ssm_parameters(prefix, ["demo_password"])["demo_password"]
    return os.environ["DEMO_PASSWORD"]
