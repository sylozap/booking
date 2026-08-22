#!/usr/bin/env bash
# Declares the event topics (D31) and verifies their configuration.
#
#   scripts/create_topics.sh apply    create missing topics, align configs
#   scripts/create_topics.sh verify   assert the broker matches this file
#
# Topics are per aggregate, not per event type (ADR-0011). Partition key is
# specialist_id, which orders events for one specialist while keeping different
# specialists parallel; changing the partition count redistributes those keys,
# so it is a design decision rather than a knob.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
COMPOSE=(docker compose --file "${REPO_ROOT}/deploy/local/docker-compose.yml")
KAFKA_TOPICS=(/opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092)
KAFKA_CONFIGS=(/opt/kafka/bin/kafka-configs.sh --bootstrap-server kafka:9092)

readonly PARTITIONS=3
readonly REPLICATION_FACTOR=1  # Single broker locally.
readonly RETENTION_30_DAYS=2592000000
readonly RETENTION_90_DAYS=7776000000

# name:cleanup.policy:retention.ms
# catalog.offerings is compacted (D32): rebuilding the offerings projection in
# booking must work after an arbitrary delay, so it cannot depend on retention.
# Retention is irrelevant for a compacted topic, hence the empty field.
TOPICS=(
    "identity.users:delete:${RETENTION_30_DAYS}"
    "catalog.organizations:delete:${RETENTION_30_DAYS}"
    "catalog.offerings:compact:"
    "booking.bookings:delete:${RETENTION_30_DAYS}"
    "payment.payments:delete:${RETENTION_30_DAYS}"
    # One dead letter topic per consuming service. A silently dropped event in
    # a system that moves money is not acceptable (EVENTS section 5), and the
    # longer retention buys time to notice and replay.
    "identity.dead-letter:delete:${RETENTION_90_DAYS}"
    "catalog.dead-letter:delete:${RETENTION_90_DAYS}"
    "booking.dead-letter:delete:${RETENTION_90_DAYS}"
    "payment.dead-letter:delete:${RETENTION_90_DAYS}"
    "notification.dead-letter:delete:${RETENTION_90_DAYS}"
)

kafka() {
    "${COMPOSE[@]}" exec -T kafka "$@"
}

topic_config() {
    local topic="$1" key="$2"
    kafka "${KAFKA_CONFIGS[@]}" --entity-type topics --entity-name "${topic}" \
        --describe 2>/dev/null \
        | grep -oE "${key}=[^ ,]*" | head -1 | cut -d= -f2
}

topic_partitions() {
    local topic="$1"
    kafka "${KAFKA_TOPICS[@]}" --describe --topic "${topic}" 2>/dev/null \
        | grep -oE 'PartitionCount: [0-9]+' | grep -oE '[0-9]+'
}

apply() {
    local existing
    existing="$(kafka "${KAFKA_TOPICS[@]}" --list 2>/dev/null || true)"

    for spec in "${TOPICS[@]}"; do
        IFS=: read -r topic policy retention <<<"${spec}"

        local config_args=(--config "cleanup.policy=${policy}")
        [[ -n "${retention}" ]] && config_args+=(--config "retention.ms=${retention}")

        if grep -qxF "${topic}" <<<"${existing}"; then
            # Already there: realign the config rather than recreate the topic,
            # so re-running this script never discards data.
            local alter_args=("cleanup.policy=${policy}")
            [[ -n "${retention}" ]] && alter_args+=("retention.ms=${retention}")
            kafka "${KAFKA_CONFIGS[@]}" --entity-type topics --entity-name "${topic}" \
                --alter --add-config "$(IFS=,; echo "${alter_args[*]}")" >/dev/null
            printf '  updated  %s\n' "${topic}"
        else
            kafka "${KAFKA_TOPICS[@]}" --create --topic "${topic}" \
                --partitions "${PARTITIONS}" \
                --replication-factor "${REPLICATION_FACTOR}" \
                "${config_args[@]}" >/dev/null
            printf '  created  %s\n' "${topic}"
        fi
    done
}

verify() {
    local failures=0
    local existing
    existing="$(kafka "${KAFKA_TOPICS[@]}" --list 2>/dev/null || true)"

    for spec in "${TOPICS[@]}"; do
        IFS=: read -r topic policy retention <<<"${spec}"

        if ! grep -qxF "${topic}" <<<"${existing}"; then
            printf '  \033[31mFAIL\033[0m  %s is missing\n' "${topic}"
            failures=$((failures + 1))
            continue
        fi

        local problems=()

        local actual_partitions
        actual_partitions="$(topic_partitions "${topic}")"
        [[ "${actual_partitions}" == "${PARTITIONS}" ]] \
            || problems+=("partitions=${actual_partitions:-?} want ${PARTITIONS}")

        local actual_policy
        actual_policy="$(topic_config "${topic}" cleanup.policy)"
        [[ "${actual_policy}" == "${policy}" ]] \
            || problems+=("cleanup.policy=${actual_policy:-?} want ${policy}")

        if [[ -n "${retention}" ]]; then
            local actual_retention
            actual_retention="$(topic_config "${topic}" retention.ms)"
            [[ "${actual_retention}" == "${retention}" ]] \
                || problems+=("retention.ms=${actual_retention:-?} want ${retention}")
        fi

        if (( ${#problems[@]} > 0 )); then
            printf '  \033[31mFAIL\033[0m  %s: %s\n' "${topic}" "$(IFS='; '; echo "${problems[*]}")"
            failures=$((failures + 1))
        else
            printf '  \033[32mok\033[0m    %-24s partitions=%s cleanup.policy=%s\n' \
                "${topic}" "${actual_partitions}" "${actual_policy}"
        fi
    done

    echo
    if (( failures > 0 )); then
        echo "topics: ${failures} topic(s) do not match the declaration"
        return 1
    fi
    echo "topics: all ${#TOPICS[@]} topics match the declaration"
}

case "${1:-apply}" in
    apply)  apply ;;
    verify) verify ;;
    *)
        echo "usage: $(basename "$0") [apply|verify]" >&2
        exit 2
        ;;
esac
