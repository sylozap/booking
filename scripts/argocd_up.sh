#!/usr/bin/env bash
# Installs ArgoCD into the local cluster and applies the root Application.
#
#   scripts/argocd_up.sh
#   GIT_REPO_URL=https://github.com/owner/booking.git scripts/argocd_up.sh
#
# Bootstrap only: ArgoCD, the project it works in and the root Application are
# applied by hand, because a GitOps controller cannot deploy itself. Everything
# past that point comes from the repository (D10).
#
# Idempotent: safe to re-run on a cluster that already has ArgoCD.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

readonly CLUSTER_NAME=booking
readonly KUBE_CONTEXT="kind-${CLUSTER_NAME}"
readonly NAMESPACE=argocd
readonly AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-${HOME}/.config/sops/age/keys.txt}"

"${SCRIPT_DIR}/install_tools.sh" check kubectl kustomize >/dev/null

kubectl() { command kubectl --context "${KUBE_CONTEXT}" "$@"; }

if ! kind get clusters 2>/dev/null | grep -qxF "${CLUSTER_NAME}"; then
    echo "cluster ${CLUSTER_NAME} does not exist; run make cluster-up" >&2
    exit 1
fi

# ArgoCD renders manifests, so it is ArgoCD that needs the decryption key
# (D52). The Secret is created from the key file on this machine and is the
# one piece of the cluster that is deliberately not in the repository.
if [[ ! -f "${AGE_KEY_FILE}" ]]; then
    echo "age key not found at ${AGE_KEY_FILE}; run make secrets-key" >&2
    exit 1
fi

GIT_REPO_URL="${GIT_REPO_URL:-$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null || true)}"
if [[ -z "${GIT_REPO_URL}" ]]; then
    cat >&2 <<'MESSAGE'
no git remote to deploy from.

ArgoCD pulls manifests from a repository; this one has no origin. Create the
remote, or pass the URL explicitly:

  GIT_REPO_URL=https://github.com/owner/booking.git make argocd-up
MESSAGE
    exit 1
fi

echo "==> namespace ${NAMESPACE}"
kubectl create namespace "${NAMESPACE}" --dry-run=client --output=yaml | kubectl apply --filename -

echo
echo "==> age key"
kubectl --namespace "${NAMESPACE}" create secret generic sops-age \
    --from-file="keys.txt=${AGE_KEY_FILE}" \
    --dry-run=client --output=yaml | kubectl apply --filename -

echo
echo "==> argocd"
# Server-side apply: the ApplicationSet CRD is larger than the 256 KiB limit
# on the last-applied-configuration annotation that client-side apply writes.
kustomize build "${REPO_ROOT}/deploy/argocd/install" \
    | kubectl apply --server-side --force-conflicts --filename -

# The repo-server carries the KSOPS plugin and the key; if it cannot start,
# every future sync fails on a decryption error rather than on the real cause.
kubectl --namespace "${NAMESPACE}" rollout status deployment/argocd-repo-server --timeout 300s
kubectl --namespace "${NAMESPACE}" rollout status deployment/argocd-server --timeout 300s
kubectl --namespace "${NAMESPACE}" rollout status statefulset/argocd-application-controller --timeout 300s

echo
echo "==> project and root application (${GIT_REPO_URL})"
kubectl apply --filename "${REPO_ROOT}/deploy/argocd/project.yaml"
sed "s|\${GIT_REPO_URL}|${GIT_REPO_URL}|g" \
    "${REPO_ROOT}/deploy/argocd/root-app.yaml" \
    | kubectl apply --filename -

echo
echo "argocd ready."
echo "  ui:       make argocd-ui   (http://localhost:8080)"
echo "  password: make argocd-password"
