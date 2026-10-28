# syntax=docker/dockerfile:1
# One image for everything: the spack base (ESMF 8.9.1, OpenMPI, netCDF, yaml-cpp), the Python tooling,
# a shared Kokkos, ESMF-enabled CECE and CATChem built from the submodules, and the prototype sources.
# Stages form a DAG: python, kokkos, cece and catchem all derive from base and only final depends on all
# of them, so a change to one piece rebuilds that piece alone. CI and tools/ci.sh bind-mount the live
# checkout at /repo and run there; /opt/protos/src is a convenience copy.
#
#   BUILDX_BUILDER=cloud-bkrlps-cece-builder docker compose build
#   docker compose run --rm ci sh -c 'cmake -S . -B build && cmake --build build && ctest --test-dir build -L ci'
ARG BASE_IMAGE=noaaepic/ufschem-spack-base-ubuntu-gcc-13-dev:latest
ARG UV_VERSION=0.10.7
ARG KOKKOS_VERSION=5.1.1

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS base
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The spack environment view. The base image activates it only in interactive shells (~/.bashrc);
# every CI command runs non-interactively, so the paths are set here, explicitly.
ENV SPACK_VIEW=/opt/ufschem/spack-stack/spack/var/spack/environments/ufschem/.spack-env/view
ENV ESMFMKFILE=${SPACK_VIEW}/lib/esmf.mk \
    PATH=${SPACK_VIEW}/bin:${PATH} \
    CMAKE_PREFIX_PATH=${SPACK_VIEW} \
    PKG_CONFIG_PATH=${SPACK_VIEW}/lib/pkgconfig \
    LD_LIBRARY_PATH=${SPACK_VIEW}/lib

# MPI as root, oversubscribed: for every mpirun in the container (CECE's own tests, ad-hoc runs) ...
ENV OMPI_ALLOW_RUN_AS_ROOT=1 \
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
    OMPI_MCA_rmaps_base_oversubscribe=1
# ... and for CTest: the top-level CMakeLists.txt copies this into MPIEXEC_PREFLAGS when that is empty,
# so `cmake -S . -B build` needs no flags inside the container and stays clean on a host without it.
ENV PROTOS_MPIEXEC_PREFLAGS="--allow-run-as-root;--oversubscribe"

# uv, with the venv outside any bind mount. UV_PYTHON pins the image's interpreter (3.12.3), so no
# managed-interpreter download; PRE_COMMIT_HOME keeps the isolated hook environments in the image.
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PRE_COMMIT_HOME=/opt/pre-commit \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3

# ---------------------------------------------------------------------------------------------------
# Python tooling: keyed on the three files only. The throwaway `git init` satisfies pre-commit's
# in-a-repo requirement while the hook environments (pre-commit-hooks, conventional-pre-commit) bake.
FROM base AS python
WORKDIR /opt/protos-tooling
COPY pyproject.toml uv.lock .pre-commit-config.yaml ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
RUN --mount=type=cache,target=/root/.cache/uv \
    git init -q . \
    && uv run --no-sync pre-commit install-hooks \
    && rm -rf .git

# ---------------------------------------------------------------------------------------------------
# One Kokkos for both models (the base image ships none). CECE would otherwise FetchContent its own on
# every rebuild, and CATChem, once CATCHEM_ENABLE_KOKKOS is turned on, must link the same Kokkos as CECE.
FROM base AS kokkos
ARG KOKKOS_VERSION
RUN curl -fsSL "https://github.com/kokkos/kokkos/releases/download/${KOKKOS_VERSION}/kokkos-${KOKKOS_VERSION}.tar.gz" \
      | tar -xz -C /tmp \
    && cmake -S "/tmp/kokkos-${KOKKOS_VERSION}" -B /tmp/kokkos-build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/kokkos \
      -DCMAKE_CXX_STANDARD=20 \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DKokkos_ENABLE_SERIAL=ON \
      -DKokkos_ENABLE_OPENMP=ON \
    && cmake --build /tmp/kokkos-build -j"$(nproc)" \
    && cmake --install /tmp/kokkos-build \
    && rm -rf /tmp/kokkos-*

# ---------------------------------------------------------------------------------------------------
# Kokkos Tools (the space-time-stack and kernel-timer connectors) for the `profile` label: built only with
# --build-arg KOKKOS_TOOLS=on (tools/ci.sh test profile passes it), otherwise /opt/kokkos-tools/lib is empty
# and the image is unchanged. Nothing links them: Kokkos loads a tool at run time from KOKKOS_TOOLS_LIBS.
FROM base AS kokkos-tools
ARG KOKKOS_TOOLS=off
ARG KOKKOS_TOOLS_VERSION=5.2.0
COPY --from=kokkos /opt/kokkos /opt/kokkos
RUN mkdir -p /opt/kokkos-tools/lib \
    && if [ "${KOKKOS_TOOLS}" = on ]; then \
         curl -fsSL "https://github.com/kokkos/kokkos-tools/archive/refs/tags/${KOKKOS_TOOLS_VERSION}.tar.gz" \
           | tar -xz -C /tmp \
         && cmake -S "/tmp/kokkos-tools-${KOKKOS_TOOLS_VERSION}" -B /tmp/kokkos-tools-build -G Ninja \
           -DCMAKE_BUILD_TYPE=Release -DKokkos_ROOT=/opt/kokkos \
         && cmake --build /tmp/kokkos-tools-build -j"$(nproc)" --target kp_space_time_stack kp_kernel_timer \
         && find /tmp/kokkos-tools-build -name 'libkp_*.so' -exec cp {} /opt/kokkos-tools/lib/ \; \
         && rm -rf /tmp/kokkos-tools-*; \
       fi

# ---------------------------------------------------------------------------------------------------
# CECE (ENABLE_ESMF=ON): cece_nuopc_cap (static) and cece_nuopc_app. The test suite stays off (GTest and
# RapidCheck are not in the base image; the submodule's own CI runs it). The build tree is kept: CECE
# exports no CMake package, so the prototype driver links the cap and its .mod files from the tree.
# configure.log keeps the "CECE dependency" summary (Kokkos, yaml-cpp, ESMF, netCDF provenance).
# netCDF: spack's netcdf-c (4.9.3, the one ESMF links) installs no CMake package file, and apt's 4.9.2
# under /usr does, so CECE's find_package(netCDF CONFIG) would pick apt's. The config lookup is disabled
# and CECE's fallback uses the netCDF root FindESMF derives from esmf.mk; HELM's AXIS uses pkg-config,
# which PKG_CONFIG_PATH (base stage) points at the view.
#
# Incremental: the build tree lives in a BuildKit cache mount at its final path, so a source edit recompiles
# the changed files only (COPY keeps the context's mtimes; Ninja compares them). The tree is copied out of
# the mount and moved back in a second step, because a mount's contents never reach the image; the paths,
# rpaths and .mod files stay /opt/cece/build. A toolchain stamp (compilers, esmf.mk, Kokkos) wipes the
# cached tree when the base image or Kokkos changes. sharing=locked serializes builds of the same tree.
# Hosted CI starts with an empty mount and builds from scratch, as before.
FROM base AS cece
COPY --from=kokkos /opt/kokkos /opt/kokkos
COPY extern/CECE /opt/cece/src
RUN --mount=type=cache,id=protos-cece-build,target=/opt/cece/build,sharing=locked \
    stamp="$(g++ --version | head -1; gfortran --version | head -1; md5sum "${ESMFMKFILE}"; \
             md5sum /opt/kokkos/lib/cmake/Kokkos/KokkosConfigVersion.cmake)" \
    && if [ "$(cat /opt/cece/build/.toolchain 2>/dev/null)" != "${stamp}" ]; then \
         echo "cece: new toolchain, clean build tree"; find /opt/cece/build -mindepth 1 -delete; \
       fi \
    && echo "${stamp}" > /opt/cece/build/.toolchain \
    && cmake -S /opt/cece/src -B /opt/cece/build -G Ninja \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DENABLE_ESMF=ON \
      -DCECE_BUILD_TESTING=OFF \
      -DCECE_MPIEXEC_CONTAINER_FLAGS=ON \
      -DKokkos_ROOT=/opt/kokkos \
      -DCMAKE_DISABLE_FIND_PACKAGE_netCDF=ON \
      -DCMAKE_INSTALL_PREFIX=/opt/cece/install \
      2>&1 | tee /opt/cece/configure.log \
    && cmake --build /opt/cece/build -j"$(nproc)" \
    && cmake --install /opt/cece/build \
    && cp -a /opt/cece/build /opt/cece/build.out
RUN rm -rf /opt/cece/build && mv /opt/cece/build.out /opt/cece/build

# ---------------------------------------------------------------------------------------------------
# CATChem (CATCHEM_BUILD_NUOPC=ON): the self-contained cc_nuopc archive plus NUOPC_CATChem_cap. Kokkos
# and MUSICA stay off (the NUOPC cap does not need them; /opt/kokkos is there when a later task flips
# Kokkos on). In NUOPC builds CATChem uses its vendored, symbol-isolated yaml-cpp by design.
# Makefiles, not Ninja: NUOPC_CATChem_cap and cc_nuopc compile the same sources and both emit their
# .mod files into build/include, which Ninja rejects as duplicate outputs (make tolerates it).
# Incremental the same way as CECE (cache mount at /opt/catchem/build, toolchain stamp, copy out and move
# back); make compares mtimes, and CMake's Fortran dependency scanning recompiles the modules that use a
# changed one.
FROM base AS catchem
COPY extern/CATChem /opt/catchem/src
RUN --mount=type=cache,id=protos-catchem-build,target=/opt/catchem/build,sharing=locked \
    stamp="$(g++ --version | head -1; gfortran --version | head -1; md5sum "${ESMFMKFILE}")" \
    && if [ "$(cat /opt/catchem/build/.toolchain 2>/dev/null)" != "${stamp}" ]; then \
         echo "catchem: new toolchain, clean build tree"; find /opt/catchem/build -mindepth 1 -delete; \
       fi \
    && echo "${stamp}" > /opt/catchem/build/.toolchain \
    && cmake -S /opt/catchem/src -B /opt/catchem/build -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DCATCHEM_BUILD_NUOPC=ON \
      -DCATCHEM_BUILD_TESTING=OFF \
      -DCATCHEM_ENABLE_KOKKOS=OFF \
      -DCATCHEM_BUILD_MUSICA=OFF \
      -DCMAKE_INSTALL_PREFIX=/opt/catchem/install \
      2>&1 | tee /opt/catchem/configure.log \
    && cmake --build /opt/catchem/build -j"$(nproc)" \
    && cmake --install /opt/catchem/build \
    && cp -a /opt/catchem/build /opt/catchem/build.out
RUN rm -rf /opt/catchem/build && mv /opt/catchem/build.out /opt/catchem/build

# ---------------------------------------------------------------------------------------------------
FROM base AS final
COPY --from=python /opt/venv /opt/venv
COPY --from=python /opt/pre-commit /opt/pre-commit
COPY --from=kokkos /opt/kokkos /opt/kokkos
COPY --from=kokkos-tools /opt/kokkos-tools /opt/kokkos-tools
COPY --from=cece /opt/cece /opt/cece
COPY --from=catchem /opt/catchem /opt/catchem
ENV KOKKOS_ROOT=/opt/kokkos \
    CECE_ROOT=/opt/cece \
    CATCHEM_ROOT=/opt/catchem
# One OpenMP thread per process unless something says otherwise, unbound (with several ranks per node and no
# rank binding, OpenMP binding would put every rank's thread on the same core). A backstop only: CECE's cap
# takes Kokkos's thread count from the PEs ESMF gives its PET, whatever this says. Set here, not in base:
# an edit to base invalidates every stage.
ENV OMP_NUM_THREADS=1 \
    OMP_PROC_BIND=false
# The prototype sources, last: the only layer a prototype edit invalidates.
COPY . /opt/protos/src
WORKDIR /repo
CMD ["/bin/bash"]

# ---------------------------------------------------------------------------------------------------
# Tests as a build step, for a native amd64 run on the Docker Build Cloud builder (an Apple-silicon host
# cannot run the image's HDF5 under Rosetta: no x87). Not an image anyone uses: `tools/ci.sh test [label]`
# builds `test-out` with --no-cache-filter test so the RUN always executes, and the ctest output is the
# build log.
#   CTEST_LABEL  the CTest label to run (ci, local, c96, profile)
#   CTEST_ARGS   extra ctest arguments (e.g. "-R proto_met_catchem$")
# The fix files land in a build cache mount, so the EMC download happens once per builder node; the
# inventories and manifests are regenerated by the mockdata fixture every run.
# A red ctest does not fail this stage: its exit status goes to /out/ctest.status next to the run
# outputs (PET logs, totals, CATChem diagnostics, the generated configs, LastTest.log), which `test-out`
# exports and tools/ci.sh copies to build/ci-out/<label>/ before exiting with that status. Anything that
# builds `test` directly must read /out/ctest.status; a configure or build error still fails the stage.
# The reporting and the copy are sequenced with `;` and `|| true`: the stage's shell runs with pipefail, and a
# grep without a match must not skip the export.
FROM final AS test
ARG CTEST_LABEL=ci
ARG CTEST_ARGS=""
WORKDIR /repo
COPY . /repo
RUN --mount=type=cache,target=/repo/data,id=protos-data \
    uv sync --frozen \
    && cmake -S . -B build \
    && cmake --build build -j"$(nproc)" \
    && mkdir -p /out \
    && status=0 \
    && { ctest --test-dir build --output-on-failure -L "${CTEST_LABEL}" ${CTEST_ARGS} || status=$?; } \
    && echo "$status" > /out/ctest.status \
    && runs="build/protos/met_catchem/run_*/ build/protos/cece_met/run_*/ build/protos/cece_met_catchem/run_*/" \
    && if [ "$status" -eq 0 ]; then \
         echo "ctest -L ${CTEST_LABEL}: passed"; \
         for d in $runs; do \
           [ -f "$d/PET0.ESMF_LogFile" ] || [ -f "$d/PET00.ESMF_LogFile" ] || continue; \
           echo "== $d: $(ls "$d"/catchem_diag 2>/dev/null | wc -l) diagnostic files"; \
           grep -h "driver: grid \|reference-shared\|global cell area\|MET: step 120\|Parent grid: tileCount\|host-block destination\|Realized export field\|hosted import\|MET: totals 2021-03-23T05:48\|MET: tracer so2\|ShareStatusField=\|read from NUOPC imports\|RealizeAccepted removed" "$d"/PET0*.ESMF_LogFile | cut -c38- | sort -u || true; \
         done; \
         grep -h "check_conservation:\|completed 120 steps\|memory per PET" build/Testing/Temporary/LastTest.log 2>/dev/null | sort -u || true; \
         echo "== esmApp yaml-cpp symbols: $(nm -C build/protos/met_catchem/esmApp | grep -c 'catchem_yaml::') catchem_yaml::, $(nm -C build/protos/met_catchem/esmApp | grep -c ' YAML::') YAML:: defined; shared: $(ldd build/protos/met_catchem/esmApp | grep -c yaml-cpp) libyaml-cpp"; \
       else \
         echo "ctest -L ${CTEST_LABEL}: FAILED (status $status); ESMF log errors follow"; \
         for d in $runs; do grep -h "ERROR" "$d"/PET0*.ESMF_LogFile 2>/dev/null || true; done | tail -40; \
       fi; \
    for d in $runs; do if [ -d "$d" ]; then mkdir -p "/out/$d" && cp -r "$d". "/out/$d"; fi; done; \
    mkdir -p /out/build/Testing && cp -r build/gen /out/build/ 2>/dev/null; \
    cp build/Testing/Temporary/LastTest.log /out/build/Testing/ 2>/dev/null; \
    test -f /out/ctest.status

# The test stage's outputs, for `docker buildx build --target test-out --output type=local,dest=...`.
FROM scratch AS test-out
COPY --from=test /out /
