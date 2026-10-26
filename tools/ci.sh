#!/usr/bin/env bash
# Run a CI job locally, inside the same image CI uses, with the checkout bind-mounted at /repo:
#   tools/ci.sh build        build the image with the Docker Build Cloud builder (docker compose build)
#   tools/ci.sh pre-commit   every hook on every file
#   tools/ci.sh pytest       the Python tooling suite with coverage
#   tools/ci.sh ctest        configure, build and run the `ci`-labelled CTest tests
# The three command strings are the ones .github/workflows/ci.yaml runs; keep them identical.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export BUILDX_BUILDER="${BUILDX_BUILDER:-cloud-bkrlps-cece-builder}"

readonly PRE_COMMIT_CMD='git config --global --add safe.directory /repo && uv sync --frozen && uv run pre-commit run --all-files'
readonly PYTEST_CMD='git config --global --add safe.directory /repo && uv sync --frozen && uv run pytest tools/tests --cov --cov-report=term --cov-report=html:coverage-html'
readonly CTEST_CMD='cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure -L ci'

run_in_image() {
  docker compose run --rm ci sh -c "$1"
}

case "${1:-}" in
  build) docker compose build ;;
  pre-commit) run_in_image "$PRE_COMMIT_CMD" ;;
  pytest) run_in_image "$PYTEST_CMD" ;;
  ctest) run_in_image "$CTEST_CMD" ;;
  *)
    echo "usage: $0 {build|pre-commit|pytest|ctest}" >&2
    exit 2
    ;;
esac
