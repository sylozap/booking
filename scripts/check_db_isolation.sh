#!/usr/bin/env bash
# Asserts the tenant of every database is alone in it (D42, D19).
#
# Connections go through the container network address, not loopback: the image
# trusts loopback, so only the network path exercises scram-sha-256 as a real
# service would.
#
# For each service role: connecting to its own database must succeed, and
# connecting to any of the other four must be refused by PostgreSQL. This is
# the check that keeps database-per-service from silently degrading into a
# shared database with five schemas.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
COMPOSE=(docker compose --file "${REPO_ROOT}/deploy/local/docker-compose.yml")

SERVICES=(identity catalog booking payment notification)
# Extension is created only in the database whose schema needs it.
declare -A REQUIRED_EXTENSION=([identity]=citext [booking]=btree_gist)

failures=0

# Prints nothing on success; the caller decides how to report.
try_connect() {
    local role="$1" database="$2"
    "${COMPOSE[@]}" exec -T \
        --env "PGPASSWORD=${role}_local_dev" \
        postgres \
        psql --host=postgres --username="${role}" --dbname="${database}" \
             --no-psqlrc --quiet --tuples-only --command='SELECT 1' \
        >/dev/null 2>&1
}

report() {
    local outcome="$1" message="$2"
    if [[ "${outcome}" == "ok" ]]; then
        printf '  \033[32mok\033[0m    %s\n' "${message}"
    else
        printf '  \033[31mFAIL\033[0m  %s\n' "${message}"
        failures=$((failures + 1))
    fi
}

echo "Own database is reachable:"
for service in "${SERVICES[@]}"; do
    if try_connect "${service}" "${service}"; then
        report ok "${service} -> ${service}"
    else
        report fail "${service} -> ${service} (expected to connect)"
    fi
done

echo
echo "Foreign databases are refused:"
for service in "${SERVICES[@]}"; do
    for other in "${SERVICES[@]}"; do
        [[ "${service}" == "${other}" ]] && continue
        if try_connect "${service}" "${other}"; then
            report fail "${service} -> ${other} (connected, must be refused)"
        else
            report ok "${service} -> ${other} refused"
        fi
    done
done

echo
echo "Required extensions:"
for service in "${!REQUIRED_EXTENSION[@]}"; do
    extension="${REQUIRED_EXTENSION[${service}]}"
    installed="$("${COMPOSE[@]}" exec -T \
        --env "PGPASSWORD=${service}_local_dev" \
        postgres \
        psql --host=postgres --username="${service}" --dbname="${service}" \
             --no-psqlrc --quiet --tuples-only --no-align \
             --command="SELECT count(*) FROM pg_extension WHERE extname = '${extension}'" \
        2>/dev/null || echo 0)"
    if [[ "${installed}" == "1" ]]; then
        report ok "${service} has ${extension}"
    else
        report fail "${service} is missing ${extension}"
    fi
done

echo
if (( failures > 0 )); then
    echo "database isolation: ${failures} check(s) failed"
    exit 1
fi
echo "database isolation: all checks passed"
