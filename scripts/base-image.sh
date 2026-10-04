#!/usr/bin/env bash
#
# base-image.sh - Name and locate the shared dependency image (docker/base.Dockerfile).
#
# The image is tagged by a hash of everything that decides its contents (uv.lock,
# every pyproject.toml and the base Dockerfile), so a tag can never hold stale
# dependencies: change the lock and you get a new tag.
#
#   scripts/base-image.sh tag             -> lock-<hash>
#   scripts/base-image.sh ref             -> ghcr.io/xsavikx/sre-agent-base:lock-<hash>
#   scripts/base-image.sh published       -> exit 0 if that ref is publicly pullable from GHCR
#
# Forks can publish their own copy and point SRE_BASE_REPO at it.

set -euo pipefail

SRE_BASE_REPO="${SRE_BASE_REPO:-ghcr.io/xsavikx/sre-agent-base}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

hash_inputs() {
    (
        cd "$ROOT"
        cat uv.lock pyproject.toml app/pyproject.toml agent/pyproject.toml sre_agent/pyproject.toml \
            inventory_agent/pyproject.toml sre_common/pyproject.toml docker/base.Dockerfile
    )
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi
}

tag() {
    echo "lock-$(hash_inputs | sha256 | cut -c1-12)"
}

# Anonymous pull check against the GHCR registry API: works for public packages
# without docker or credentials, and fails closed for private or missing ones.
published() {
    local repo="${SRE_BASE_REPO#ghcr.io/}" token
    token=$(curl -fsS "https://ghcr.io/token?scope=repository:${repo}:pull" 2>/dev/null \
        | sed -n 's/.*"token":"\([^"]*\)".*/\1/p') || return 1
    [ -n "$token" ] || return 1
    curl -fsS -o /dev/null -I \
        -H "Authorization: Bearer ${token}" \
        -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json" \
        "https://ghcr.io/v2/${repo}/manifests/$(tag)"
}

case "${1:-}" in
    tag) tag ;;
    ref) echo "${SRE_BASE_REPO}:$(tag)" ;;
    published) published ;;
    *)
        echo "usage: $0 {tag|ref|published}" >&2
        exit 2
        ;;
esac
