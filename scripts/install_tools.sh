#!/usr/bin/env bash
# Installs the cluster toolchain into ~/.local/bin.
#
#   scripts/install_tools.sh              install what is missing or out of date
#   scripts/install_tools.sh install age  install only the named tools
#   scripts/install_tools.sh check        report versions, install nothing
#
# Versions are pinned and checksums are recorded here rather than fetched
# alongside the download: a checksum taken from the same response it is meant
# to verify proves only that the transfer was not corrupted. Pinning them in
# the repository also makes a re-uploaded release asset fail loudly.
#
# Only static single-file binaries land here; nothing is installed system-wide
# and nothing needs a package manager.
set -euo pipefail

readonly INSTALL_DIR="${INSTALL_DIR:-${HOME}/.local/bin}"

# name|version|url|sha256|archive member ("-" for a bare binary)|installed binary sha256
#
# The last field is only for a tool that cannot report its own version; for
# everything else it is empty and the version string is what gets checked.
TOOLS=(
    "kind|v0.32.0|https://github.com/kubernetes-sigs/kind/releases/download/v0.32.0/kind-linux-amd64|50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54|-|"
    "kubectl|v1.36.4|https://dl.k8s.io/release/v1.36.4/bin/linux/amd64/kubectl|8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a|-|"
    "kustomize|v5.8.1|https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.8.1/kustomize_v5.8.1_linux_amd64.tar.gz|029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d|kustomize|"
    "sops|v3.13.3|https://github.com/getsops/sops/releases/download/v3.13.3/sops-v3.13.3.linux.amd64|e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b|-|"
    # age publishes a sigsum proof rather than a checksum file; this digest was
    # taken from the asset once and pins it from here on.
    "age|v1.3.1|https://github.com/FiloSottile/age/releases/download/v1.3.1/age-v1.3.1-linux-amd64.tar.gz|bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377|age/age|"
    "age-keygen|v1.3.1|https://github.com/FiloSottile/age/releases/download/v1.3.1/age-v1.3.1-linux-amd64.tar.gz|bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377|age/age-keygen|"
    # The kustomize plugin that decrypts SOPS files. The cluster gets it from
    # the KSOPS image; this copy is what makes `kustomize build` of
    # deploy/k8s/base/secrets work on the developer's machine.
    "ksops|v4.5.1|https://github.com/viaduct-ai/kustomize-sops/releases/download/v4.5.1/ksops_4.5.1_Linux_x86_64.tar.gz|9641e03a301bf2fc4b98d0e837a0351e1ca443ecd7a68b25e6af2149fbb2ef30|ksops|0fe5ab3545347ae261508f5e662aa767c1d6a11f21c0010a520deb0c2d45f73f"
    # The delivery pipeline is GitHub Actions and ghcr.io (ADR-0009), and the
    # branching model is pull requests into main (D60).
    "gh|v2.98.0|https://github.com/cli/cli/releases/download/v2.98.0/gh_2.98.0_linux_amd64.tar.gz|3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de|gh_2.98.0_linux_amd64/bin/gh|"
)

report() {
    local outcome="$1" message="$2"
    case "${outcome}" in
        ok)      printf '  \033[32mok\033[0m       %s\n' "${message}" ;;
        install) printf '  \033[36minstall\033[0m  %s\n' "${message}" ;;
        *)       printf '  \033[31mFAIL\033[0m     %s\n' "${message}" ;;
    esac
}

# Every tool reports its own version differently, and two of them treat
# --version as an unknown flag. One grep for the pinned string is enough to
# tell "already the right build" from "something else".
version_output() {
    case "$1" in
        kubectl)   kubectl version --client 2>&1 ;;
        kustomize) kustomize version 2>&1 ;;
        *)         "$1" --version 2>&1 ;;
    esac
}

# ksops is a kustomize exec plugin: it reads a manifest on stdin and has no
# version flag at all, so the installed file is compared by digest instead.
installed_version_matches() {
    local name="$1" version="$2" binary_sha256="${3:-}"
    local path
    path="$(command -v "${name}" 2>/dev/null)" || return 1

    if [[ -n "${binary_sha256}" ]]; then
        [[ "$(sha256sum "${path}" | cut -d" " -f1)" == "${binary_sha256}" ]]
        return
    fi
    version_output "${name}" | grep -qF "${version#v}"
}

install_tool() {
    local name="$1" version="$2" url="$3" sha256="$4" member="$5"
    local workdir
    workdir="$(mktemp --directory)"
    # shellcheck disable=SC2064  # workdir is expanded now on purpose.
    trap "rm -rf '${workdir}'" RETURN

    curl --silent --show-error --location --fail --max-time 300 \
        --output "${workdir}/download" "${url}" \
        || { report fail "${name}: download failed"; return 1; }

    local actual
    actual="$(sha256sum "${workdir}/download" | cut -d' ' -f1)"
    if [[ "${actual}" != "${sha256}" ]]; then
        report fail "${name}: sha256 ${actual}, pinned ${sha256}"
        return 1
    fi

    if [[ "${member}" == "-" ]]; then
        install -m 0755 "${workdir}/download" "${INSTALL_DIR}/${name}"
    else
        tar --extract --file "${workdir}/download" --directory "${workdir}" "${member}"
        install -m 0755 "${workdir}/${member}" "${INSTALL_DIR}/${name}"
    fi
    report install "${name} ${version}"
}

# No names given means every tool; naming them keeps a job that needs one
# binary from downloading seven.
selected() {
    local wanted="$1"
    shift
    # Nothing left after the name being tested means no filter was given.
    if (( $# == 0 )); then
        return 0
    fi
    for name in "$@"; do
        [[ "${name}" == "${wanted}" ]] && return 0
    done
    return 1
}

action="${1:-install}"
shift || true

case "${action}" in
    install)
        mkdir -p "${INSTALL_DIR}"
        echo "Toolchain in ${INSTALL_DIR}:"
        for spec in "${TOOLS[@]}"; do
            IFS='|' read -r name version url sha256 member binary_sha256 <<<"${spec}"
            selected "${name}" "$@" || continue
            if installed_version_matches "${name}" "${version}" "${binary_sha256}"; then
                report ok "${name} ${version}"
            else
                install_tool "${name}" "${version}" "${url}" "${sha256}" "${member}"
            fi
        done

        echo
        case ":${PATH}:" in
            *":${INSTALL_DIR}:"*) ;;
            *) echo "note: ${INSTALL_DIR} is not on PATH; add it to use these tools." ;;
        esac
        ;;

    check)
        missing=0
        echo "Toolchain:"
        for spec in "${TOOLS[@]}"; do
            IFS='|' read -r name version _ _ _ binary_sha256 <<<"${spec}"
            selected "${name}" "$@" || continue
            if installed_version_matches "${name}" "${version}" "${binary_sha256}"; then
                report ok "${name} ${version}"
            elif command -v "${name}" >/dev/null 2>&1; then
                report fail "${name}: not the pinned ${version}"
                missing=$((missing + 1))
            else
                report fail "${name}: missing"
                missing=$((missing + 1))
            fi
        done
        if (( missing > 0 )); then
            echo
            echo "toolchain: ${missing} tool(s) missing; run scripts/install_tools.sh"
            exit 1
        fi
        ;;

    *)
        echo "usage: $(basename "$0") [install|check] [tool...]" >&2
        exit 2
        ;;
esac
