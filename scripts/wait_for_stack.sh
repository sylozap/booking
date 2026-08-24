#!/usr/bin/env bash
# Waits until the local stack is usable, then prints where to reach it.
#
#   scripts/wait_for_stack.sh          wait up to WAIT_TIMEOUT seconds
#   WAIT_TIMEOUT=30 scripts/wait_for_stack.sh
#
# `docker compose up --wait` only proves each container passes its own
# healthcheck. That is a weaker statement than "the environment is ready": a
# broker with no topics, or a Grafana whose provisioning failed, is healthy and
# useless. Every probe below asserts something a service will actually depend
# on, so a broken environment fails here instead of inside the first test that
# touches it.
#
# Host addresses come from `docker compose port`, not from hardcoded numbers,
# so a port override such as POSTGRES_HOST_PORT=15432 is picked up for free.
#
# Requires only docker and curl on the host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
COMPOSE=(docker compose --file "${REPO_ROOT}/deploy/local/docker-compose.yml")
KAFKA_TOPICS=(/opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092)

readonly TIMEOUT="${WAIT_TIMEOUT:-180}"
readonly BACKUP_BUCKET=postgres-backups
readonly DATABASES=(identity catalog booking payment notification)
readonly GRAFANA_DATASOURCES=(prometheus loki tempo)

failures=0

report() {
    local outcome="$1" name="$2" detail="$3"
    if [[ "${outcome}" == "ok" ]]; then
        printf '  \033[32mok\033[0m    %-16s %s\n' "${name}" "${detail}"
    else
        printf '  \033[31mFAIL\033[0m  %-16s %s\n' "${name}" "${detail}"
        failures=$((failures + 1))
    fi
}

# --- helpers ---------------------------------------------------------------

compose_exec() {
    local service="$1"
    shift
    "${COMPOSE[@]}" exec -T "${service}" "$@"
}

# Published address of a container port, as something connectable: docker
# reports a wildcard binding as 0.0.0.0 or [::], neither of which is an address
# a client can dial. Resolves to nothing for a container that is not running,
# which the probe for that component reports on its own line.
resolve() {
    local service="$1" port="$2" mapped
    mapped="$("${COMPOSE[@]}" port "${service}" "${port}" 2>/dev/null | head -1)"
    mapped="${mapped/#0.0.0.0:/127.0.0.1:}"
    printf '%s' "${mapped/#\[::\]:/127.0.0.1:}"
}

container_running() {
    local state
    state="$("${COMPOSE[@]}" ps --all --format '{{.State}}' "$1" 2>/dev/null | head -1)"
    [[ "${state}" == "running" ]]
}

http_ok() {
    local url="$1" code
    code="$(curl --silent --output /dev/null --max-time 5 --write-out '%{http_code}' "${url}" 2>/dev/null)" \
        || return 1
    [[ "${code}" == "200" ]]
}

tcp_open() {
    local host="${1%:*}" port="${1##*:}"
    timeout 2 bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null
}

# --- probes ----------------------------------------------------------------
#
# Each probe prints one line: a detail for the summary on success, the reason
# on failure.

check_postgres() {
    compose_exec postgres pg_isready --username=postgres --dbname=postgres >/dev/null 2>&1 \
        || { echo "not accepting connections"; return 1; }

    local missing wanted="'${DATABASES[0]}'"
    for db in "${DATABASES[@]:1}"; do
        wanted+=",'${db}'"
    done

    missing="$(compose_exec postgres psql --username=postgres --dbname=postgres \
        --no-psqlrc --quiet --tuples-only --no-align \
        --command="SELECT coalesce(string_agg(d, ', '), '') FROM unnest(ARRAY[${wanted}]) AS d
                   WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = d)" 2>/dev/null)" \
        || { echo "cannot list databases"; return 1; }

    [[ -z "${missing//[[:space:]]/}" ]] || { echo "missing databases: ${missing}"; return 1; }
    echo "${#DATABASES[@]} databases at ${PG_ENDPOINT}"
}

check_redis() {
    local pong
    pong="$(compose_exec redis redis-cli ping 2>/dev/null | tr -d '\r')" \
        || { echo "not answering"; return 1; }
    [[ "${pong}" == "PONG" ]] || { echo "ping returned '${pong:-nothing}'"; return 1; }
    echo "responding at ${REDIS_ENDPOINT}"
}

# An empty broker is ready by its own healthcheck and unusable by any consumer,
# so the declared topics are part of readiness (D31). Existence is what is
# checked here; that their partitioning and cleanup policy match the
# declaration is a stronger claim, and asking the broker about every topic's
# config costs a JVM start each — scripts/create_topics.sh verify does that.
check_kafka() {
    local existing missing=()
    existing="$(compose_exec kafka "${KAFKA_TOPICS[@]}" --list 2>/dev/null)" \
        || { echo "broker not answering"; return 1; }

    local topic
    while read -r topic; do
        grep -qxF "${topic}" <<<"${existing}" || missing+=("${topic}")
    done < <("${SCRIPT_DIR}/create_topics.sh" list)

    (( ${#missing[@]} == 0 )) \
        || { echo "${#missing[@]} topic(s) missing (${missing[0]}...); run make topics"; return 1; }
    echo "broker and topics at ${KAFKA_ENDPOINT}"
}

# The collector image is distroless, so the probe runs from the host against
# the two ports the services will actually push telemetry to.
check_otel_collector() {
    tcp_open "${OTLP_GRPC_ENDPOINT}" || { echo "OTLP gRPC ${OTLP_GRPC_ENDPOINT} is closed"; return 1; }
    tcp_open "${OTLP_HTTP_ENDPOINT}" || { echo "OTLP HTTP ${OTLP_HTTP_ENDPOINT} is closed"; return 1; }
    echo "OTLP grpc ${OTLP_GRPC_ENDPOINT}, http ${OTLP_HTTP_ENDPOINT}"
}

# Not just "Prometheus is up", but "Prometheus is scraping the collector":
# without that the metrics half of the pipeline is silently dead.
check_prometheus() {
    http_ok "http://${PROMETHEUS_ENDPOINT}/-/ready" || { echo "not ready"; return 1; }

    local scraped
    scraped="$(curl --silent --max-time 5 --get \
        --data-urlencode 'query=count(up{job=~"otel-collector.*"} == 1)' \
        "http://${PROMETHEUS_ENDPOINT}/api/v1/query" 2>/dev/null \
        | sed -nE 's/.*"value":\[[0-9.]+,"([0-9]+)"\].*/\1/p')"

    [[ "${scraped}" == "2" ]] \
        || { echo "scrapes ${scraped:-0} of 2 collector targets"; return 1; }
    echo "scraping the collector at http://${PROMETHEUS_ENDPOINT}"
}

check_loki() {
    http_ok "http://${LOKI_ENDPOINT}/ready" || { echo "not ready"; return 1; }
    echo "ready at http://${LOKI_ENDPOINT}"
}

check_tempo() {
    http_ok "http://${TEMPO_ENDPOINT}/ready" || { echo "not ready"; return 1; }
    echo "ready at http://${TEMPO_ENDPOINT}"
}

# Provisioning is checked by presence rather than by the datasource health API:
# Grafana does not implement that endpoint for the Tempo plugin.
check_grafana() {
    http_ok "http://${GRAFANA_ENDPOINT}/api/health" || { echo "API not answering"; return 1; }

    local missing=()
    for uid in "${GRAFANA_DATASOURCES[@]}"; do
        http_ok "http://${GRAFANA_ENDPOINT}/api/datasources/uid/${uid}" || missing+=("${uid}")
    done

    (( ${#missing[@]} == 0 )) \
        || { echo "datasources not provisioned: ${missing[*]}"; return 1; }
    echo "${#GRAFANA_DATASOURCES[@]} datasources at http://${GRAFANA_ENDPOINT}"
}

check_mailhog() {
    http_ok "http://${MAILHOG_UI_ENDPOINT}/api/v2/messages?limit=1" \
        || { echo "API not answering"; return 1; }
    echo "smtp ${MAILHOG_SMTP_ENDPOINT}, ui http://${MAILHOG_UI_ENDPOINT}"
}

# The bucket, not just the server: the WAL archive has nowhere to go without
# it, and minio-init creating it is the step most likely to have failed (D56).
check_minio() {
    http_ok "http://${MINIO_ENDPOINT}/minio/health/ready" || { echo "not ready"; return 1; }
    "${COMPOSE[@]}" exec -T \
        --env "MC_HOST_probe=http://minioadmin:minioadmin@localhost:9000" \
        minio mc stat "probe/${BACKUP_BUCKET}" >/dev/null 2>&1 \
        || { echo "bucket ${BACKUP_BUCKET} is missing; run make up"; return 1; }
    echo "bucket ${BACKUP_BUCKET} at http://${MINIO_ENDPOINT}"
}

# --- driver ----------------------------------------------------------------

# Every component gets at least one attempt, so a stack that runs out of budget
# still reports what exactly is not ready instead of only the first offender.
# The component name is the compose service name, which is what makes the
# "is it even running" check possible before probing it.
wait_for() {
    local name="$1" probe="$2" detail
    while true; do
        if ! container_running "${name}"; then
            detail="container is not running"
        elif detail="$("${probe}" 2>&1)"; then
            report ok "${name}" "${detail}"
            return 0
        fi
        if (( SECONDS >= DEADLINE )); then
            report fail "${name}" "${detail:-no detail}"
            return 1
        fi
        sleep 2
    done
}

if [[ -z "$("${COMPOSE[@]}" ps --status=running --services 2>/dev/null || true)" ]]; then
    echo "the local stack is not running; start it with: make up" >&2
    exit 1
fi

PG_ENDPOINT="$(resolve postgres 5432)"
REDIS_ENDPOINT="$(resolve redis 6379)"
KAFKA_ENDPOINT="$(resolve kafka 29092)"
OTLP_GRPC_ENDPOINT="$(resolve otel-collector 4317)"
OTLP_HTTP_ENDPOINT="$(resolve otel-collector 4318)"
PROMETHEUS_ENDPOINT="$(resolve prometheus 9090)"
LOKI_ENDPOINT="$(resolve loki 3100)"
TEMPO_ENDPOINT="$(resolve tempo 3200)"
GRAFANA_ENDPOINT="$(resolve grafana 3000)"
MAILHOG_SMTP_ENDPOINT="$(resolve mailhog 1025)"
MAILHOG_UI_ENDPOINT="$(resolve mailhog 8025)"
MINIO_ENDPOINT="$(resolve minio 9000)"
MINIO_CONSOLE_ENDPOINT="$(resolve minio 9001)"

readonly DEADLINE=$(( SECONDS + TIMEOUT ))

echo "Waiting for the local stack (up to ${TIMEOUT}s):"
wait_for postgres       check_postgres       || true
wait_for redis          check_redis          || true
wait_for kafka          check_kafka          || true
wait_for otel-collector check_otel_collector || true
wait_for prometheus     check_prometheus     || true
wait_for loki           check_loki           || true
wait_for tempo          check_tempo          || true
wait_for grafana        check_grafana        || true
wait_for mailhog        check_mailhog        || true
wait_for minio          check_minio          || true

echo
if (( failures > 0 )); then
    echo "environment: ${failures} component(s) not ready after ${TIMEOUT}s"
    echo "inspect with: docker compose --file deploy/local/docker-compose.yml logs"
    exit 1
fi

cat <<SUMMARY
environment ready.

  Grafana          http://${GRAFANA_ENDPOINT}
  Prometheus       http://${PROMETHEUS_ENDPOINT}
  Loki             http://${LOKI_ENDPOINT}
  Tempo            http://${TEMPO_ENDPOINT}
  Mailhog          http://${MAILHOG_UI_ENDPOINT}
  MinIO console    http://${MINIO_CONSOLE_ENDPOINT}

  OTLP             grpc://${OTLP_GRPC_ENDPOINT}  http://${OTLP_HTTP_ENDPOINT}
  PostgreSQL       postgresql://<service>@${PG_ENDPOINT}/<service>
  Redis            redis://${REDIS_ENDPOINT}
  Kafka            ${KAFKA_ENDPOINT}
  SMTP             ${MAILHOG_SMTP_ENDPOINT}
SUMMARY
