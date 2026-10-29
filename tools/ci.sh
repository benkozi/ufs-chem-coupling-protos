#!/usr/bin/env bash
# Run a CI job locally, inside the same image CI uses, with the checkout bind-mounted at /repo:
#   tools/ci.sh build        build the image with the Docker Build Cloud builder (docker compose build)
#   tools/ci.sh pre-commit   every hook on every file
#   tools/ci.sh pytest       the Python tooling suite with coverage
#   tools/ci.sh ctest        configure, build and run the `ci`-labelled CTest tests
#   tools/ci.sh test [label] [ctest args]
#                            the same on the cloud builder's native amd64 node, as the Dockerfile's `test`
#                            stage (needed on Apple silicon, where Rosetta cannot run the image's HDF5);
#                            the run outputs land in build/ci-out/<label>/, the exit status is ctest's;
#                            the `profile` label builds the image with Kokkos Tools (KOKKOS_TOOLS=on)
# The three command strings are the ones .github/workflows/ci.yaml runs; keep them identical.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export BUILDX_BUILDER="${BUILDX_BUILDER:-cloud-bkrlps-cece-builder}"

# One cloud build at a time (template.md): overlapping builds share the builder's cores, each one's time counts
# against the build minutes, and two mock-data fixtures race on the shared data cache mount. A second
# `build` or `test` waits for the first; the lock is a directory (macOS has no flock) removed on exit. A lock
# left by a killed run: rmdir build/.ci-build.lock.
cloud_build_lock() {
  mkdir -p build
  until mkdir build/.ci-build.lock 2>/dev/null; do
    echo "ci.sh: another cloud build is running (build/.ci-build.lock); waiting" >&2
    sleep 30
  done
  trap 'rmdir build/.ci-build.lock 2>/dev/null || true' EXIT
}

readonly PRE_COMMIT_CMD='git config --global --add safe.directory /repo && uv sync --frozen && uv run pre-commit run --all-files'
readonly PYTEST_CMD='git config --global --add safe.directory /repo && uv sync --frozen && uv run pytest tools/tests --cov --cov-report=term --cov-report=html:coverage-html'
readonly CTEST_CMD='git config --global --add safe.directory /repo && uv sync --frozen && cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure -L ci'

run_in_image() {
  docker compose run --rm ci sh -c "$1"
}

case "${1:-}" in
  build)
    cloud_build_lock
    docker compose build
    ;;
  pre-commit) run_in_image "$PRE_COMMIT_CMD" ;;
  pytest) run_in_image "$PYTEST_CMD" ;;
  ctest) run_in_image "$CTEST_CMD" ;;
  test)
    label="${2:-ci}"
    shift $(( $# > 1 ? 2 : $# ))
    # The test stage records ctest's status instead of failing; test-out exports it with the run outputs
    # (PET logs, totals, CATChem diagnostics, the generated configs, LastTest.log) to build/ci-out/<label>/.
    cloud_build_lock
    out="build/ci-out/${label}"
    rm -rf "$out"
    mkdir -p "$out"
    tools="off"
    [ "$label" = profile ] && tools="on"
    docker buildx build --builder "$BUILDX_BUILDER" --platform linux/amd64 --target test-out \
      --build-arg "CTEST_LABEL=${label}" --build-arg "CTEST_ARGS=$*" --build-arg "KOKKOS_TOOLS=${tools}" \
      --no-cache-filter test --progress=plain --output "type=local,dest=${out}" . 2>&1 \
      | grep -v -E '^#[0-9]+ (DONE|CACHED|\[internal\]|transferring|sha256:|extracting|resolve )' || true
    if [ ! -f "$out/ctest.status" ]; then
      echo "ci.sh test: no ctest status in $out (the image build failed before ctest ran)" >&2
      exit 1
    fi
    status="$(cat "$out/ctest.status")"
    echo "ci.sh test: ctest -L ${label} exit status ${status}; run outputs in ${out}/"
    exit "$status"
    ;;
  *)
    echo "usage: $0 {build|pre-commit|pytest|ctest|test [label] [ctest args]}" >&2
    exit 2
    ;;
esac
