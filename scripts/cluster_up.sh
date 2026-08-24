#!/usr/bin/env bash
# Creates the local cluster and brings it to the point where an application
# could be deployed into it: Ingress controller running, namespaces present.
#
#   scripts/cluster_up.sh
#
# Idempotent: re-running it on an existing cluster reconciles the ingress
# controller and the namespaces instead of failing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"

readonly CLUSTER_NAME=booking
readonly KUBE_CONTEXT="kind-${CLUSTER_NAME}"
# Pinned with the same reasoning as the node image: the controller is part of
# the environment, not a moving dependency of it.
readonly INGRESS_NGINX_VERSION=controller-v1.15.1
readonly INGRESS_NGINX_MANIFEST="https://raw.githubusercontent.com/kubernetes/ingress-nginx/${INGRESS_NGINX_VERSION}/deploy/static/provider/kind/deploy.yaml"

"${SCRIPT_DIR}/install_tools.sh" check kind kubectl >/dev/null

if kind get clusters 2>/dev/null | grep -qxF "${CLUSTER_NAME}"; then
    echo "==> cluster ${CLUSTER_NAME} already exists"
else
    echo "==> creating cluster ${CLUSTER_NAME}"
    kind create cluster --config "${REPO_ROOT}/deploy/cluster/kind.yaml" --wait 120s
fi

echo
echo "==> ingress-nginx (${INGRESS_NGINX_VERSION})"
kubectl --context "${KUBE_CONTEXT}" apply --filename "${INGRESS_NGINX_MANIFEST}"

# The admission webhook rejects Ingress objects until its certificate job has
# run, so "controller pod is ready" is not yet "an Ingress can be created".
kubectl --context "${KUBE_CONTEXT}" wait --namespace ingress-nginx \
    --for=condition=Ready pod \
    --selector app.kubernetes.io/component=controller \
    --timeout 180s

echo
echo "==> namespaces"
kubectl --context "${KUBE_CONTEXT}" apply --filename "${REPO_ROOT}/deploy/cluster/namespaces.yaml"

echo
kubectl --context "${KUBE_CONTEXT}" get nodes
echo
kubectl --context "${KUBE_CONTEXT}" get namespaces dev prod
echo
echo "cluster ready. context: ${KUBE_CONTEXT}"
