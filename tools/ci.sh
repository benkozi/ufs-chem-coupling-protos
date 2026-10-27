#!/usr/bin/env bash
# Run a CI job locally, inside the same image CI uses, with the checkout bind-mounted at /repo:
#   tools/ci.sh build        build the image with the Docker Build Cloud builder (docker compose build)
#   tools/ci.sh pre-commit   every hook on every file
#   tools/ci.sh pytest       the Python tooling suite with coverage
#   tools/ci.sh ctest        configure, build and run the `ci`-labelled CTest tests
#   tools/ci.sh test [label] [ctest args]
#                            the same on the cloud builder's native amd64 node, as the Dockerfile's `test`
#                            stage (needed on Apple silicon, where Rosetta cannot run the image's HDF5)
# The three command strings are the ones .github/workflows/ci.yaml runs; keep them identical.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export BUILDX_BUILDER="${BUILDX_BUILDER:-cloud-bkrlps-cece-builder}"

readonly PRE_COMMIT_CMD='git config --global --add safe.directory /repo && uv sync --frozen && uv run pre-commit run --all-files'
readonly PYTEST_CMD='git config --global --add safe.directory /repo && uv sync --frozen && uv run pytest tools/tests --cov --cov-report=term --cov-report=html:coverage-html'
readonly CTEST_CMD='git config --global --add safe.directory /repo && uv sync --frozen && cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure -L ci'

run_in_image() {
  docker compose run --rm ci sh -c "$1"
}

case "${1:-}" in
  build) docker compose build ;;
  pre-commit) run_in_image "$PRE_COMMIT_CMD" ;;
  pytest) run_in_image "$PYTEST_CMD" ;;
  ctest) run_in_image "$CTEST_CMD" ;;
  test)
    label="${2:-ci}"
    shift $(( $# > 1 ? 2 : $# ))
    docker buildx build --builder "$BUILDX_BUILDER" --platform linux/amd64 --target test \
      --build-arg "CTEST_LABEL=${label}" --build-arg "CTEST_ARGS=$*" \
      --no-cache-filter test --progress=plain . 2>&1 | grep -v -E '^#[0-9]+ (DONE|CACHED|\[internal\]|transferring|sha256:|extracting|resolve )'
    ;;
  *)
    echo "usage: $0 {build|pre-commit|pytest|ctest|test [label] [ctest args]}" >&2
    exit 2
    ;;
esac
