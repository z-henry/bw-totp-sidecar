#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="bw-totp-sidecar"
VERSION="$(tr -d '[:space:]' < "${ROOT_DIR}/VERSION")"
BASE_IMAGE="${BASE_IMAGE:-python:3.12-slim}"

if [[ -z "${VERSION}" ]]; then
  echo "VERSION file is empty" >&2
  exit 1
fi

docker build \
  --build-arg PYTHON_BASE_IMAGE="${BASE_IMAGE}" \
  --build-arg APP_VERSION="${VERSION}" \
  -t "${IMAGE_NAME}:${VERSION}" \
  -t "${IMAGE_NAME}:latest" \
  "${ROOT_DIR}"
