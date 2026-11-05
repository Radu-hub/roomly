#!/usr/bin/env bash
# One-time database setup on AWS. Run on the instance through SSM Session Manager:
#
#   sudo /opt/roomly/bootstrap-db.sh <image_tag>
#
# Creates the roles and database as the RDS master user, then migrates, seeds and deploys.
# Passwords come from SSM and travel only through environment variables, so they never
# reach a file, the process list or this script's log. Safe to re-run: if the database
# already exists it skips straight to the migrate step.
set -euo pipefail

TAG="${1:?Usage: bootstrap-db.sh <image_tag>}"

# shellcheck source=/dev/null
source /opt/roomly/env
IMAGE="$IMAGE_REPO:$TAG"
PREFIX="/roomly/prod"

param() {
  aws ssm get-parameter --region "$AWS_REGION" --name "$PREFIX/$1" --with-decryption \
    --query Parameter.Value --output text
}

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "${IMAGE_REPO%%/*}"
docker pull "$IMAGE"

export PGHOST PGPASSWORD OWNER_PW APP_PW
PGHOST="$(param db/host)"
PGPASSWORD="$(param db/master_password)"
OWNER_PW="$(param db/owner_password)"
APP_PW="$(param db/app_password)"

in_image() {
  docker run --rm -i \
    -e PGHOST -e PGPASSWORD -e OWNER_PW -e APP_PW \
    -e PGUSER=roomly_master -e PGDATABASE=postgres -e PGSSLMODE=verify-full \
    -e PGSSLROOTCERT=/app/certs/rds-global-bundle.pem \
    "$IMAGE" "$@"
}

if [ "$(in_image psql -Atc "SELECT 1 FROM pg_database WHERE datname = 'roomly'")" = "1" ]; then
  echo "Database already bootstrapped, skipping 000_bootstrap.sql"
else
  echo "Running 000_bootstrap.sql"
  # Single quotes on purpose: the container's shell expands these, so the passwords never
  # appear in a command line on this host.
  # shellcheck disable=SC2016
  in_image sh -c 'exec psql -v ON_ERROR_STOP=1 -v owner_password="$OWNER_PW" -v app_password="$APP_PW" -f /app/db/000_bootstrap.sql'
fi
unset PGPASSWORD OWNER_PW APP_PW

app_env=(-e SSM_PREFIX="$PREFIX" -e AWS_REGION="$AWS_REGION" -e APP_ENV=prod)
echo "Running migrations"
docker run --rm "${app_env[@]}" "$IMAGE" python -m roomly.migrate
echo "Seeding demo data"
docker run --rm "${app_env[@]}" "$IMAGE" python -m roomly.seed

echo "Deploying $TAG"
/opt/roomly/deploy.sh "$TAG"
