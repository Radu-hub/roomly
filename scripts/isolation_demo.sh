#!/usr/bin/env bash
# Cross-tenant demo (G2): an Acme user asks for a Globex booking by its id.
#
#   scripts/isolation_demo.sh <base_url> <globex_booking_id>
#
# Find an id by logging in as "Globex: employee" and opening any booking: it's in the URL.
set -euo pipefail

BASE_URL="${1:?Usage: isolation_demo.sh <base_url> <globex_booking_id>}"
BOOKING_ID="${2:?Usage: isolation_demo.sh <base_url> <globex_booking_id>}"

JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

csrf_from() { sed -n 's/.*name="csrf" value="\([^"]*\)".*/\1/p' | head -n 1; }

echo "1. Logging in as the Acme employee"
CSRF="$(curl -fsS -c "$JAR" -b "$JAR" "$BASE_URL/" | csrf_from)"
curl -fsS -o /dev/null -c "$JAR" -b "$JAR" \
  --data-urlencode "slug=acme" --data-urlencode "role=employee" --data-urlencode "csrf=$CSRF" \
  "$BASE_URL/demo-login"

echo "2. Requesting Globex booking $BOOKING_ID with Acme's session"
echo "   GET $BASE_URL/o/acme/bookings/$BOOKING_ID"
STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE_URL/o/acme/bookings/$BOOKING_ID")"
echo "3. HTTP status: $STATUS"
echo

if [ "$STATUS" = "404" ]; then
  echo "The route has no org check in Python; Row-Level Security made the row invisible."
else
  echo "UNEXPECTED: wanted 404. Is $BOOKING_ID really a Globex booking id?"
  exit 1
fi
